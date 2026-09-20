package com.evansgisgen.geoworld.landmark;

import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoTile;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderGetter;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.NbtAccounter;
import net.minecraft.nbt.NbtIo;
import net.minecraft.util.RandomSource;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.ServerLevelAccessor;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.levelgen.structure.BoundingBox;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructurePlaceSettings;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Resolves a dataset's {@code landmarks.json} into placed templates and
 * indexes their bounding boxes by chunk (the plan's LandmarkSpatialIndex).
 *
 * <p>When a chunk reaches decoration, every landmark whose transformed
 * bounding box intersects it is rendered with a chunk-clamped
 * {@link StructurePlaceSettings#setBoundingBox bounding box} — the same
 * slicing mechanism vanilla uses, so multi-chunk templates assemble across
 * generation without special-casing.
 *
 * <p>A landmark's bounding box also suppresses procedural building shells
 * inside it, so a curated structure never sits on top of a generated one.
 */
public final class LandmarkIndex {
    private static final Logger LOGGER = LoggerFactory.getLogger(LandmarkIndex.class);
    private static final Gson GSON = new Gson();

    /** A landmark with its template loaded and world placement computed. */
    public record Placed(Landmark def, StructureTemplate template,
                         BlockPos origin, BoundingBox box, int groundY) {}

    public static final LandmarkIndex EMPTY = new LandmarkIndex(Map.of());

    private final Map<Long, List<Placed>> byChunk;
    private final List<Placed> all;

    private LandmarkIndex(Map<Long, List<Placed>> byChunk) {
        this.byChunk = byChunk;
        this.all = byChunk.values().stream().flatMap(List::stream).distinct().toList();
    }

    /** True if this column sits inside a landmark's exact bounding box. */
    public boolean contains(int x, int z) {
        for (Placed p : all) {
            if (p.box().isInside(x, p.box().minY(), z)) {
                return true;
            }
        }
        return false;
    }

    /** True if any landmark claims this column — suppresses building shells. */
    public boolean suppressesBuildingAt(int x, int z) {
        // Margin absorbs the offset between a landmark's template bounds
        // and the imported footprint it replaces.
        for (Placed p : all) {
            BoundingBox b = p.box();
            if (x >= b.minX() - 3 && x <= b.maxX() + 3
                    && z >= b.minZ() - 3 && z <= b.maxZ() + 3) {
                return true;
            }
        }
        return false;
    }

    public List<Placed> forChunk(ChunkPos pos) {
        return byChunk.getOrDefault(pos.toLong(), List.of());
    }

    /** The placed landmark with this id, for studio staging/replacement. */
    public java.util.Optional<Placed> findById(String id) {
        return all.stream().filter(p -> p.def().id().equals(id)).findFirst();
    }

    /**
     * Loads {@code <dataset>/landmarks.json} and the referenced
     * {@code landmarks/*.nbt} templates. Needs a block registry, so this is
     * called lazily at first decoration, not at dataset load.
     */
    public static LandmarkIndex load(GeoDataset dataset, HolderGetter<Block> blocks) {
        return load(dataset, blocks, null);
    }

    /**
     * As {@link #load(GeoDataset, HolderGetter)}, additionally merging an
     * overlay directory ({@code <overlay>/landmarks.json} + {@code
     * landmarks/*.nbt}) — studio-saved lots live there so dataset rebuilds
     * never discard player work. Overlay entries replace dataset entries
     * with the same id.
     */
    public static LandmarkIndex load(GeoDataset dataset, HolderGetter<Block> blocks,
            @Nullable Path overlayRoot) {
        Path root = dataset.root();
        if (root == null) {
            return EMPTY;
        }
        // id -> (def, nbtDir); overlay entries overwrite dataset ones.
        Map<String, Map.Entry<Landmark, Path>> defs = new java.util.LinkedHashMap<>();
        collectDefs(root, defs);
        if (overlayRoot != null) {
            collectDefs(overlayRoot, defs);
        }
        if (defs.isEmpty()) {
            return EMPTY;
        }

        Map<Long, List<Placed>> byChunk = new HashMap<>();
        for (var entry : defs.values()) {
            Landmark def = entry.getKey();
            Path nbt = entry.getValue().resolve("landmarks").resolve(def.templatePath());
            try {
                CompoundTag tag = NbtIo.readCompressed(nbt, NbtAccounter.unlimitedHeap());
                StructureTemplate template = new StructureTemplate();
                template.load(blocks, tag);

                int x = dataset.blockX(def.east());
                int z = dataset.blockZ(def.north());
                GeoTile tile = dataset.tileAt(x, z).orElse(null);
                int groundY = tile != null
                        ? tile.elevation(dataset.localCoord(x), dataset.localCoord(z))
                        : GeoTile.NO_DATA;
                if (groundY == GeoTile.NO_DATA) {
                    LOGGER.warn("Landmark '{}' sits outside dataset elevation — skipped", def.id());
                    continue;
                }
                // The anchor's Y is the template's declared ground plane;
                // it lands one block above the dataset terrain surface, so
                // template y < anchor.y (a basement) sinks into the ground.
                BlockPos worldAnchor = new BlockPos(x, groundY + 1, z);
                BlockPos origin = worldAnchor.subtract(def.anchor());
                StructurePlaceSettings settings = new StructurePlaceSettings()
                        .setRotation(def.rotation())
                        .setRotationPivot(def.anchor());
                BoundingBox box = template.getBoundingBox(settings, origin);
                Placed placed = new Placed(def, template, origin, box, groundY);
                for (int cx = box.minX() >> 4; cx <= box.maxX() >> 4; cx++) {
                    for (int cz = box.minZ() >> 4; cz <= box.maxZ() >> 4; cz++) {
                        byChunk.computeIfAbsent(ChunkPos.asLong(cx, cz),
                                k -> new ArrayList<>()).add(placed);
                    }
                }
                LOGGER.info("Landmark '{}' at ({}, {}, {}) box {}", def.id(),
                        origin.getX(), origin.getY(), origin.getZ(), box);
            } catch (IOException | RuntimeException e) {
                LOGGER.warn("Failed to load landmark '{}': {}", def.id(), e.toString());
            }
        }
        return new LandmarkIndex(byChunk);
    }

    /** Reads {@code <dir>/landmarks.json} into defs keyed by id. */
    private static void collectDefs(Path dir,
            Map<String, Map.Entry<Landmark, Path>> defs) {
        Path file = dir.resolve("landmarks.json");
        if (!Files.isRegularFile(file)) {
            return;
        }
        try {
            for (Landmark def : Landmark.listFromJson(
                    GSON.fromJson(Files.readString(file), JsonObject.class))) {
                defs.put(def.id(), Map.entry(def, dir));
            }
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read landmarks {}: {}", file, e.toString());
        }
    }

    /**
     * Renders the chunk-intersecting slice of every landmark claiming this
     * chunk. Runs after vanilla decoration + cover clearing; feature
     * overhang inside the footprint is scrubbed first so a neighbor's tree
     * can't grow into the house.
     */
    public void placeChunk(ServerLevelAccessor level, ChunkAccess chunk,
            java.util.function.Predicate<BlockState> isFeatureOverhang) {
        List<Placed> list = forChunk(chunk.getPos());
        if (list.isEmpty()) {
            return;
        }
        ChunkPos cp = chunk.getPos();
        BoundingBox chunkBox = new BoundingBox(
                cp.getMinBlockX(), level.getMinBuildHeight(), cp.getMinBlockZ(),
                cp.getMaxBlockX(), level.getMaxBuildHeight() - 1, cp.getMaxBlockZ());
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (Placed p : list) {
            scrubOverhang(chunk, p, chunkBox, isFeatureOverhang);
            applyTerrainPolicy(chunk, p, chunkBox);
            StructurePlaceSettings settings = new StructurePlaceSettings()
                    .setRotation(p.def().rotation())
                    .setRotationPivot(p.def().anchor())
                    .setBoundingBox(chunkBox);
            RandomSource random = RandomSource.create(
                    cp.toLong() * 31L + p.def().id().hashCode());
            p.template().placeInWorld(level, p.origin(), p.def().anchor(),
                    settings, random, Block.UPDATE_ALL);
            if (p.def().terrainPolicy() == TerrainPolicy.FOLLOW_TERRAIN) {
                followTerrain(chunk, p, chunkBox);
            }
        }
    }

    /** Removes leaves/logs/plants/snow inside the footprint before placing. */
    private static void scrubOverhang(ChunkAccess chunk, Placed p,
            BoundingBox chunkBox,
            java.util.function.Predicate<BlockState> isFeatureOverhang) {
        BoundingBox box = intersect(p.box(), chunkBox);
        if (box == null) {
            return;
        }
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int x = box.minX(); x <= box.maxX(); x++) {
            for (int z = box.minZ(); z <= box.maxZ(); z++) {
                for (int y = box.minY(); y <= box.maxY(); y++) {
                    if (isFeatureOverhang.test(chunk.getBlockState(pos.set(x, y, z)))) {
                        chunk.setBlockState(pos, Blocks.AIR.defaultBlockState(), false);
                    }
                }
            }
        }
    }

    private static void applyTerrainPolicy(ChunkAccess chunk, Placed p,
            BoundingBox chunkBox) {
        TerrainPolicy policy = p.def().terrainPolicy();
        // REPLACE_LOT templates carry their own terrain (air included) — no
        // fill/cut pass; FOLLOW_TERRAIN runs its own post-pass.
        if (policy == TerrainPolicy.NONE || policy == TerrainPolicy.FOLLOW_TERRAIN
                || policy == TerrainPolicy.REPLACE_LOT) {
            return;
        }
        BoundingBox box = intersect(p.box(), chunkBox);
        if (box == null) {
            return;
        }
        int ground = p.groundY();
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int x = box.minX(); x <= box.maxX(); x++) {
            for (int z = box.minZ(); z <= box.maxZ(); z++) {
                // Fill: air pockets below the ground plane become dirt so the
                // foundation never floats over a cave mouth or carved dip.
                for (int y = ground - 1; y > chunk.getMinBuildHeight(); y--) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (!cur.isAir() && cur.getFluidState().isEmpty()) {
                        break;
                    }
                    chunk.setBlockState(pos, Blocks.DIRT.defaultBlockState(), false);
                }
                if (policy == TerrainPolicy.LEVEL_FOUNDATION) {
                    // Cut: everything above the ground plane inside the
                    // footprint — terrain, water, whatever — becomes air.
                    for (int y = ground + 1; y <= box.maxY(); y++) {
                        chunk.setBlockState(pos.set(x, y, z),
                                Blocks.AIR.defaultBlockState(), false);
                    }
                } else { // CUT_AND_FILL — trim terrain towering over the roof
                    for (int y = p.box().maxY() + 1; y <= chunk.getMaxBuildHeight() - 1; y++) {
                        BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                        if (cur.isAir()) {
                            break;
                        }
                        chunk.setBlockState(pos, Blocks.AIR.defaultBlockState(), false);
                    }
                }
            }
        }
    }

    /** Extends each column's lowest placed block down to solid terrain. */
    private static void followTerrain(ChunkAccess chunk, Placed p,
            BoundingBox chunkBox) {
        BoundingBox box = intersect(p.box(), chunkBox);
        if (box == null) {
            return;
        }
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        for (int x = box.minX(); x <= box.maxX(); x++) {
            for (int z = box.minZ(); z <= box.maxZ(); z++) {
                BlockState top = null;
                int y = box.minY();
                for (; y <= box.maxY(); y++) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (!cur.isAir()) {
                        top = cur;
                        break;
                    }
                }
                if (top == null) {
                    continue;
                }
                for (y--; y > box.minY() - 32 && y > chunk.getMinBuildHeight(); y--) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (!cur.isAir() && cur.getFluidState().isEmpty()) {
                        break;
                    }
                    chunk.setBlockState(pos, top, false);
                }
            }
        }
    }

    private static BoundingBox intersect(BoundingBox a, BoundingBox b) {
        if (a.maxX() < b.minX() || a.minX() > b.maxX()
                || a.maxZ() < b.minZ() || a.minZ() > b.maxZ()
                || a.maxY() < b.minY() || a.minY() > b.maxY()) {
            return null;
        }
        return new BoundingBox(
                Math.max(a.minX(), b.minX()), Math.max(a.minY(), b.minY()),
                Math.max(a.minZ(), b.minZ()),
                Math.min(a.maxX(), b.maxX()), Math.min(a.maxY(), b.maxY()),
                Math.min(a.maxZ(), b.maxZ()));
    }
}
