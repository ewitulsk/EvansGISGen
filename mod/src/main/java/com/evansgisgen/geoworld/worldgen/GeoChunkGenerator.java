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
public final class GeoChunkGenerator extends ChunkGenerator {
    private static final double FULL_RADIUS = 400.0;
    private static final double EDGE_RADIUS = 500.0;
    private static final int TARGET_HEIGHT = 70;

    private static final BlockState FILL_BLOCK = Blocks.STONE.defaultBlockState();
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();

    private final ChunkGenerator delegate;
    private final GeoDataset dataset;

    public GeoChunkGenerator(ChunkGenerator delegate) {
        this(delegate, GeoWorldMod.dataset());
    }

    public GeoChunkGenerator(ChunkGenerator delegate, GeoDataset dataset) {
        super(delegate.getBiomeSource());
        this.delegate = delegate;
        this.dataset = dataset;
    }

    public ChunkGenerator delegate() {
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

    private record GeoTarget(int height, double weight) {}

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
            double w = tile.hasInfluence() ? tile.influenceWeight(lx, lz) / 255.0 : 1.0;
            return w <= 0.0 ? null : new GeoTarget(tile.elevation(lx, lz), w);
        }
        double w = circleInfluence(x, z);
        return w <= 0.0 ? null : new GeoTarget(TARGET_HEIGHT, w);
    }

    private int deformedHeight(int x, int z, int vanillaHeight) {
        GeoTarget target = geoTarget(x, z);
        return target == null
                ? vanillaHeight
                : (int) Math.round(Mth.lerp(target.weight(), vanillaHeight, target.height()));
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
                if (delta == 0) {
                    continue;
                }

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
                    }
                    if (state != column[y - minY]) {
                        chunk.setBlockState(pos.set(x, y, z), state, false);
                        modified = true;
                    }
                }
            }
        }

        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    private static int surfaceHeight(ChunkAccess chunk, BlockPos.MutableBlockPos pos, int x, int z, int minY, int topY) {
        for (int y = topY; y > minY; y--) {
            if (!chunk.getBlockState(pos.set(x, y, z)).isAir()) {
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
        delegate.applyBiomeDecoration(level, chunk, structureManager);
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
        return deformedHeight(x, z, delegate.getBaseHeight(x, z, type, level, random));
    }

    @Override
    public NoiseColumn getBaseColumn(int x, int z, LevelHeightAccessor level, RandomState random) {
        return delegate.getBaseColumn(x, z, level, random);
    }

    @Override
    public int getFirstOccupiedHeight(int x, int z, Heightmap.Types types, LevelHeightAccessor level, RandomState random) {
        return deformedHeight(x, z, delegate.getFirstOccupiedHeight(x, z, types, level, random));
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
