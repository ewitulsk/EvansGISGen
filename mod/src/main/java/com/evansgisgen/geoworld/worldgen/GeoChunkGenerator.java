package com.evansgisgen.geoworld.worldgen;

import com.evansgisgen.geoworld.GeoWorldMod;
import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoTile;
import com.evansgisgen.geoworld.geo.GeoTransform;
import com.mojang.datafixers.util.Pair;
import com.mojang.serialization.MapCodec;
import java.util.EnumSet;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.HolderSet;
import net.minecraft.core.RegistryAccess;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.WorldGenRegion;
import net.minecraft.util.Mth;
import net.minecraft.util.random.WeightedRandomList;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.NoiseColumn;
import net.minecraft.world.level.StructureManager;
import net.minecraft.world.level.WorldGenLevel;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeManager;
import net.minecraft.world.level.biome.MobSpawnSettings;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.ChunkAccess;
import net.minecraft.world.level.chunk.ChunkGenerator;
import net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator;
import net.minecraft.world.level.chunk.ChunkGeneratorStructureState;
import net.minecraft.world.level.levelgen.GenerationStep;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.level.levelgen.RandomState;
import net.minecraft.world.level.levelgen.blending.Blender;
import net.minecraft.world.level.levelgen.structure.Structure;
import net.minecraft.world.level.levelgen.structure.StructureSet;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplateManager;
import org.jetbrains.annotations.Nullable;

/**
 * Chunk generator that wraps a vanilla {@link ChunkGenerator} (typically a
 * {@code NoiseBasedChunkGenerator}) and applies geographic overrides on top.
 *
 * <p>Phase 0/1 proof-of-concept: terrain is deformed toward {@link #TARGET_HEIGHT}
 * inside a circle of radius {@link #EDGE_RADIUS} centered on the configured
 * geographic origin ({@link GeoTransform#originX()}/{@link GeoTransform#originZ()}),
 * fully flattened within {@link #FULL_RADIUS} and smoothly blended to vanilla
 * at the rim. All other behavior is delegated to the wrapped generator.
 */
public final class GeoChunkGenerator extends NoiseBasedChunkGenerator {
    private static final double FULL_RADIUS = 400.0;
    private static final double EDGE_RADIUS = 500.0;
    private static final int TARGET_HEIGHT = 70;

    private static final BlockState FILL_BLOCK = Blocks.STONE.defaultBlockState();
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();
    private static final BlockState WATER = Blocks.WATER.defaultBlockState();

    // Road surface class ids — shared with the compiler (roads.py).
    static final int ROAD_NONE = 0;
    static final int ROAD_ASPHALT = 1;
    static final int ROAD_CURB = 2;
    static final int ROAD_SIDEWALK = 3;
    static final int ROAD_SHOULDER = 4;
    static final int ROAD_TRACK = 5;
    static final int ROAD_MARKING = 6;

    /** Block each road surface class renders as (theme hook for Phase 7). */
    public static BlockState roadBlock(int roadClass) {
        return switch (roadClass) {
            case ROAD_ASPHALT -> Blocks.GRAY_CONCRETE.defaultBlockState();
            case ROAD_CURB -> Blocks.SMOOTH_STONE.defaultBlockState();
            case ROAD_SIDEWALK -> Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState();
            case ROAD_SHOULDER -> Blocks.GRAVEL.defaultBlockState();
            case ROAD_TRACK -> Blocks.DIRT_PATH.defaultBlockState();
            case ROAD_MARKING -> Blocks.YELLOW_CONCRETE.defaultBlockState();
            default -> AIR;
        };
    }

    private final NoiseBasedChunkGenerator delegate;
    private final GeoDataset dataset;

    public GeoChunkGenerator(NoiseBasedChunkGenerator delegate) {
        this(delegate, GeoWorldMod.dataset());
    }

    public GeoChunkGenerator(NoiseBasedChunkGenerator delegate, GeoDataset dataset) {
        // Extending NoiseBasedChunkGenerator (not ChunkGenerator) is load-bearing:
        // ChunkMap creates the level's RandomState from the generator's noise
        // settings guarded by `instanceof NoiseBasedChunkGenerator`, falling back
        // to NoiseGeneratorSettings.dummy() otherwise — which produces an
        // empty noise router and an all-water world.
        super(delegate.getBiomeSource(), delegate.generatorSettings());
        this.delegate = delegate;
        this.dataset = dataset;
    }

