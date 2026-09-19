package com.evansgisgen.geoworld.debug;

import com.evansgisgen.geoworld.GeoWorldMod;
import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoPoint;
import com.evansgisgen.geoworld.geo.GeoTransform;
import com.mojang.brigadier.arguments.DoubleArgumentType;
import net.minecraft.commands.Commands;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.network.chat.Component;
import net.minecraft.world.phys.Vec3;
import net.neoforged.neoforge.event.RegisterCommandsEvent;

/**
 * Debug commands for verifying the geographic coordinate mapping in-game.
 *
 * <ul>
 *   <li>{@code /geoworld info} — shows the active transform (origin, scale, datum)</li>
 *   <li>{@code /geoworld geo} — shows the geographic coordinates of the source position</li>
 *   <li>{@code /geoworld geo <east> <north>} — shows the block coordinates for a geo point</li>
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
        event.getDispatcher().register(Commands.literal("geoworld").then(info).then(geo));
    }

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
