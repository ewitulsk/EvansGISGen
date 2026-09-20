package com.evansgisgen.geoworld.studio;

import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoTile;
import com.evansgisgen.geoworld.landmark.LandmarkIndex;
import com.evansgisgen.geoworld.parcel.Parcel;
import com.evansgisgen.geoworld.parcel.ParcelIndex;
import com.evansgisgen.geoworld.parcel.ParcelSelection;
import com.evansgisgen.geoworld.worldgen.GeoChunkGenerator;
import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashSet;
import java.util.Set;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtIo;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructurePlaceSettings;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import net.minecraft.world.level.storage.LevelResource;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * The parcel-studio workflow (Phase 12): stage a real-world lot in the
 * {@code geoworld:studio} dimension — true DEM terrain plus a context ring,
 * selected-parcel boundaries (red), neighbor parcel lines (gray), footprint
 * edges (yellow), and either the existing landmark template or procedural
 * shells as scaffolding — then capture the edited lot back into the overlay
 * landmark set as a {@code REPLACE_LOT} template.
 *
 * <p>A lot is a {@link ParcelSelection}: one parcel for a house, several for
 * a city block or streetscape. Multi-parcel selections capture and register
 * as a single {@code lot_<hash>} landmark keyed by the sorted parcel set.
 */
public final class StudioService {
    private static final Logger LOGGER = LoggerFactory.getLogger(StudioService.class);
    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    public static final ResourceKey<Level> STUDIO = ResourceKey.create(
            Registries.DIMENSION,
            ResourceLocation.fromNamespaceAndPath("geoworld", "studio"));

    /** World-save directory holding studio-captured landmarks. */
    public static final String OVERLAY_DIR = "geoworld_landmarks";

    /** Context ring beyond the selection bbox (geo meters). */
    private static final int CONTEXT = 32;
    /** Capture extends this far below the lowest ground (basements). */
    private static final int BASE_DROP = 8;
    /** Capture headroom above the highest ground. */
    private static final int HEADROOM = 32;
    /** Guard against accidentally staging an absurd selection. */
    private static final int MAX_LOT_SPAN = 640;

    private static final BlockState STONE = Blocks.STONE.defaultBlockState();
    private static final BlockState DIRT = Blocks.DIRT.defaultBlockState();
    private static final BlockState GRASS = Blocks.GRASS_BLOCK.defaultBlockState();
    private static final BlockState SAND = Blocks.SAND.defaultBlockState();
    private static final BlockState WATER = Blocks.WATER.defaultBlockState();
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();
    private static final BlockState PARCEL_LINE = Blocks.RED_CONCRETE.defaultBlockState();
    private static final BlockState NEIGHBOR_LINE = Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState();
    private static final BlockState FOOTPRINT_LINE = Blocks.YELLOW_CONCRETE.defaultBlockState();

    private StudioService() {}

    public record Result(boolean ok, String message) {}

    // --- enter --------------------------------------------------------------