    public NoiseBasedChunkGenerator delegate() {
        return delegate;
    }

    public GeoDataset dataset() {
        return dataset;
    }

    @Override
    protected MapCodec<? extends ChunkGenerator> codec() {
        return GeoChunkGeneratorCodec.INSTANCE;
    }

    // --- Phase 0 proof-of-concept deformation ---------------------------------

    /**
     * Fallback influence circle around the configured origin, used only where
     * no dataset tile covers the column (Phase 0 proof-of-concept behavior).
     */
    private double circleInfluence(int x, int z) {
        GeoTransform t = dataset.transform();
        double dx = x - t.originX();
        double dz = z - t.originZ();
        double distSq = dx * dx + dz * dz;
        if (distSq >= EDGE_RADIUS * EDGE_RADIUS) {
            return 0.0;
        }
        if (distSq <= FULL_RADIUS * FULL_RADIUS) {
            return 1.0;
        }
        double s = (EDGE_RADIUS - Math.sqrt(distSq)) / (EDGE_RADIUS - FULL_RADIUS);
        return smootherstep(s);
    }

    private static double smootherstep(double t) {
        return t * t * t * (t * (t * 6.0 - 15.0) + 10.0);
    }

    /**
     * Geographic column target: {@code height} is the solid-surface Y (the
     * riverbed at wet columns — the compiler bakes the channel into the
     * elevation layer), {@code waterDepth} is the compiled water column depth
     * in blocks above the bed (0 = dry).
     */
    private record GeoTarget(int height, double weight, int waterDepth) {}

    /**
     * Resolves the geographic target for a column: tile elevation + influence
     * when a dataset tile covers it, otherwise the fallback circle. Returns
     * null where there is no geographic influence.
     */
    @Nullable
    private GeoTarget geoTarget(int x, int z) {
        GeoTile tile = dataset.tileAt(x, z).orElse(null);
        if (tile != null && tile.hasElevation()) {
            int lx = dataset.localCoord(x);
            int lz = dataset.localCoord(z);
            int height = tile.elevation(lx, lz);
            if (height == GeoTile.NO_DATA) {
                return null;
            }
            double w = dataset.influence(x, z);
            return w <= 0.0 ? null : new GeoTarget(height, w, tile.waterDepth(lx, lz));
        }
        double w = circleInfluence(x, z);
        return w <= 0.0 ? null : new GeoTarget(TARGET_HEIGHT, w, 0);
    }

    private int deformedHeight(int x, int z, int vanillaHeight, Heightmap.Types type) {
        GeoTarget target = geoTarget(x, z);
        if (target == null) {
            return vanillaHeight;
        }
        int h = (int) Math.round(Mth.lerp(target.weight(), vanillaHeight, target.height()));
        // Heightmap types that count fluids (WORLD_SURFACE, MOTION_BLOCKING*)
        // report the water surface on wet columns; OCEAN_FLOOR* report the bed.
        if (target.waterDepth() > 0 && type != Heightmap.Types.OCEAN_FLOOR
                && type != Heightmap.Types.OCEAN_FLOOR_WG) {
            h += target.waterDepth();
        }
        return h;
    }

    /**
     * Vanilla baseline for deformed columns, measured against the solid
     * surface ({@link Heightmap.Types#OCEAN_FLOOR}) since deformation strips
     * vanilla surface water.
     */
    private int vanillaBaseline(int x, int z, Heightmap.Types type, LevelHeightAccessor level, RandomState random) {
        return delegate.getBaseHeight(
                x, z, geoTarget(x, z) == null ? type : Heightmap.Types.OCEAN_FLOOR, level, random);
    }

