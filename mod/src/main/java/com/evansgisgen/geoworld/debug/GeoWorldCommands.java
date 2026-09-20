package com.evansgisgen.geoworld.debug;

import com.evansgisgen.geoworld.GeoWorldMod;
import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoPoint;
import com.evansgisgen.geoworld.geo.GeoTransform;
import com.evansgisgen.geoworld.parcel.ParcelIndex;
import com.evansgisgen.geoworld.studio.StudioService;
import com.evansgisgen.geoworld.worldgen.GeoChunkGenerator;
import com.mojang.brigadier.arguments.DoubleArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.suggestion.Suggestions;
import com.mojang.brigadier.suggestion.SuggestionsBuilder;
import java.util.concurrent.CompletableFuture;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.Vec3;
import net.neoforged.neoforge.event.RegisterCommandsEvent;

/**
 * Debug and studio commands.
 *
 * <ul>
 *   <li>{@code /geoworld info} — shows the active transform (origin, scale, datum)</li>
 *   <li>{@code /geoworld geo} — shows the geographic coordinates of the source position</li>
 *   <li>{@code /geoworld geo <east> <north>} — shows the block coordinates for a geo point</li>
 *   <li>{@code /geoworld studio <x,z|x0,z0 x1,z1|address>} — stage the
 *       matching parcel (or every parcel in the block-coord rect, for a
 *       whole city block) in the studio dimension and teleport there
 *       (op only)</li>
 *   <li>{@code /geoworld studio bare <query>} — stage without the procedural
 *       shell or saved landmark</li>
 *   <li>{@code /geoworld studio save} — capture the edited lot as a
 *       REPLACE_LOT landmark and return</li>
 *   <li>{@code /geoworld studio exit} — leave the studio without saving</li>
 * </ul>
 */
public final class GeoWorldCommands {
    private GeoWorldCommands() {}

    public static void register(RegisterCommandsEvent event) {
        var north = Commands.argument("north", DoubleArgumentType.doubleArg())
                .executes(ctx -> geoToBlock(ctx.getSource(),
                        DoubleArgumentType.getDouble(ctx, "east"),
                        DoubleArgumentType.getDouble(ctx, "north")));
        var east = Commands.argument("east", DoubleArgumentType.doubleArg()).then(north);
        var geo = Commands.literal("geo").executes(ctx -> geoAtSource(ctx.getSource())).then(east);
        var info = Commands.literal("info").executes(ctx -> info(ctx.getSource()));

        var query = Commands.argument("query", StringArgumentType.greedyString())
                .suggests(GeoWorldCommands::suggestAddresses)
                .executes(ctx -> studioEnter(ctx.getSource(),
                        StringArgumentType.getString(ctx, "query"), false));
        var bareQuery = Commands.argument("query", StringArgumentType.greedyString())
                .suggests(GeoWorldCommands::suggestAddresses)
                .executes(ctx -> studioEnter(ctx.getSource(),
                        StringArgumentType.getString(ctx, "query"), true));
        var studio = Commands.literal("studio")
                .requires(src -> src.hasPermission(2))
                .then(Commands.literal("save")
                        .executes(ctx -> studioSave(ctx.getSource())))
                .then(Commands.literal("exit")
                        .executes(ctx -> studioExit(ctx.getSource())))
                .then(Commands.literal("bare").then(bareQuery))
                .then(query);

        event.getDispatcher().register(
                Commands.literal("geoworld").then(info).then(geo).then(studio));
    }

    // --- studio ---------------------------------------------------------------

    /** Gazetteer prefix matches first, remote geocoder merged when enabled. */
    private static CompletableFuture<Suggestions> suggestAddresses(
            com.mojang.brigadier.context.CommandContext<CommandSourceStack> ctx,
            SuggestionsBuilder builder) {
        String remaining = builder.getRemaining();
        for (String s : GeoWorldMod.parcels().suggest(remaining, 8)) {
            builder.suggest(s);
        }
        var geocoder = GeoWorldMod.geocoder();
        if (!geocoder.canAutocomplete()) {
            return builder.buildFuture();
        }
        return geocoder.suggest(remaining, 6).thenApply(remote -> {
            for (String s : remote) {
                builder.suggest(s);
            }
            return builder.build();
        }).exceptionally(e -> builder.build());
    }

    private static int studioEnter(CommandSourceStack source) {
        return studioEnter(source, "", false);
    }

    private static int studioEnter(CommandSourceStack source, String query,
            boolean bare) {
        ServerPlayer player = player(source);
        if (player == null) {
            return 0;
        }
        GeoDataset dataset = GeoWorldMod.dataset();
        ParcelIndex parcels = GeoWorldMod.parcels();
        if (parcels.isEmpty()) {
            fail(source, "no parcels.json in the dataset — run "
                    + "fetch-parcels + compile-parcels first");
            return 0;
        }
        StudioService.resolveQuery(dataset, parcels, query).ifPresentOrElse(
                selection -> enter(source, player, selection, bare),
                () -> geocoderFallback(source, player, query, bare));
        return 1;
    }