    /**
     * Stages {@code selection}'s lot in the studio dimension and teleports
     * the player to it. Re-entering re-stages from scratch — either the
     * saved landmark or the procedural baseline — so a discarded session
     * never leaves half-edits behind.
     */
    public static Result enter(ServerPlayer player, GeoDataset dataset,
            ParcelIndex parcels, LandmarkIndex landmarks,
            ParcelSelection selection, boolean bare) {
        MinecraftServer server = player.getServer();
        if (server == null) {
            return new Result(false, "no server");
        }
        ServerLevel studio = server.getLevel(STUDIO);
        if (studio == null) {
            return new Result(false,
                    "studio dimension missing — is the geoworld datapack loaded?");
        }
        int dx = dataset.blockX(selection.maxEast() + CONTEXT)
                - dataset.blockX(selection.minEast() - CONTEXT) + 1;
        int dz = dataset.blockZ(selection.minNorth() - CONTEXT)
                - dataset.blockZ(selection.maxNorth() + CONTEXT) + 1;
        if (dx > MAX_LOT_SPAN || dz > MAX_LOT_SPAN) {
            return new Result(false, String.format(
                    "selection too large (%dx%d m incl. context; max %d) — "
                            + "narrow the region", dx, dz, MAX_LOT_SPAN));
        }
        StudioData data = StudioData.get(server.overworld());
        if (data.session(player.getUUID()) != null) {
            return new Result(false,
                    "already in a studio session — /geoworld studio save or exit first");
        }

        String lotKey = selection.landmarkId();
        BlockPos lotOrigin = data.lotSlot(lotKey);
        stage(studio, dataset, parcels, landmarks, selection, lotOrigin, bare);

        data.beginSession(player.getUUID(), new StudioData.Session(
                selection.idList(), lotOrigin,
                player.level().dimension().location(),
                player.blockPosition(), player.getYRot(), player.getXRot()));

        // Spawn at the selection center, on the surface.
        double ce = (selection.minEast() + selection.maxEast()) / 2.0;
        double cn = (selection.minNorth() + selection.maxNorth()) / 2.0;
        int cx = lotOrigin.getX() + dataset.blockX(ce)
                - dataset.blockX(selection.minEast() - CONTEXT);
        int cz = lotOrigin.getZ() + dataset.blockZ(cn)
                - dataset.blockZ(selection.maxNorth() + CONTEXT);
        int ground = groundAt(dataset, ce, cn);
        player.teleportTo(studio, cx + 0.5, ground + 2, cz + 0.5,
                player.getYRot(), player.getXRot());
        return new Result(true, String.format(
                "staged %s (%d parcel%s)%s at studio (%d, %d) — red=lot, "
                        + "gray=neighbors, yellow=footprint; "
                        + "/geoworld studio save when done",
                lotKey, selection.parcels().size(),
                selection.parcels().size() == 1 ? "" : "s",
                bare ? " (bare)" : "",
                lotOrigin.getX(), lotOrigin.getZ()));
    }

    // --- stage ---------------------------------------------------------------

    /** Stages the lot — public so scripted surveys can exercise it headless. */
    public static void stage(ServerLevel studio, GeoDataset dataset,
            ParcelIndex parcels, LandmarkIndex landmarks,
            ParcelSelection selection, BlockPos lotOrigin, boolean bare) {
        double e0 = selection.minEast() - CONTEXT;
        double n1 = selection.maxNorth() + CONTEXT;
        int bx0 = dataset.blockX(e0);
        int bz0 = dataset.blockZ(n1);
        int dx = dataset.blockX(selection.maxEast() + CONTEXT) - bx0 + 1;
        int dz = dataset.blockZ(selection.minNorth() - CONTEXT) - bz0 + 1;

        // 1) Terrain: real DEM columns over the lot rect.
        int[][] grounds = new int[dx][dz];
        for (int sx = 0; sx < dx; sx++) {
            for (int sz = 0; sz < dz; sz++) {
                int wx = lotOrigin.getX() + sx;
                int wz = lotOrigin.getZ() + sz;
                int gx = bx0 + sx;
                int gz = bz0 + sz;
                int ground = dataset.elevationAt(gx, gz);
                if (ground == GeoTile.NO_DATA) {
                    ground = dataset.transform().datumY();
                }
                grounds[sx][sz] = ground;
                int water = dataset.waterDepthAt(gx, gz);
                // Terrain column: stone base, dirt fill, surface skin.
                for (int y = ground - BASE_DROP; y <= ground - 4; y++) {
                    studio.setBlock(new BlockPos(wx, y, wz), STONE, Block.UPDATE_ALL);
                }
                for (int y = ground - 3; y <= ground - 1; y++) {
                    studio.setBlock(new BlockPos(wx, y, wz), DIRT, Block.UPDATE_ALL);
                }
                BlockState top = water > 0 ? SAND
                        : surfaceBlock(dataset.surfaceClassAt(gx, gz));
                studio.setBlock(new BlockPos(wx, ground, wz),
                        top != null ? top : GRASS, Block.UPDATE_ALL);
                // Clear the build volume; fill water above the bed.
                for (int y = ground + 1; y <= ground + HEADROOM; y++) {
                    studio.setBlock(new BlockPos(wx, y, wz),
                            water > 0 && y <= ground + water ? WATER : AIR,
                            Block.UPDATE_ALL);
                }
            }
        }

        // 2) Parcel rings: selected red, context neighbors gray.
        Set<Long> selected = new HashSet<>();
        Set<String> selectedIds = new HashSet<>();
        for (Parcel p : selection.parcels()) {
            selected.addAll(ringCells(p));
            selectedIds.add(p.id());
        }
        for (long cell : selected) {
            placeMarker(studio, grounds, lotOrigin, bx0, bz0, cell, PARCEL_LINE);
        }
        for (Parcel other : parcels.within(
                selection.minEast() - CONTEXT, selection.minNorth() - CONTEXT,
                selection.maxEast() + CONTEXT, selection.maxNorth() + CONTEXT)) {
            if (selectedIds.contains(other.id())) {
                continue;
            }
            for (long cell : ringCells(other)) {
                if (!selected.contains(cell)) {
                    placeMarker(studio, grounds, lotOrigin, bx0, bz0, cell,
                            NEIGHBOR_LINE);
                }
            }
        }

        // 3) Buildings: procedural shells inside the selection (unless bare
        // or a landmark already stages the build), footprint outlines
        // elsewhere for context.
        boolean hasLandmark = landmarks.findById(selection.landmarkId()).isPresent();
        for (int sx = 0; sx < dx; sx++) {
            for (int sz = 0; sz < dz; sz++) {
                int gx = bx0 + sx;
                int gz = bz0 + sz;
                int cls = dataset.buildingClassAt(gx, gz);
                if (cls == 0) {
                    continue;
                }
                int wx = lotOrigin.getX() + sx;
                int wz = lotOrigin.getZ() + sz;
                int ground = grounds[sx][sz];
                if (!hasLandmark && !bare
                        && selection.contains(geoEast(dataset, gx),
                                geoNorth(dataset, gz))) {
                    stageShell(studio, dataset, wx, wz, gx, gz, cls, ground);
                } else if (isFootprintEdge(dataset, gx, gz)) {
                    studio.setBlock(new BlockPos(wx, ground + 1, wz),
                            FOOTPRINT_LINE, Block.UPDATE_ALL);
                }
            }
        }

        // 4) Existing landmark: paste the saved template so the player edits
        // the real build, not a regenerated shell.
        landmarks.findById(selection.landmarkId()).ifPresent(placed -> {
            BlockPos studioOrigin = new BlockPos(
                    lotOrigin.getX() + (placed.origin().getX() - bx0),
                    placed.origin().getY(),
                    lotOrigin.getZ() + (placed.origin().getZ() - bz0));
            placed.template().placeInWorld(studio, studioOrigin,
                    placed.def().anchor(),
                    new StructurePlaceSettings().setRotation(placed.def().rotation())
                            .setRotationPivot(placed.def().anchor()),
                    RandomSource.create(selection.landmarkId().hashCode()),
                    Block.UPDATE_ALL);
        });
    }