    /**
     * Vertically shifts every column in the chunk by the difference between its
     * vanilla surface height and the deformed target height, preserving the
     * column's internal structure (strata, caves) rather than rebuilding it.
     */
    private void deformChunk(ChunkAccess chunk) {
        ChunkPos chunkPos = chunk.getPos();

        if (dataset.isEmpty()) {
            // Cheap reject: if the closest point of this chunk to the origin is
            // outside the fallback influence circle, nothing to do. With a real
            // dataset loaded, tile coverage decides per column instead.
            GeoTransform t = dataset.transform();
            int closestX = Mth.clamp(t.originX(), chunkPos.getMinBlockX(), chunkPos.getMaxBlockX());
            int closestZ = Mth.clamp(t.originZ(), chunkPos.getMinBlockZ(), chunkPos.getMaxBlockZ());
            double ddx = closestX - t.originX();
            double ddz = closestZ - t.originZ();
            if (ddx * ddx + ddz * ddz >= EDGE_RADIUS * EDGE_RADIUS) {
                return;
            }
        }

        int minY = getMinY();
        int height = getGenDepth();
        int topY = minY + height - 1;

        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        BlockState[] column = new BlockState[height];
        boolean modified = false;

        for (int lx = 0; lx < 16; lx++) {
            for (int lz = 0; lz < 16; lz++) {
                int x = chunkPos.getBlockX(lx);
                int z = chunkPos.getBlockZ(lz);
                GeoTarget target = geoTarget(x, z);
                if (target == null) {
                    continue;
                }

                int surface = surfaceHeight(chunk, pos, x, z, minY, topY);
                int delta = (int) Math.round(Mth.lerp(target.weight(), surface, target.height())) - surface;

                for (int i = 0; i < height; i++) {
                    column[i] = chunk.getBlockState(pos.set(x, minY + i, z));
                }
                for (int y = minY; y <= topY; y++) {
                    int srcY = y - delta;
                    BlockState state;
                    if (srcY < minY) {
                        state = FILL_BLOCK;
                    } else if (srcY > topY) {
                        state = AIR;
                    } else {
                        state = column[srcY - minY];
                        // Vanilla surface water (oceans, lakes, ponds) must not
                        // ride the shift onto the geographic surface; cave and
                        // aquifer water at/below the solid surface is preserved.
                        if (srcY > surface) {
                            state = stripFluid(state);
                        }
                    }
                    if (state != column[y - minY]) {
                        chunk.setBlockState(pos.set(x, y, z), state, false);
                        modified = true;
                    }
                }

                // Hydrology: the compiled elevation at wet columns is the
                // channel bed; fill water bed+1 .. bed+depth so the river's
                // cross-section comes from the dataset, not the DEM.
                int depth = target.waterDepth();
                if (depth > 0) {
                    int bed = surface + delta;
                    for (int y = bed + 1; y <= bed + depth && y <= topY; y++) {
                        if (!chunk.getBlockState(pos.set(x, y, z)).is(Blocks.WATER)) {
                            chunk.setBlockState(pos.set(x, y, z), WATER, false);
                            modified = true;
                        }
                    }
                }
            }
        }

        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    private static BlockState stripFluid(BlockState state) {
        return state.getFluidState().isEmpty() ? state : AIR;
    }

    /** Topmost non-air, non-fluid block — the solid ground deformation measures against. */
    private static int surfaceHeight(ChunkAccess chunk, BlockPos.MutableBlockPos pos, int x, int z, int minY, int topY) {
        for (int y = topY; y > minY; y--) {
            BlockState state = chunk.getBlockState(pos.set(x, y, z));
            if (!state.isAir() && state.getFluidState().isEmpty()) {
                return y;
            }
        }
        return minY;
    }

    // --- Generation pipeline --------------------------------------------------

    @Override
    public CompletableFuture<ChunkAccess> createBiomes(RandomState randomState, Blender blender, StructureManager structureManager, ChunkAccess chunk) {
        return delegate.createBiomes(randomState, blender, structureManager, chunk);
    }

    @Override
    public CompletableFuture<ChunkAccess> fillFromNoise(Blender blender, RandomState randomState, StructureManager structureManager, ChunkAccess chunk) {
        return delegate.fillFromNoise(blender, randomState, structureManager, chunk)
                .thenApply(generated -> {
                    deformChunk(generated);
                    return generated;
                });
    }

    @Override
    public void buildSurface(WorldGenRegion level, StructureManager structureManager, RandomState random, ChunkAccess chunk) {
        delegate.buildSurface(level, structureManager, random, chunk);
    }

    @Override
    public void applyCarvers(WorldGenRegion level, long seed, RandomState random, BiomeManager biomeManager, StructureManager structureManager, ChunkAccess chunk, GenerationStep.Carving step) {
        delegate.applyCarvers(level, seed, random, biomeManager, structureManager, chunk, step);
    }

    @Override
    public void applyBiomeDecoration(WorldGenLevel level, ChunkAccess chunk, StructureManager structureManager) {
        // Roads are paved before vanilla decoration: trees, flowers and
        // grass patches require dirt-like ground and cannot plant on
        // pavement, so road cells stay clear automatically.
        paveRoads(chunk);
        delegate.applyBiomeDecoration(level, chunk, structureManager);
        // Decoration can still deposit cover (snow layers) on pavement, and
        // already-decorated neighbors may have grown trees into road cells
        // before paving ran — clear anything that isn't terrain.
        clearRoadCover(chunk);
    }

    /**
     * Replaces the top solid blocks of every compiled road column with the
     * class's surface block. The DEM already holds the pavement elevation,
     * so no carving is needed. Water columns are skipped — bridges are a
     * later stage.
     */
    private void paveRoads(ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        // Chunks never straddle tiles (256 % 16 == 0), so one lookup covers
        // the whole chunk.
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || !tile.hasRoad()) {
            return;
        }

        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        boolean modified = false;

        for (int lx = 0; lx < 16; lx++) {
            for (int lz = 0; lz < 16; lz++) {
                int x = chunkPos.getBlockX(lx);
                int z = chunkPos.getBlockZ(lz);
                int tlx = dataset.localCoord(x);
                int tlz = dataset.localCoord(z);
                int road = tile.roadClass(tlx, tlz);
                if (road == ROAD_NONE) {
                    continue;
                }
                if (tile.waterDepth(tlx, tlz) > 0) {
                    continue;  // bridges are a later stage
                }
                int surface = groundHeight(chunk, pos, x, z, minY, topY);
                if (surface <= minY) {
                    continue;
                }
                BlockState roadBlock = roadBlock(road);
                for (int y = surface; y > surface - 3 && y > minY; y--) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (!cur.isAir() && cur.getFluidState().isEmpty() && cur != roadBlock) {
                        chunk.setBlockState(pos.set(x, y, z), roadBlock, false);
                        modified = true;
                    }
                }
            }
        }

        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    /**
     * Removes decoration output sitting above paved cells: snow layers and
     * other replaceable cover deposited by the freeze step, plus trunk/leaf
     * intrusions written by neighbors that decorated before this chunk paved.
     * Only vegetation/cover is stripped — terrain overhangs are left alone.
     */
    private void clearRoadCover(ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || !tile.hasRoad()) {
            return;
        }

        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        boolean modified = false;