    private static void enter(CommandSourceStack source, ServerPlayer player,
            com.evansgisgen.geoworld.parcel.ParcelSelection selection,
            boolean bare) {
        var landmarks = landmarks(source);
        StudioService.Result r = StudioService.enter(player,
                GeoWorldMod.dataset(), GeoWorldMod.parcels(), landmarks,
                selection, bare);
        send(source, r);
    }

    /** Gazetteer missed — try the configured geocoder, then report. */
    private static void geocoderFallback(CommandSourceStack source,
            ServerPlayer player, String query, boolean bare) {
        var geocoder = GeoWorldMod.geocoder();
        if (!geocoder.enabled()) {
            fail(source, "no parcel matches '" + query
                    + "' — use x,z block coordinates (no geocoder configured)");
            return;
        }
        source.sendSuccess(() -> Component.literal("looking up '" + query + "'…"),
                false);
        geocoder.lookup(query).thenAccept(opt ->
                source.getServer().execute(() -> opt.ifPresentOrElse(en -> {
                    GeoWorldMod.parcels().at(en[0], en[1]).ifPresentOrElse(
                            parcel -> enter(source, player,
                                    com.evansgisgen.geoworld.parcel
                                            .ParcelSelection.single(parcel),
                                    bare),
                            () -> fail(source, "'" + query + "' geocodes outside "
                                    + "the dataset's parcels"));
                }, () -> fail(source, "geocoder found no match for '" + query + "'"))));
    }

    private static int studioSave(CommandSourceStack source) {
        ServerPlayer player = player(source);
        if (player == null) {
            return 0;
        }
        var generator = source.getServer().overworld().getChunkSource()
                .getGenerator();
        if (!(generator instanceof GeoChunkGenerator geoGen)) {
            fail(source, "overworld is not a geoworld world");
            return 0;
        }
        send(source, StudioService.save(player, GeoWorldMod.dataset(),
                GeoWorldMod.parcels(), geoGen));
        return 1;
    }

    private static int studioExit(CommandSourceStack source) {
        ServerPlayer player = player(source);
        if (player == null) {
            return 0;
        }
        send(source, StudioService.exit(player));
        return 1;
    }

    private static com.evansgisgen.geoworld.landmark.LandmarkIndex landmarks(
            CommandSourceStack source) {
        var generator = source.getServer().overworld().getChunkSource()
                .getGenerator();
        return generator instanceof GeoChunkGenerator geoGen
                ? geoGen.landmarks(source.registryAccess(),
                        StudioService.overlayRoot(source.getServer().overworld()))
                : com.evansgisgen.geoworld.landmark.LandmarkIndex.EMPTY;
    }

    private static ServerPlayer player(CommandSourceStack source) {
        try {
            return source.getPlayerOrException();
        } catch (com.mojang.brigadier.exceptions.CommandSyntaxException e) {
            fail(source, "player-only command");
            return null;
        }
    }

    private static void send(CommandSourceStack source, StudioService.Result r) {
        if (r.ok()) {
            source.sendSuccess(() -> Component.literal(r.message()), false);
        } else {
            fail(source, r.message());
        }
    }

    private static void fail(CommandSourceStack source, String message) {
        source.sendFailure(Component.literal(message));
    }

    // --- existing info/geo ----------------------------------------------------

    private static int info(CommandSourceStack source) {
        GeoTransform t = GeoWorldMod.transform();
        source.sendSuccess(() -> Component.literal(String.format(
                "GeoWorld origin=(%d, %d) scale=(%.3f horiz, %.3f vert m/block) datum=(%.1f m -> y%d)",
                t.originX(), t.originZ(), t.horizontalMetersPerBlock(), t.verticalMetersPerBlock(),
                t.datumElevationMeters(), t.datumY())), false);
        GeoDataset dataset = GeoWorldMod.dataset();
        source.sendSuccess(() -> Component.literal(dataset.isEmpty()
                ? "GeoWorld dataset: none (Phase 0 fallback circle active)"
                : String.format("GeoWorld dataset: '%s' (%d tiles) at %s",
                        dataset.name(), dataset.tileCount(), dataset.root())), false);
        ParcelIndex parcels = GeoWorldMod.parcels();
        source.sendSuccess(() -> Component.literal(parcels.isEmpty()
                ? "GeoWorld parcels: none"
                : String.format("GeoWorld parcels: %d", parcels.size())), false);
        return 1;
    }

    private static int geoAtSource(CommandSourceStack source) {
        Vec3 pos = source.getPosition();
        GeoPoint geo = GeoWorldMod.transform().geoPoint((int) Math.floor(pos.x), (int) Math.floor(pos.z));
        source.sendSuccess(() -> Component.literal(String.format(
                "(%d, %d) -> %.2f m east, %.2f m north of projection origin",
                (int) Math.floor(pos.x), (int) Math.floor(pos.z), geo.eastMeters(), geo.northMeters())), false);
        return 1;
    }

    private static int geoToBlock(CommandSourceStack source, double east, double north) {
        GeoTransform t = GeoWorldMod.transform();
        source.sendSuccess(() -> Component.literal(String.format(
                "%.2f m east, %.2f m north -> block (%d, %d)",
                east, north, t.blockX(east), t.blockZ(north))), false);
        return 1;
    }
}