    private static double geoEast(GeoDataset dataset, int gx) {
        return dataset.transform().geoPoint(gx, 0).eastMeters();
    }

    private static double geoNorth(GeoDataset dataset, int gz) {
        return dataset.transform().geoPoint(0, gz).northMeters();
    }

    @Nullable
    private static BlockState surfaceBlock(int surfaceClass) {
        return GeoChunkGenerator.surfaceBlock(surfaceClass);
    }

    /** Polygon edge cells as packed block-coord longs (geo block space). */
    private static Set<Long> ringCells(Parcel parcel) {
        Set<Long> cells = new HashSet<>();
        for (double[][] ring : parcel.rings()) {
            for (int i = 0, j = ring.length - 1; i < ring.length; j = i++) {
                double[] a = ring[i];
                double[] b = ring[j];
                int steps = Math.max(1, (int) Math.ceil(Math.max(
                        Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]))));
                for (int k = 0; k <= steps; k++) {
                    double t = (double) k / steps;
                    int gx = (int) Math.round(a[0] + (b[0] - a[0]) * t);
                    int gz = (int) Math.round(-(a[1] + (b[1] - a[1]) * t));
                    cells.add((((long) gx) << 32) | (gz & 0xFFFFFFFFL));
                }
            }
        }
        return cells;
    }

    private static void placeMarker(ServerLevel studio, int[][] grounds,
            BlockPos lotOrigin, int bx0, int bz0, long cell, BlockState block) {
        int gx = (int) (cell >> 32);
        int gz = (int) cell;
        int sx = gx - bx0;
        int sz = gz - bz0;
        if (sx < 0 || sz < 0 || sx >= grounds.length || sz >= grounds[0].length) {
            return;
        }
        studio.setBlock(new BlockPos(lotOrigin.getX() + sx,
                grounds[sx][sz] + 1, lotOrigin.getZ() + sz), block,
                Block.UPDATE_ALL);
    }

    private static boolean isFootprintEdge(GeoDataset dataset, int gx, int gz) {
        return dataset.buildingClassAt(gx + 1, gz) == 0
                || dataset.buildingClassAt(gx - 1, gz) == 0
                || dataset.buildingClassAt(gx, gz + 1) == 0
                || dataset.buildingClassAt(gx, gz - 1) == 0;
    }

    /** Mirrors GeoChunkGenerator.buildBuildings for a single column. */
    private static void stageShell(ServerLevel studio, GeoDataset dataset,
            int wx, int wz, int gx, int gz, int cls, int ground) {
        var palette = GeoChunkGenerator.buildingPalette(cls);
        if (palette == null) {
            return;
        }
        int levels = Math.max(1, dataset.buildingLevelsAt(gx, gz));
        int roof = ground + levels * 3 + 1;
        if (isFootprintEdge(dataset, gx, gz)) {
            for (int y = ground; y < roof; y++) {
                boolean window = y > ground && y < roof - 1
                        && Math.floorMod(gx + gz, 3) == 0;
                studio.setBlock(new BlockPos(wx, y, wz),
                        window ? palette.window() : palette.wall(),
                        Block.UPDATE_ALL);
            }
        } else {
            studio.setBlock(new BlockPos(wx, ground, wz), palette.floor(),
                    Block.UPDATE_ALL);
        }
        studio.setBlock(new BlockPos(wx, roof, wz), palette.roof(),
                Block.UPDATE_ALL);
    }

    // --- save / exit ----------------------------------------------------------

    public static Result save(ServerPlayer player, GeoDataset dataset,
            ParcelIndex parcels, GeoChunkGenerator generator) {
        MinecraftServer server = player.getServer();
        if (server == null) {
            return new Result(false, "no server");
        }
        StudioData data = StudioData.get(server.overworld());
        StudioData.Session session = data.session(player.getUUID());
        if (session == null) {
            return new Result(false, "no active studio session — "
                    + "start one with /geoworld studio <address|x,z>");
        }
        ServerLevel studio = server.getLevel(STUDIO);
        ParcelSelection selection = selectionOf(parcels, session.parcelId());
        if (studio == null || selection == null) {
            return new Result(false, "studio session is stale — "
                    + "use /geoworld studio exit and re-enter");
        }
        Result r = saveLot(studio, server.overworld(), dataset, selection,
                session.lotOrigin(), generator);
        if (r.ok()) {
            returnBack(player, data.endSession(player.getUUID()));
        }
        return r;
    }

    /** Rebuilds a session's selection from its persisted parcel-id list. */
    @Nullable
    private static ParcelSelection selectionOf(ParcelIndex parcels,
            String idList) {
        java.util.List<Parcel> list = new java.util.ArrayList<>();
        for (String id : idList.split(",")) {
            Parcel p = parcels.byId(id);
            if (p == null) {
                return null;
            }
            list.add(p);
        }
        return list.isEmpty() ? null : ParcelSelection.of(list);
    }

    /**
     * Player-independent save: capture the lot volume, write the overlay
     * landmark, refresh the index, and stamp the template into the
     * overworld. Split out so scripted scenarios can exercise it without a
     * player entity.
     */
    public static Result saveLot(ServerLevel studio, ServerLevel overworld,
            GeoDataset dataset, ParcelSelection selection, BlockPos lotOrigin,
            GeoChunkGenerator generator) {
        // Capture region = selection bbox (geo), inside the context ring;
        // vertically base-drop through headroom.
        int ox = lotOrigin.getX()
                + (dataset.blockX(selection.minEast())
                        - dataset.blockX(selection.minEast() - CONTEXT));
        int oz = lotOrigin.getZ()
                + (dataset.blockZ(selection.maxNorth())
                        - dataset.blockZ(selection.maxNorth() + CONTEXT));
        int dx = dataset.blockX(selection.maxEast())
                - dataset.blockX(selection.minEast()) + 1;
        int dz = dataset.blockZ(selection.minNorth())
                - dataset.blockZ(selection.maxNorth()) + 1;

        int minGround = Integer.MAX_VALUE;
        int maxGround = Integer.MIN_VALUE;
        for (int bx = dataset.blockX(selection.minEast());
                bx <= dataset.blockX(selection.maxEast()); bx++) {
            for (int bz = dataset.blockZ(selection.maxNorth());
                    bz <= dataset.blockZ(selection.minNorth()); bz++) {
                int g = dataset.elevationAt(bx, bz);
                if (g != GeoTile.NO_DATA) {
                    minGround = Math.min(minGround, g);
                    maxGround = Math.max(maxGround, g);
                }
            }
        }
        if (minGround == Integer.MAX_VALUE) {
            minGround = maxGround = dataset.transform().datumY();
        }
        int baseY = minGround - BASE_DROP;
        int height = maxGround + HEADROOM - baseY + 1;

        StructureTemplate template = new StructureTemplate();
        template.fillFromWorld(studio, new BlockPos(ox, baseY, oz),
                new net.minecraft.core.Vec3i(dx, height, dz), false, null);

        Path overlay = overlayRoot(overworld);
        String landmarkId = selection.landmarkId();
        String nbtName = landmarkId + ".nbt";
        try {
            Files.createDirectories(overlay.resolve("landmarks"));
            NbtIo.writeCompressed(template.save(new CompoundTag()),
                    overlay.resolve("landmarks").resolve(nbtName));
            upsertLandmark(overlay.resolve("landmarks.json"), dataset, selection,
                    landmarkId, nbtName, baseY);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to save studio lot {}: {}", landmarkId, e.toString());
            return new Result(false, "save failed: " + e.getMessage());
        }

        // Refresh the index so suppressesBuildingAt/forChunk see the new lot,
        // then stamp the template into the already-generated overworld.
        generator.reloadLandmarks(overworld.registryAccess(), overlay);
        int groundCorner = groundAt(dataset, selection.minEast(),
                selection.maxNorth());
        BlockPos origin = new BlockPos(dataset.blockX(selection.minEast()),
                baseY, dataset.blockZ(selection.maxNorth()));
        BlockPos pivot = new BlockPos(0, groundCorner + 1 - baseY, 0);
        template.placeInWorld(overworld, origin, pivot,
                new StructurePlaceSettings().setRotation(Rotation.NONE),
                RandomSource.create(landmarkId.hashCode()), Block.UPDATE_ALL);
        markDirty(overworld, origin, dx, dz);

        return new Result(true, String.format(
                "saved %s (%d parcels, %dx%dx%d) — landmark '%s' now "
                        + "overrides the lot",
                selection.idList(), selection.parcels().size(),
                dx, height, dz, landmarkId));
    }

    private static int groundAt(GeoDataset dataset, double east, double north) {
        int g = dataset.elevationAt(dataset.blockX(east), dataset.blockZ(north));
        return g == GeoTile.NO_DATA ? dataset.transform().datumY() : g;
    }

    public static Path overlayRoot(ServerLevel level) {
        return level.getServer().getWorldPath(LevelResource.ROOT)
                .resolve(OVERLAY_DIR);
    }

    /** Writes/replaces the overlay landmarks.json entry for this lot. */
    private static void upsertLandmark(Path file, GeoDataset dataset,
            ParcelSelection selection, String landmarkId, String nbtName,
            int baseY) throws IOException {
        JsonObject root;
        if (Files.isRegularFile(file)) {
            root = GSON.fromJson(Files.readString(file), JsonObject.class);
        } else {
            root = new JsonObject();
            root.add("landmarks", new JsonArray());
        }
        JsonArray list = root.getAsJsonArray("landmarks");

        int groundCorner = groundAt(dataset, selection.minEast(),
                selection.maxNorth());
        JsonObject def = new JsonObject();
        def.addProperty("id", landmarkId);
        def.addProperty("template", nbtName);
        JsonObject pos = new JsonObject();
        pos.addProperty("east", selection.minEast());
        pos.addProperty("north", selection.maxNorth());
        def.add("position", pos);
        JsonArray anchor = new JsonArray();
        anchor.add(0);
        anchor.add(groundCorner + 1 - baseY);
        anchor.add(0);
        def.add("anchor", anchor);
        def.addProperty("rotation", "NONE");
        def.addProperty("terrain", "REPLACE_LOT");
        JsonArray ids = new JsonArray();
        for (Parcel p : selection.parcels()) {
            ids.add(p.id());
        }
        def.add("parcels", ids);

        JsonArray merged = new JsonArray();
        for (var el : list) {
            if (!el.getAsJsonObject().get("id").getAsString().equals(landmarkId)) {
                merged.add(el);
            }
        }
        merged.add(def);
        root.add("landmarks", merged);
        Files.writeString(file, GSON.toJson(root));
    }

    /** Force the placed region's chunks to resave + resend to clients. */
    private static void markDirty(ServerLevel level, BlockPos origin,
            int dx, int dz) {
        for (int cx = origin.getX() >> 4; cx <= (origin.getX() + dx - 1) >> 4; cx++) {
            for (int cz = origin.getZ() >> 4; cz <= (origin.getZ() + dz - 1) >> 4; cz++) {
                var chunk = level.getChunkSource().getChunk(cx, cz, false);
                if (chunk != null) {
                    chunk.setUnsaved(true);
                }
            }
        }
    }

    /** Leaves the studio without saving; teleports the player back. */
    public static Result exit(ServerPlayer player) {
        MinecraftServer server = player.getServer();
        if (server == null) {
            return new Result(false, "no server");
        }
        StudioData data = StudioData.get(server.overworld());
        StudioData.Session session = data.endSession(player.getUUID());
        if (session == null) {
            return new Result(false, "no active studio session");
        }
        returnBack(player, session);
        return new Result(true, "left studio (lot not saved)");
    }

    private static void returnBack(ServerPlayer player,
            @Nullable StudioData.Session session) {
        if (session == null || player.getServer() == null) {
            return;
        }
        ServerLevel dest = player.getServer().getLevel(ResourceKey.create(
                Registries.DIMENSION, session.returnDim()));
        if (dest == null) {
            dest = player.getServer().overworld();
        }
        BlockPos p = session.returnPos();
        player.teleportTo(dest, p.getX() + 0.5, p.getY(), p.getZ() + 0.5,
                session.returnYaw(), session.returnPitch());
    }

    /**
     * Resolves a studio query to a selection: "x,z" block coords pick the
     * containing parcel; "x0,z0 x1,z1" (or region form) selects every parcel
     * intersecting that block-coord rect; anything else is an address.
     */
    public static java.util.Optional<ParcelSelection> resolveQuery(
            GeoDataset dataset, ParcelIndex parcels, String query) {
        Double[] rect = parseRect(query);
        if (rect != null) {
            double e0 = geoEast(dataset, (int) Math.round(Math.min(rect[0], rect[2])));
            double e1 = geoEast(dataset, (int) Math.round(Math.max(rect[0], rect[2])));
            // block z -> geo north flips sign: min z is the north edge.
            double n1 = geoNorth(dataset, (int) Math.round(Math.min(rect[1], rect[3])));
            double n0 = geoNorth(dataset, (int) Math.round(Math.max(rect[1], rect[3])));
            var hits = parcels.within(e0, n0, e1, n1);
            return hits.isEmpty()
                    ? java.util.Optional.empty()
                    : java.util.Optional.of(ParcelSelection.of(hits));
        }
        Double[] coords = ParcelIndex.parseCoords(query);
        if (coords != null) {
            // "x,z" minecraft block coords -> the containing parcel.
            double e = geoEast(dataset, (int) Math.round(coords[0]));
            double n = geoNorth(dataset, (int) Math.round(coords[1]));
            return parcels.at(e, n).map(ParcelSelection::single);
        }
        return parcels.resolve(query).map(ParcelSelection::single);
    }

    /** Parses a four-number rect "x0,z0 x1,z1" (block coords); else null. */
    @Nullable
    private static Double[] parseRect(String query) {
        java.util.regex.Matcher m = java.util.regex.Pattern.compile(
                "\\s*(-?[\\d.]+)[,\\s]+(-?[\\d.]+)[,\\s]+(-?[\\d.]+)[,\\s]+(-?[\\d.]+)\\s*")
                .matcher(query);
        return m.matches()
                ? new Double[]{Double.parseDouble(m.group(1)),
                        Double.parseDouble(m.group(2)),
                        Double.parseDouble(m.group(3)),
                        Double.parseDouble(m.group(4))}
                : null;
    }
}