        for (int lx = 0; lx < 16; lx++) {
            for (int lz = 0; lz < 16; lz++) {
                int x = chunkPos.getBlockX(lx);
                int z = chunkPos.getBlockZ(lz);
                int tlx = dataset.localCoord(x);
                int tlz = dataset.localCoord(z);
                if (tile.roadClass(tlx, tlz) == ROAD_NONE
                        || tile.waterDepth(tlx, tlz) > 0) {
                    continue;
                }
                int surface = groundHeight(chunk, pos, x, z, minY, topY);
                for (int y = surface + 1; y <= topY; y++) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (isFeatureOverhang(cur)) {
                        chunk.setBlockState(pos.set(x, y, z), AIR, false);
                        modified = true;
                    }
                }
            }
        }

        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    /** Blocks decoration writes above terrain: plants/cover, tree trunks, leaves. */
    private static boolean isFeatureOverhang(BlockState state) {
        return state.canBeReplaced()
                || state.is(net.minecraft.tags.BlockTags.LEAVES)
                || state.is(net.minecraft.tags.BlockTags.LOGS);
    }

    /**
     * Topmost terrain block — like {@link #surfaceHeight} but skips
     * decoration output (leaves, trunks, snow cover) so paving measures
     * the ground, not a tree that grew into the column.
     */
    private static int groundHeight(ChunkAccess chunk, BlockPos.MutableBlockPos pos, int x, int z, int minY, int topY) {
        for (int y = topY; y > minY; y--) {
            BlockState state = chunk.getBlockState(pos.set(x, y, z));
            if (!state.isAir() && state.getFluidState().isEmpty() && !isFeatureOverhang(state)) {
                return y;
            }
        }
        return minY;
    }

    @Override
    public void spawnOriginalMobs(WorldGenRegion level) {
        delegate.spawnOriginalMobs(level);
    }

    // --- Structures -----------------------------------------------------------

    @Override
    public ChunkGeneratorStructureState createState(HolderLookup<StructureSet> structureSetLookup, RandomState randomState, long seed) {
        return delegate.createState(structureSetLookup, randomState, seed);
    }

    @Override
    public void createStructures(RegistryAccess registryAccess, ChunkGeneratorStructureState structureState, StructureManager structureManager, ChunkAccess chunk, StructureTemplateManager structureTemplateManager) {
        delegate.createStructures(registryAccess, structureState, structureManager, chunk, structureTemplateManager);
    }

    @Override
    public void createReferences(WorldGenLevel level, StructureManager structureManager, ChunkAccess chunk) {
        delegate.createReferences(level, structureManager, chunk);
    }

    @Nullable
    @Override
    public Pair<BlockPos, Holder<Structure>> findNearestMapStructure(ServerLevel level, HolderSet<Structure> structure, BlockPos pos, int searchRadius, boolean skipKnownStructures) {
        return delegate.findNearestMapStructure(level, structure, pos, searchRadius, skipKnownStructures);
    }

    // --- Height / shape queries ------------------------------------------------

    @Override
    public int getBaseHeight(int x, int z, Heightmap.Types type, LevelHeightAccessor level, RandomState random) {
        return deformedHeight(x, z, vanillaBaseline(x, z, type, level, random), type);
    }

    @Override
    public NoiseColumn getBaseColumn(int x, int z, LevelHeightAccessor level, RandomState random) {
        NoiseColumn column = delegate.getBaseColumn(x, z, level, random);
        GeoTarget target = geoTarget(x, z);
        if (target == null) {
            return column;
        }
        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        int surface = topY;
        while (surface > minY) {
            BlockState s = column.getBlock(surface);
            if (!s.isAir() && s.getFluidState().isEmpty()) {
                break;
            }
            surface--;
        }
        int delta = (int) Math.round(Mth.lerp(target.weight(), surface, target.height())) - surface;
        boolean changed = delta != 0;
        BlockState[] shifted = new BlockState[getGenDepth()];
        for (int y = minY; y <= topY; y++) {
            int srcY = y - delta;
            BlockState state;
            if (srcY < minY) {
                state = FILL_BLOCK;
            } else if (srcY > topY) {
                state = AIR;
            } else {
                state = column.getBlock(srcY);
                if (srcY > surface) {
                    state = stripFluid(state);
                }
            }
            if (state != column.getBlock(y)) {
                changed = true;
            }
            shifted[y - minY] = state;
        }
        int depth = target.waterDepth();
        if (depth > 0) {
            int bed = surface + delta;
            for (int y = bed + 1; y <= bed + depth && y <= topY; y++) {
                shifted[y - minY] = WATER;
            }
            changed = true;
        }
        // Road columns report the paved surface (same block swap paveRoads
        // performs during decoration).
        GeoTile roadTile = dataset.tileAt(x, z).orElse(null);
        if (depth == 0 && roadTile != null && roadTile.hasRoad()) {
            int road = roadTile.roadClass(dataset.localCoord(x), dataset.localCoord(z));
            if (road != ROAD_NONE) {
                BlockState roadBlock = roadBlock(road);
                int bed = surface + delta;
                for (int y = bed, paved = 0; y > minY && paved < 3; y--) {
                    BlockState s = shifted[y - minY];
                    if (!s.isAir() && s.getFluidState().isEmpty()) {
                        shifted[y - minY] = roadBlock;
                        paved++;
                        changed = true;
                    }
                }
            }
        }
        return changed ? new NoiseColumn(minY, shifted) : column;
    }

    @Override
    public int getFirstOccupiedHeight(int x, int z, Heightmap.Types types, LevelHeightAccessor level, RandomState random) {
        return deformedHeight(x, z,
                delegate.getFirstOccupiedHeight(x, z,
                        geoTarget(x, z) == null ? types : Heightmap.Types.OCEAN_FLOOR, level, random),
                types);
    }

    @Override
    public int getMinY() {
        return delegate.getMinY();
    }

    @Override
    public int getGenDepth() {
        return delegate.getGenDepth();
    }

    @Override
    public int getSeaLevel() {
        return delegate.getSeaLevel();
    }

    @Override
    public int getSpawnHeight(LevelHeightAccessor level) {
        return delegate.getSpawnHeight(level);
    }

    // --- Misc ------------------------------------------------------------------

    @Override
    public WeightedRandomList<MobSpawnSettings.SpawnerData> getMobsAt(Holder<Biome> biome, StructureManager structureManager, MobCategory category, BlockPos pos) {
        return delegate.getMobsAt(biome, structureManager, category, pos);
    }

    @Override
    public void addDebugScreenInfo(List<String> info, RandomState random, BlockPos pos) {
        delegate.addDebugScreenInfo(info, random, pos);
    }
}
