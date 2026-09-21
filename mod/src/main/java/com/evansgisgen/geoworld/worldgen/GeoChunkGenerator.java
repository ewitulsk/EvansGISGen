package com.evansgisgen.geoworld.worldgen;

import com.evansgisgen.geoworld.GeoWorldMod;
import com.evansgisgen.geoworld.business.BusinessIndex;
import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.landmark.LandmarkIndex;
import com.evansgisgen.geoworld.module.ModuleIndex;
import com.evansgisgen.geoworld.geo.GeoTile;
import com.evansgisgen.geoworld.geo.GeoTransform;
import com.mojang.datafixers.util.Pair;
import com.mojang.serialization.MapCodec;
import java.util.EnumSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Holder;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.HolderSet;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.Registries;
import net.minecraft.data.worldgen.features.TreeFeatures;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.WorldGenRegion;
import net.minecraft.util.Mth;
import net.minecraft.util.RandomSource;
import net.minecraft.util.random.WeightedRandomList;
import net.minecraft.world.entity.MobCategory;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.LevelHeightAccessor;
import net.minecraft.world.level.NoiseColumn;
import net.minecraft.world.level.StructureManager;
import net.minecraft.world.level.WorldGenLevel;
import net.minecraft.world.level.biome.Biome;
import net.minecraft.world.level.biome.BiomeManager;
import net.minecraft.world.level.biome.BiomeSource;
import net.minecraft.world.level.biome.Biomes;
import net.minecraft.world.level.biome.FixedBiomeSource;
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
import net.minecraft.world.level.levelgen.feature.ConfiguredFeature;
import net.minecraft.world.level.levelgen.structure.BoundingBox;
import net.minecraft.world.level.levelgen.structure.Structure;
import net.minecraft.world.level.levelgen.structure.StructureSet;
import net.minecraft.world.level.levelgen.structure.StructureStart;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplateManager;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

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
    private static final Logger LOGGER = LoggerFactory.getLogger(GeoChunkGenerator.class);

    private static final double FULL_RADIUS = 400.0;
    private static final double EDGE_RADIUS = 500.0;
    private static final int TARGET_HEIGHT = 70;

    private static final BlockState FILL_BLOCK = Blocks.STONE.defaultBlockState();
    private static final BlockState DIRT = Blocks.DIRT.defaultBlockState();
    private static final BlockState AIR = Blocks.AIR.defaultBlockState();
    private static final BlockState WATER = Blocks.WATER.defaultBlockState();

    /**
     * Solid "overburden" depth at full geographic influence: cave and aquifer
     * pockets within this many blocks under the deformed surface are filled.
     * The column shift preserves caves, which would otherwise daylight through
     * roads and buildings; deep caves below the band are left intact (scaled
     * by influence so they fade back in across the blend ramp). Real Nebraska
     * ground carries ~15+ m of unconsolidated till/loess over bedrock anyway.
     */
    private static final int OVERBURDEN_M = 16;

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

    // Land-use surface class ids — shared with the compiler (landuse.py).
    static final int SURFACE_NATURAL = 0;
    static final int SURFACE_GRASS = 1;
    static final int SURFACE_FARMLAND = 2;
    static final int SURFACE_FOREST = 3;
    static final int SURFACE_RESIDENTIAL = 4;
    static final int SURFACE_COMMERCIAL = 5;
    static final int SURFACE_INDUSTRIAL = 6;
    static final int SURFACE_PARKING = 7;
    static final int SURFACE_RAILWAY = 8;
    static final int SURFACE_PARK = 9;

    /**
     * Top block each land-use class renders as. Classification stays
     * semantic in the dataset — this theme is the only block mapping, so a
     * different theme could restyle the same world.
     */
    @Nullable
    public static BlockState surfaceBlock(int surfaceClass) {
        return switch (surfaceClass) {
            case SURFACE_GRASS, SURFACE_FOREST, SURFACE_RESIDENTIAL, SURFACE_PARK
                    -> Blocks.GRASS_BLOCK.defaultBlockState();
            case SURFACE_FARMLAND -> Blocks.FARMLAND.defaultBlockState();
            case SURFACE_COMMERCIAL -> Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState();
            case SURFACE_INDUSTRIAL -> Blocks.GRAY_CONCRETE.defaultBlockState();
            case SURFACE_PARKING -> Blocks.GRAY_CONCRETE.defaultBlockState();
            case SURFACE_RAILWAY -> Blocks.GRAVEL.defaultBlockState();
            default -> null;
        };
    }

    // Building class ids — shared with the compiler (buildings.py).
    static final int BUILDING_NONE = 0;
    static final int BUILDING_RESIDENTIAL = 1;
    static final int BUILDING_COMMERCIAL = 2;
    static final int BUILDING_INDUSTRIAL = 3;
    static final int BUILDING_CIVIC = 4;
    static final int BUILDING_OUTBUILDING = 5;
    static final int BUILDING_GENERIC = 6;
    // Phase 16 taxonomy — use-specific classes from building=/POI tags.
    static final int BUILDING_SUPERMARKET = 7;
    static final int BUILDING_RESTAURANT = 8;
    static final int BUILDING_FUEL = 9;
    static final int BUILDING_SCHOOL = 10;
    static final int BUILDING_CHURCH = 11;
    static final int BUILDING_HOSPITAL = 12;
    static final int BUILDING_HOTEL = 13;
    static final int BUILDING_PARKING = 14;
    static final int BUILDING_SPORTS = 15;
    static final int BUILDING_AGRICULTURAL = 16;
    static final int BUILDING_CAR = 17;
    static final int BUILDING_STORAGE = 18;

    // Interior zone ids — shared with the compiler (Phase 20, interior
    // layer). 0 = unzoned.
    static final int ZONE_VESTIBULE = 1;
    static final int ZONE_CHECKOUT = 2;
    static final int ZONE_SELF_CHECKOUT = 3;
    static final int ZONE_GROCERY = 4;
    static final int ZONE_GENERAL = 5;
    static final int ZONE_CLOTHING = 6;
    static final int ZONE_ELECTRONICS = 7;
    static final int ZONE_PHARMACY = 8;
    static final int ZONE_BACKROOM = 9;
    static final int ZONE_CART_STORAGE = 10;

    /**
     * Floor slab material per interior zone (Phase 20). Zones tint the
     * floor so the store reads as departments from inside; circulation
     * areas stay neutral.
     */
    public static BlockState zoneFloor(int zone) {
        return switch (zone) {
            case ZONE_VESTIBULE -> Blocks.SMOOTH_STONE.defaultBlockState();
            case ZONE_CHECKOUT -> Blocks.WHITE_CONCRETE.defaultBlockState();
            case ZONE_SELF_CHECKOUT -> Blocks.LIGHT_BLUE_CONCRETE.defaultBlockState();
            case ZONE_GROCERY -> Blocks.WHITE_CONCRETE.defaultBlockState();
            case ZONE_GENERAL -> Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState();
            case ZONE_CLOTHING -> Blocks.CYAN_CARPET.defaultBlockState();
            case ZONE_ELECTRONICS -> Blocks.BLUE_CARPET.defaultBlockState();
            case ZONE_PHARMACY -> Blocks.WHITE_CARPET.defaultBlockState();
            case ZONE_BACKROOM -> Blocks.GRAY_CONCRETE.defaultBlockState();
            case ZONE_CART_STORAGE -> Blocks.POLISHED_ANDESITE.defaultBlockState();
            default -> Blocks.STONE.defaultBlockState();
        };
    }

    /** Wall/floor/roof materials per building class (Phase 8 theme). */
    public record BuildPalette(BlockState wall, BlockState floor,
                               BlockState roof, BlockState window) {}

    /** Palette for a building class; null for none/unknown. */
    @Nullable
    public static BuildPalette buildingPalette(int buildingClass) {
        return buildingClass > 0 && buildingClass < BUILDING_PALETTES.length
                ? BUILDING_PALETTES[buildingClass] : null;
    }

    private static final BuildPalette[] BUILDING_PALETTES = {
            null,
            new BuildPalette(Blocks.BRICKS.defaultBlockState(),            // residential
                    Blocks.OAK_PLANKS.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.STONE_BRICKS.defaultBlockState(),      // commercial
                    Blocks.STONE.defaultBlockState(),
                    Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.GRAY_CONCRETE.defaultBlockState(),     // industrial
                    Blocks.STONE.defaultBlockState(),
                    Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.QUARTZ_BLOCK.defaultBlockState(),      // civic
                    Blocks.STONE.defaultBlockState(),
                    Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.COBBLESTONE.defaultBlockState(),       // outbuilding
                    Blocks.DIRT.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.BRICKS.defaultBlockState(),            // generic
                    Blocks.STONE.defaultBlockState(),
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            // Phase 16 use-specific palettes — distinct roof/wall accents
            // make the taxonomy legible from the ground.
            new BuildPalette(Blocks.WHITE_CONCRETE.defaultBlockState(),    // supermarket
                    Blocks.STONE.defaultBlockState(),
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.RED_NETHER_BRICKS.defaultBlockState(), // restaurant
                    Blocks.STONE.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.WHITE_CONCRETE.defaultBlockState(),    // fuel
                    Blocks.STONE.defaultBlockState(),
                    Blocks.CYAN_CONCRETE.defaultBlockState(),   // canopy band
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.END_STONE_BRICKS.defaultBlockState(),  // school
                    Blocks.STONE.defaultBlockState(),
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.SANDSTONE.defaultBlockState(),         // church
                    Blocks.STONE.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.WHITE_CONCRETE.defaultBlockState(),    // hospital
                    Blocks.QUARTZ_BLOCK.defaultBlockState(),
                    Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(), // hotel
                    Blocks.QUARTZ_BLOCK.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.GRAY_CONCRETE.defaultBlockState(),     // parking
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.WHITE_CONCRETE.defaultBlockState(),    // sports
                    Blocks.STONE.defaultBlockState(),
                    Blocks.GREEN_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.RED_TERRACOTTA.defaultBlockState(),    // agricultural
                    Blocks.DIRT.defaultBlockState(),
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.LIGHT_GRAY_CONCRETE.defaultBlockState(), // car
                    Blocks.STONE.defaultBlockState(),
                    Blocks.BLUE_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.ORANGE_TERRACOTTA.defaultBlockState(), // storage
                    Blocks.STONE.defaultBlockState(),
                    Blocks.GRAY_CONCRETE.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
    };

    // Phase 18 facade variety — deterministic per-instance variants seeded
    // by buildingId. Roofs/floors stay constant per class so surveys and
    // silhouettes stay consistent; only the walls (facade) change.
    private static final BuildPalette[] RESIDENTIAL_VARIANTS = {
            new BuildPalette(Blocks.BRICKS.defaultBlockState(),            // brick
                    Blocks.OAK_PLANKS.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.WHITE_CONCRETE.defaultBlockState(),    // siding
                    Blocks.OAK_PLANKS.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.END_STONE_BRICKS.defaultBlockState(),  // blonde brick
                    Blocks.OAK_PLANKS.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
    };

    private static final BuildPalette[] OUTBUILDING_VARIANTS = {
            new BuildPalette(Blocks.COBBLESTONE.defaultBlockState(),       // stone shed
                    Blocks.DIRT.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.OAK_PLANKS.defaultBlockState(),        // wood shed
                    Blocks.DIRT.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
            new BuildPalette(Blocks.STONE.defaultBlockState(),             // plain
                    Blocks.DIRT.defaultBlockState(),
                    Blocks.DARK_OAK_PLANKS.defaultBlockState(),
                    Blocks.GLASS.defaultBlockState()),
    };

    /**
     * Palette for a building class + instance (Phase 18): residential and
     * outbuilding classes pick a deterministic facade variant seeded by
     * the footprint id; everything else uses the class palette.
     */
    @Nullable
    public static BuildPalette buildingPalette(int buildingClass, int buildingId) {
        BuildPalette[] variants = buildingClass == BUILDING_RESIDENTIAL
                ? RESIDENTIAL_VARIANTS
                : buildingClass == BUILDING_OUTBUILDING
                        ? OUTBUILDING_VARIANTS : null;
        if (variants != null && buildingId != 0) {
            return variants[Math.floorMod(buildingId, variants.length)];
        }
        return buildingPalette(buildingClass);
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

    /**
     * Lazily-resolved biome source for a GeoWorld level: a fixed plains
     * biome while a dataset is loaded. Vanilla biome noise knows nothing
     * about Nebraska — without this the Big Blue freezes in cold-biome
     * spots and jungles cover downtown. Uniform plains is the closest
     * vanilla biome to real Beatrice land cover; land-use classes provide
     * the actual variety.
     *
     * <p>Resolved lazily: the delegate's multi-noise source holds an
     * unbound parameter list until registries load, so {@code
     * possibleBiomes()} cannot be called during preset decode.
     */
    private volatile BiomeSource geoBiomeSource;

    private BiomeSource geoBiomeSource() {
        BiomeSource src = geoBiomeSource;
        if (src == null) {
            synchronized (this) {
                src = geoBiomeSource;
                if (src == null) {
                    src = delegate.getBiomeSource();
                    if (!dataset.isEmpty()) {
                        for (Holder<Biome> biome : src.possibleBiomes()) {
                            if (biome.is(Biomes.PLAINS)) {
                                src = new FixedBiomeSource(biome);
                                break;
                            }
                        }
                    }
                    geoBiomeSource = src;
                }
            }
        }
        return src;
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
        // Wet road columns carry a paved deck one block above the waterline —
        // it's solid, so every type sees it as the column top.
        int depth = target.waterDepth();
        if (depth > 0) {
            if (isWetRoadColumn(x, z)) {
                h += depth + 1;
            } else if (type != Heightmap.Types.OCEAN_FLOOR
                    && type != Heightmap.Types.OCEAN_FLOOR_WG) {
                h += depth;
            }
        }
        // An elevated deck is a solid slab — every heightmap type sees it
        // as the column top, and surface queries must not target the
        // underpass.
        int deck = dataset.roadDeckYAt(x, z);
        if (deck != GeoTile.NO_DATA && deck > h) {
            h = deck;
        }
        return h;
    }

    /** True where a compiled road crosses water — paveRoads decks these. */
    private boolean isWetRoadColumn(int x, int z) {
        GeoTile tile = dataset.tileAt(x, z).orElse(null);
        return tile != null && tile.hasRoad()
                && tile.roadClass(dataset.localCoord(x), dataset.localCoord(z)) != ROAD_NONE;
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

                // Overburden: seal caves/aquifers in the band under the
                // deformed surface (the bed on wet columns — river water sits
                // above it and is never touched). Dirt for the upper few
                // meters so exposed cave ceilings read as subsoil.
                int overburden = (int) Math.ceil(target.weight() * OVERBURDEN_M);
                int top = surface + delta;
                for (int y = top - 1; y >= top - overburden && y > minY; y--) {
                    BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                    if (cur.isAir() || !cur.getFluidState().isEmpty()) {
                        chunk.setBlockState(pos.set(x, y, z),
                                y >= top - 3 ? DIRT : FILL_BLOCK, false);
                        modified = true;
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
        BiomeSource source = geoBiomeSource();
        if (source == delegate.getBiomeSource()) {
            return delegate.createBiomes(randomState, blender, structureManager, chunk);
        }
        // Fill biome cells from OUR biome source (fixed plains while a
        // dataset is loaded) — the delegate would write its own vanilla
        // noise biomes instead. The source is climate-independent, so the
        // random state's sampler suffices and no NoiseChunk is needed.
        chunk.fillBiomesFromNoise(source, randomState.sampler());
        return CompletableFuture.completedFuture(chunk);
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

    /**
     * True if any block column inside the chunk carries geographic influence.
     * Stride-8 sampling is enough: claims are regions/corridors hundreds of
     * meters wide — an 8 m sampling gap can't hide one.
     */
    private boolean chunkClaimed(ChunkPos pos) {
        if (dataset.isEmpty()) {
            return false;
        }
        for (int lx = 0; lx < 16; lx += 8) {
            for (int lz = 0; lz < 16; lz += 8) {
                if (dataset.influence(pos.getBlockX(lx), pos.getBlockZ(lz)) > 0.0f) {
                    return true;
                }
            }
        }
        return false;
    }

    /**
     * True if a structure bounding box reaches any claimed column. Sampled on
     * an 8 m grid over the box's footprint (clamped into the box at the end
     * points) — villages/shipwrecks are tens of meters across.
     */
    private boolean boxTouchesClaim(BoundingBox box) {
        for (int x = box.minX(); x <= box.maxX(); x += 8) {
            for (int z = box.minZ(); z <= box.maxZ(); z += 8) {
                if (dataset.influence(x, z) > 0.0f) {
                    return true;
                }
            }
        }
        return dataset.influence(box.maxX(), box.maxZ()) > 0.0f;
    }

    @Override
    public void applyCarvers(WorldGenRegion level, long seed, RandomState random, BiomeManager biomeManager, StructureManager structureManager, ChunkAccess chunk, GenerationStep.Carving step) {
        // Skip carving entirely on claimed chunks: ravines/canyons slashing
        // through streets read as damage, not terrain. Density caves below
        // the overburden band still exist — deformChunk only seals the top.
        if (!chunkClaimed(chunk.getPos())) {
            delegate.applyCarvers(level, seed, random, biomeManager, structureManager, chunk, step);
        }
    }

    @Override
    public void applyBiomeDecoration(WorldGenLevel level, ChunkAccess chunk, StructureManager structureManager) {
        // Geographic passes run before vanilla decoration: land-use surfaces
        // and building shells replace the top terrain, then roads are paved
        // so vegetation cannot plant on them.
        LandmarkIndex landmarks = landmarks(level.registryAccess(),
                com.evansgisgen.geoworld.studio.StudioService.overlayRoot(
                        level.getLevel()));
        applySurface(chunk);
        buildBuildings(chunk, landmarks,
                businesses(level.registryAccess()));
        paveRoads(chunk);
        delegate.applyBiomeDecoration(level, chunk, structureManager);
        plantForest(level, chunk);
        // Decoration can still deposit cover (snow layers) on pavement, and
        // already-decorated neighbors may have grown trees into road or
        // building cells — clear anything that isn't terrain or structure.
        clearCover(chunk);
        // Street furniture: sign posts from the compiled signs.json
        // sidecar (Phase 13) — before landmarks so a curated build wins.
        // Business pylons may stand on parking-apron cells that the
        // generic ground rule refuses, so they get their own resolver.
        com.evansgisgen.geoworld.sign.SignPlacer.place(signs(), level, chunk,
                p -> signGround(chunk, p.getX(), p.getZ()),
                p -> signGroundLoose(chunk, p.getX(), p.getZ()));
        // Curated .nbt landmarks place last, sliced to this chunk's bounds
        // (Phase 9) — nothing else may overwrite them.
        landmarks.placeChunk(level, chunk, GeoChunkGenerator::isFeatureOverhang);
        // Interior modules (Phase 21) stamp inside business shells once
        // shells and floors exist — never inside a landmark's claim.
        ModuleIndex modules = businesses(level.registryAccess()).isEmpty()
                ? ModuleIndex.EMPTY : modules(level.registryAccess());
        if (!modules.isEmpty()) {
            modules.placeChunk(level, chunk,
                    p -> interiorFloorY(chunk, p.getX(), p.getZ(),
                            landmarks));
        }
    }

    /**
     * Lazily resolves the dataset's businesses.json sidecar (Phase 19) —
     * needs the block registry, which isn't available at generator
     * construction.
     */
    private volatile BusinessIndex businessIndex;

    public BusinessIndex businesses(RegistryAccess registryAccess) {
        BusinessIndex index = businessIndex;
        if (index == null) {
            synchronized (this) {
                index = businessIndex;
                if (index == null) {
                    index = dataset.isEmpty()
                            ? BusinessIndex.EMPTY
                            : BusinessIndex.load(dataset,
                                    registryAccess.lookupOrThrow(Registries.BLOCK));
                    businessIndex = index;
                }
            }
        }
        return index;
    }

    /** Lazily resolves the dataset's modules.json sidecar (Phase 21). */
    private volatile ModuleIndex moduleIndex;

    public ModuleIndex modules(RegistryAccess registryAccess) {
        ModuleIndex index = moduleIndex;
        if (index == null) {
            synchronized (this) {
                index = moduleIndex;
                if (index == null) {
                    index = dataset.isEmpty()
                            ? ModuleIndex.EMPTY
                            : ModuleIndex.load(dataset,
                                    registryAccess.lookupOrThrow(Registries.BLOCK));
                    moduleIndex = index;
                }
            }
        }
        return index;
    }

    /**
     * Floor-slab Y at a column — the same per-instance solve
     * {@link #buildBuildings} uses; returns minY when no building or a
     * landmark claim covers the column so stray placements are skipped.
     */
    private int interiorFloorY(ChunkAccess chunk, int x, int z,
            LandmarkIndex landmarks) {
        if (landmarks.suppressesBuildingAt(x, z)) {
            return getMinY();  // a landmark claims this spot
        }
        GeoTile tile = dataset.tileAt(x, z).orElse(null);
        if (tile == null) {
            return getMinY();
        }
        int tlx = dataset.localCoord(x);
        int tlz = dataset.localCoord(z);
        if (tile.buildingClass(tlx, tlz) == BUILDING_NONE) {
            return getMinY();
        }
        int compiledRoof = tile.buildingRoofY(tlx, tlz);
        if (compiledRoof == GeoTile.NO_DATA) {
            return getMinY();
        }
        int levels = Math.max(1, tile.buildingLevels(tlx, tlz));
        int topY = getMinY() + getGenDepth() - 1;
        int roof = Math.min(topY, compiledRoof);
        return Math.max(getMinY() + 1, roof - levels * 3 - 1);
    }

    /**
     * Lazily resolves the dataset's landmarks.json + .nbt templates — needs
     * the block registry, which isn't available at generator construction.
     */
    private volatile LandmarkIndex landmarkIndex;
    @Nullable private volatile java.nio.file.Path overlayRoot;

    public LandmarkIndex landmarks(RegistryAccess registryAccess) {
        return landmarks(registryAccess, null);
    }

    /**
     * Resolves the dataset's landmarks plus the world-save overlay directory
     * (studio-saved lots). The overlay path is discovered once from the
     * decorating level and retained for reloads.
     */
    public LandmarkIndex landmarks(RegistryAccess registryAccess,
            @Nullable java.nio.file.Path overlay) {
        if (overlay != null) {
            overlayRoot = overlay;
        }
        LandmarkIndex index = landmarkIndex;
        if (index == null) {
            synchronized (this) {
                index = landmarkIndex;
                if (index == null) {
                    index = dataset.isEmpty()
                            ? LandmarkIndex.EMPTY
                            : LandmarkIndex.load(dataset,
                                    registryAccess.lookupOrThrow(Registries.BLOCK),
                                    overlayRoot);
                    landmarkIndex = index;
                }
            }
        }
        return index;
    }

    /** Drops the cached index so the next access re-reads overlay files. */
    public void reloadLandmarks(RegistryAccess registryAccess,
            java.nio.file.Path overlay) {
        overlayRoot = overlay;
        landmarkIndex = null;
        landmarks(registryAccess, overlay);
    }

    /** Lazily-resolved signs.json sidecar (Phase 13 street furniture). */
    private volatile com.evansgisgen.geoworld.sign.SignIndex signIndex;

    public com.evansgisgen.geoworld.sign.SignIndex signs() {
        com.evansgisgen.geoworld.sign.SignIndex index = signIndex;
        if (index == null) {
            synchronized (this) {
                index = signIndex;
                if (index == null) {
                    index = dataset.isEmpty()
                            ? com.evansgisgen.geoworld.sign.SignIndex.EMPTY
                            : com.evansgisgen.geoworld.sign.SignIndex.load(dataset);
                    signIndex = index;
                }
            }
        }
        return index;
    }

    /**
     * Ground Y for a sign post — the terrain surface at (x, z), refusing
     * spots that are water, paved, decked, or inside a footprint.
     */
    private int signGround(ChunkAccess chunk, int x, int z) {
        GeoTile tile = dataset.tileAt(x, z).orElse(null);
        if (tile != null) {
            int tlx = dataset.localCoord(x);
            int tlz = dataset.localCoord(z);
            // Sidewalk cells are fine — blades stand on the walk. Travel
            // lanes, water, decks and building cells refuse a post.
            int rc = tile.roadClass(tlx, tlz);
            if (tile.waterDepth(tlx, tlz) > 0
                    || (rc != ROAD_NONE && rc != ROAD_SIDEWALK)
                    || tile.roadDeckY(tlx, tlz) != GeoTile.NO_DATA
                    || tile.buildingClass(tlx, tlz) != BUILDING_NONE) {
                return getMinY();  // unplaceable — skip
            }
        }
        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        return groundHeight(chunk, new BlockPos.MutableBlockPos(), x, z, minY, topY);
    }

    /**
     * Ground Y for a business pylon (Phase 19) — looser than
     * {@link #signGround}: pylons are meant to stand on parking aprons
     * and road verges, so only water, decks and footprint cells refuse.
     */
    private int signGroundLoose(ChunkAccess chunk, int x, int z) {
        GeoTile tile = dataset.tileAt(x, z).orElse(null);
        if (tile != null) {
            int tlx = dataset.localCoord(x);
            int tlz = dataset.localCoord(z);
            if (tile.waterDepth(tlx, tlz) > 0
                    || tile.roadDeckY(tlx, tlz) != GeoTile.NO_DATA
                    || tile.buildingClass(tlx, tlz) != BUILDING_NONE) {
                return getMinY();  // unplaceable — skip
            }
        }
        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        return groundHeight(chunk, new BlockPos.MutableBlockPos(), x, z, minY, topY);
    }

    /**
     * Replaces the top terrain block of classified land-use columns with the
     * class's surface block (Phase 7). Water/road/building columns are
     * skipped — the road and building passes own those cells.
     */
    private void applySurface(ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || !tile.hasSurface()) {
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
                int cls = tile.surfaceClass(tlx, tlz);
                if (cls == SURFACE_NATURAL) {
                    continue;
                }
                if (tile.waterDepth(tlx, tlz) > 0
                        || tile.roadClass(tlx, tlz) != ROAD_NONE
                        || tile.roadDeckY(tlx, tlz) != GeoTile.NO_DATA
                        || tile.buildingClass(tlx, tlz) != BUILDING_NONE) {
                    continue;
                }
                BlockState block = surfaceBlock(cls);
                if (block == null) {
                    continue;
                }
                int surface = groundHeight(chunk, pos, x, z, minY, topY);
                if (surface <= minY) {
                    continue;
                }
                if (chunk.getBlockState(pos.set(x, surface, z)) != block) {
                    chunk.setBlockState(pos, block, false);
                    modified = true;
                }
            }
        }
        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    /** Wall blocks below ground double as foundation fill under the footprint. */
    private static final int FOUNDATION_DEPTH = 2;

    /**
     * Raises simple hollow building shells on compiled footprints (Phase 8):
     * floor slab on the terrain top, perimeter walls with a window pattern
     * up to {@code levels * 3 + 1} above ground, and a flat roof across the
     * whole footprint. Boundary detection uses dataset lookups so walls on
     * tile/chunk borders stay consistent. Water/road cells are skipped, as
     * are cells a landmark claims (Phase 9 — no shell under a curated build).
     */
    private void buildBuildings(ChunkAccess chunk, LandmarkIndex landmarks,
            BusinessIndex businesses) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || !tile.hasBuilding()) {
            return;
        }
        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        // Phase 20: business entrances get carved doorways — a player can
        // always walk in off the street.
        List<BusinessIndex.Entrance> doors =
                businesses.entrancesInChunk(chunkPos.x, chunkPos.z);
        boolean modified = false;
        for (int lx = 0; lx < 16; lx++) {
            for (int lz = 0; lz < 16; lz++) {
                int x = chunkPos.getBlockX(lx);
                int z = chunkPos.getBlockZ(lz);
                int tlx = dataset.localCoord(x);
                int tlz = dataset.localCoord(z);
                int cls = tile.buildingClass(tlx, tlz);
                if (cls == BUILDING_NONE || cls >= BUILDING_PALETTES.length) {
                    continue;
                }
                if (tile.waterDepth(tlx, tlz) > 0
                        || tile.roadClass(tlx, tlz) != ROAD_NONE
                        || tile.roadDeckY(tlx, tlz) != GeoTile.NO_DATA
                        || landmarks.suppressesBuildingAt(x, z)) {
                    continue;
                }
                int ground = groundHeight(chunk, pos, x, z, minY, topY);
                if (ground <= minY) {
                    continue;
                }
                int id = tile.buildingId(tlx, tlz);
                BuildPalette palette = buildingPalette(cls, id);
                // Phase 19: a known business repaints its shell — the
                // facade palette overrides the class/instance palette
                // per-component, so partial palettes inherit sensibly.
                int bizId = tile.business(tlx, tlz);
                BusinessIndex.Facade facade = bizId != 0
                        ? businesses.facade(bizId) : null;
                int zone = tile.interiorZone(tlx, tlz);
                int levels = Math.max(1, tile.buildingLevels(tlx, tlz));
                // Phase 15: one roof height per instance, solved by the
                // compiler from the footprint's highest ground. On slopes
                // the old per-cell roof stepped with the terrain and tore;
                // now `base` is the instance's uniform first-floor level
                // and `roof` its single flat top. Legacy datasets without
                // the roof layer fall back to the per-cell formula.
                int compiledRoof = tile.buildingRoofY(tlx, tlz);
                int roof;
                int base;
                if (compiledRoof != GeoTile.NO_DATA) {
                    roof = Math.min(topY, compiledRoof);
                    base = Math.max(minY + 1, roof - levels * 3 - 1);
                } else {
                    base = ground;
                    roof = Math.min(topY, ground + levels * 3 + 1);
                }

                // Instance-aware edges (Phase 15): a wall wherever the
                // neighbor is a different footprint — abutting row
                // buildings get party walls instead of merging into one
                // blob. Without the id layer, keep the old class-presence
                // boundary test.
                boolean edge;
                if (id != 0) {
                    edge = dataset.buildingIdAt(x + 1, z) != id
                            || dataset.buildingIdAt(x - 1, z) != id
                            || dataset.buildingIdAt(x, z + 1) != id
                            || dataset.buildingIdAt(x, z - 1) != id;
                } else {
                    edge = dataset.buildingClassAt(x + 1, z) == BUILDING_NONE
                            || dataset.buildingClassAt(x - 1, z) == BUILDING_NONE
                            || dataset.buildingClassAt(x, z + 1) == BUILDING_NONE
                            || dataset.buildingClassAt(x, z - 1) == BUILDING_NONE;
                }
                if (edge) {
                    // Doorway: wall columns within ~2 blocks of a solved
                    // entrance open for three courses above the floor.
                    boolean doorway = false;
                    if (facade != null) {
                        for (BusinessIndex.Entrance ent : doors) {
                            if (Math.abs(ent.x() - x) <= 2
                                    && Math.abs(ent.z() - z) <= 2
                                    && (ent.x() - x) * (ent.x() - x)
                                            + (ent.z() - z) * (ent.z() - z)
                                            <= 4) {
                                doorway = true;
                                break;
                            }
                        }
                    }
                    for (int y = base - FOUNDATION_DEPTH; y < roof; y++) {
                        if (y <= minY) {
                            continue;
                        }
                        BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                        if (y < base) {
                            // Foundation: only fill voids, never dig terrain.
                            if (cur.isAir() || !cur.getFluidState().isEmpty()) {
                                chunk.setBlockState(pos, palette.wall(), false);
                            }
                            continue;
                        }
                        if (doorway && y > base && y <= base + 3) {
                            chunk.setBlockState(pos, AIR, false);
                            continue;
                        }
                        // Window pattern: glass on a diagonal grid through the
                        // mid-wall rows; ground row and top row stay solid.
                        boolean window = y > base && y < roof - 1
                                && Math.floorMod(x + z, 3) == 0;
                        BlockState wallMat = palette.wall();
                        BlockState windowMat = palette.window();
                        if (facade != null) {
                            if (facade.wall() != null) {
                                wallMat = facade.wall();
                            }
                            if (facade.window() != null) {
                                windowMat = facade.window();
                            }
                            // Accent band: "parapet" paints the top two
                            // wall courses — the Walmart blue stripe.
                            if (facade.accent() != null
                                    && "parapet".equals(facade.accentY())
                                    && y >= roof - 2) {
                                wallMat = facade.accent();
                                windowMat = facade.accent();
                            }
                        }
                        chunk.setBlockState(pos,
                                window ? windowMat : wallMat, false);
                    }
                } else {
                    // Interior: one floor slab per instance — zoned floors
                    // for layout businesses (Phase 20), else the facade/
                    // class floor. Air below the slab (downhill side)
                    // fills as crawlspace.
                    BlockState floorMat = zone != 0
                            ? zoneFloor(zone)
                            : (facade != null && facade.floor() != null
                                    ? facade.floor() : palette.floor());
                    chunk.setBlockState(pos.set(x, base, z), floorMat, false);
                    for (int y = ground + 1; y < base; y++) {
                        BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                        if (cur.isAir() || !cur.getFluidState().isEmpty()) {
                            chunk.setBlockState(pos, palette.wall(), false);
                        }
                    }
                }
                // Phase 18: pitched caps on house-scale classes. Gables
                // rise half a block per meter of distance from the nearer
                // eave along the ridge's perpendicular axis; hips take
                // the minimum of all four directions (a tent). The shape
                // is seeded by the instance id.
                int cap = 0;
                if (id != 0 && GABLE_CLASSES.contains(cls)) {
                    int dE = scanIdDistance(x, z, 1, 0, id);
                    int dW = scanIdDistance(x, z, -1, 0, id);
                    int dN = scanIdDistance(x, z, 0, -1, id);
                    int dS = scanIdDistance(x, z, 0, 1, id);
                    int eave = switch (Math.floorMod(id, 3)) {
                        case 0 -> Math.min(dE, dW);   // gable, ridge N-S
                        case 1 -> Math.min(dN, dS);   // gable, ridge E-W
                        default -> Math.min(Math.min(dE, dW),
                                Math.min(dN, dS));    // hip (tent)
                    };
                    cap = Math.min(eave / GABLE_RISE_DIV, GABLE_MAX_RISE);
                }
                BlockState roofMat = facade != null && facade.roof() != null
                        ? facade.roof() : palette.roof();
                for (int y = roof; y <= roof + cap && y <= topY; y++) {
                    chunk.setBlockState(pos.set(x, y, z), roofMat, false);
                }
                modified = true;
            }
        }
        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    /** Chance of a tree per forest column — sparse grove density. */
    private static final int FOREST_TREE_DENOMINATOR = 28;

    /** House-scale classes that get pitched gable caps (Phase 18). */
    private static final Set<Integer> GABLE_CLASSES = Set.of(
            BUILDING_RESIDENTIAL, BUILDING_OUTBUILDING,
            BUILDING_AGRICULTURAL, BUILDING_CHURCH);
    /** Blocks of cap rise per block of eave distance (a ~27° gable). */
    private static final int GABLE_RISE_DIV = 2;
    /** Max cap rise above the flat baseline — read by the survey. */
    public static final int GABLE_MAX_RISE = 6;
    private static final int GABLE_SCAN_LIMIT = 48;

    /**
     * Steps from (x, z) toward (dx, dz) until a cell whose building id
     * differs — the distance to the nearer eave of this footprint's gable
     * profile. Capped at {@link #GABLE_SCAN_LIMIT}.
     */
    private int scanIdDistance(int x, int z, int dx, int dz, int id) {
        for (int d = 1; d <= GABLE_SCAN_LIMIT; d++) {
            if (dataset.buildingIdAt(x + dx * d, z + dz * d) != id) {
                return d;
            }
        }
        return GABLE_SCAN_LIMIT;
    }

    /**
     * Plants deterministic oak trees on FOREST-classified columns (Phase 7).
     * The fixed plains biome carries almost no trees, so woods need their
     * own placement. Placement skips roads, water and building cells.
     */
    private void plantForest(WorldGenLevel level, ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || !tile.hasSurface()) {
            return;
        }
        Holder<ConfiguredFeature<?, ?>> oak = level.registryAccess()
                .lookupOrThrow(Registries.CONFIGURED_FEATURE)
                .get(TreeFeatures.OAK).orElse(null);
        if (oak == null) {
            return;
        }
        int minY = getMinY();
        int topY = minY + getGenDepth() - 1;
        long seed = level.getSeed();
        BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos();
        RandomSource random = RandomSource.create();
        for (int lx = 0; lx < 16; lx++) {
            for (int lz = 0; lz < 16; lz++) {
                int x = chunkPos.getBlockX(lx);
                int z = chunkPos.getBlockZ(lz);
                int tlx = dataset.localCoord(x);
                int tlz = dataset.localCoord(z);
                if (tile.surfaceClass(tlx, tlz) != SURFACE_FOREST) {
                    continue;
                }
                if (tile.waterDepth(tlx, tlz) > 0
                        || tile.roadClass(tlx, tlz) != ROAD_NONE
                        || tile.roadDeckY(tlx, tlz) != GeoTile.NO_DATA
                        || tile.buildingClass(tlx, tlz) != BUILDING_NONE) {
                    continue;
                }
                random.setSeed(seed ^ (x * 341873128712L + z * 132897987541L));
                if (random.nextInt(FOREST_TREE_DENOMINATOR) != 0) {
                    continue;
                }
                int ground = groundHeight(chunk, pos, x, z, minY, topY);
                BlockState soil = chunk.getBlockState(pos.set(x, ground, z));
                if (!soil.is(Blocks.GRASS_BLOCK) && !soil.is(Blocks.DIRT)) {
                    continue;
                }
                oak.value().place(level, this, random, pos.set(x, ground + 1, z));
            }
        }
    }

    /** Structural under-block of an elevated deck. */
    private static final BlockState DECK_UNDER = Blocks.SMOOTH_STONE.defaultBlockState();
    /** Guardrail along elevated deck edges. */
    private static final BlockState GUARDRAIL = Blocks.STONE_BRICK_WALL.defaultBlockState();
    /** Support pier material under elevated decks. */
    private static final BlockState PIER = Blocks.STONE_BRICKS.defaultBlockState();
    /** Pier spacing lattice (blocks) under elevated decks. */
    private static final int PIER_SPACING = 8;

    /**
     * Replaces the top solid blocks of every compiled road column with the
     * class's surface block. The DEM already holds the pavement elevation,
     * so no carving is needed. Where a road crosses water the channel bed
     * is carved below grade, so instead of skipping the cell we pave a
     * deck one block above the waterline (bed + depth + 1 ≈ road grade):
     * a flat culvert/bridge slab with the water kept underneath.
     *
     * <p>Columns carrying a compiled {@code roadz} deck (Phase 13 bridges
     * and overpasses) additionally render a two-block slab at the deck's
     * solved height, guardrail walls along deck edges, and pier supports
     * on a deterministic lattice where the gap below is tall enough. The
     * space under a deck is left open — underpasses and river water stay
     * real.
     */
    private void paveRoads(ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        // Chunks never straddle tiles (256 % 16 == 0), so one lookup covers
        // the whole chunk.
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || (!tile.hasRoad() && !tile.hasRoadDeck())) {
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
                int deckY = tile.roadDeckY(tlx, tlz);
                // At-grade work runs BEFORE the deck lands in this column:
                // groundHeight would otherwise measure the new slab and
                // repave over it.
                if (road != ROAD_NONE) {
                    if (tile.waterDepth(tlx, tlz) > 0) {
                        if (deckY == GeoTile.NO_DATA) {
                            // Deck one block above the column's actual
                            // waterline (bed + depth ≈ road grade at full
                            // influence; the scan stays consistent under
                            // blending). A compiled roadz deck supersedes
                            // this fallback — no culvert under a real span.
                            for (int y = topY; y > minY; y--) {
                                BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                                if (cur.isAir()) {
                                    continue;
                                }
                                if (!cur.getFluidState().isEmpty()
                                        && y + 1 <= topY) {
                                    chunk.setBlockState(pos.set(x, y + 1, z),
                                                        roadBlock(road), false);
                                    modified = true;
                                }
                                break;
                            }
                        }
                    } else {
                        int surface = groundHeight(chunk, pos, x, z, minY, topY);
                        if (surface > minY) {
                            BlockState roadBlock = roadBlock(road);
                            for (int y = surface; y > surface - 3 && y > minY; y--) {
                                BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                                if (!cur.isAir() && cur.getFluidState().isEmpty()
                                        && cur != roadBlock) {
                                    chunk.setBlockState(pos.set(x, y, z), roadBlock, false);
                                    modified = true;
                                }
                            }
                        }
                    }
                }
                if (deckY != GeoTile.NO_DATA) {
                    modified |= paveDeck(chunk, pos, x, z, deckY,
                            tile.roadDeckClass(tlx, tlz), minY, topY);
                }
            }
        }

        if (modified) {
            Heightmap.primeHeightmaps(chunk, EnumSet.allOf(Heightmap.Types.class));
        }
    }

    /**
     * Renders one elevated deck column: surface block at {@code deckY},
     * structural under-block beneath it, a guardrail where a 4-neighbour
     * lacks a deck (the deck edge), and a pier on a regular lattice where
     * the gap to the ground is at least three blocks. Returns true when
     * the column was modified.
     */
    private boolean paveDeck(ChunkAccess chunk, BlockPos.MutableBlockPos pos,
            int x, int z, int deckY, int deckClass, int minY, int topY) {
        int ground = groundHeight(chunk, pos, x, z, minY, topY);
        if (deckY <= ground + 1 || deckY > topY) {
            return false;  // deck resolved to grade — nothing to float
        }
        BlockState surface = roadBlock(deckClass != ROAD_NONE ? deckClass : ROAD_ASPHALT);
        chunk.setBlockState(pos.set(x, deckY, z), surface, false);
        chunk.setBlockState(pos.set(x, deckY - 1, z), DECK_UNDER, false);
        if (deckY + 1 <= topY && (dataset.roadDeckYAt(x + 1, z) == GeoTile.NO_DATA
                || dataset.roadDeckYAt(x - 1, z) == GeoTile.NO_DATA
                || dataset.roadDeckYAt(x, z + 1) == GeoTile.NO_DATA
                || dataset.roadDeckYAt(x, z - 1) == GeoTile.NO_DATA)) {
            chunk.setBlockState(pos.set(x, deckY + 1, z), GUARDRAIL, false);
        }
        if (deckY - ground >= 3
                && Math.floorMod(x, PIER_SPACING) == 0
                && Math.floorMod(z, PIER_SPACING) == 0) {
            for (int y = ground + 1; y < deckY - 1; y++) {
                chunk.setBlockState(pos.set(x, y, z), PIER, false);
            }
        }
        return true;
    }

    /**
     * Removes decoration output sitting on paved cells (snow layers and
     * other replaceable cover deposited by the freeze step, plus trunk/leaf
     * intrusions written by neighbors that decorated before this chunk
     * paved) and clears vegetation out of building footprints — shell
     * blocks are never vegetation, so the full column is safe to scrub.
     * Terrain overhangs are left alone.
     */
    private void clearCover(ChunkAccess chunk) {
        if (dataset.isEmpty()) {
            return;
        }
        ChunkPos chunkPos = chunk.getPos();
        GeoTile tile = dataset.tileAt(chunkPos.getMinBlockX(), chunkPos.getMinBlockZ()).orElse(null);
        if (tile == null || (!tile.hasRoad() && !tile.hasBuilding()
                && !tile.hasRoadDeck())) {
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
                int deckY = tile.roadDeckY(tlx, tlz);
                if (deckY != GeoTile.NO_DATA) {
                    // Elevated deck: only cover above the slab is cleared —
                    // the underpass (and its trees) stays intact.
                    for (int y = deckY + 1; y <= topY; y++) {
                        BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                        if (isFeatureOverhang(cur)) {
                            chunk.setBlockState(pos.set(x, y, z), AIR, false);
                            modified = true;
                        }
                    }
                    continue;
                }
                if (tile.waterDepth(tlx, tlz) > 0) {
                    // Wet road cells carry a deck — clear any bank-side
                    // canopy overhanging it. Other wet cells are left alone.
                    if (tile.roadClass(tlx, tlz) != ROAD_NONE) {
                        for (int y = topY; y > minY; y--) {
                            BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                            if (isFeatureOverhang(cur)) {
                                chunk.setBlockState(pos, AIR, false);
                                modified = true;
                            } else if (!cur.isAir()) {
                                break;  // reached the deck or waterline
                            }
                        }
                    }
                    continue;
                }
                if (tile.buildingClass(tlx, tlz) != BUILDING_NONE) {
                    // Inside a shell nothing vegetation is legitimate —
                    // scrub the whole column.
                    for (int y = minY + 1; y <= topY; y++) {
                        BlockState cur = chunk.getBlockState(pos.set(x, y, z));
                        if (isFeatureOverhang(cur)) {
                            chunk.setBlockState(pos, AIR, false);
                            modified = true;
                        }
                    }
                    continue;
                }
                if (tile.roadClass(tlx, tlz) == ROAD_NONE) {
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
    public static boolean isFeatureOverhang(BlockState state) {
        return state.canBeReplaced()
                || state.is(net.minecraft.tags.BlockTags.LEAVES)
                || state.is(net.minecraft.tags.BlockTags.LOGS)
                || state.is(net.minecraft.tags.BlockTags.FLOWERS);
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
        if (dataset.isEmpty()) {
            return;
        }
        // Real geography owns this ground — vanilla structures never belong
        // inside it (plains-biome noise still lets shipwrecks "find" ocean
        // under Nebraska, and villages get sliced by the deformation). A
        // start whose bounding box touches ANY claimed cell is invalidated
        // whole, so a village centered just outside can't leave floating
        // houses at the city edge. getAllStarts() is unmodifiable; writing
        // INVALID_START is the supported suppress — every consumer checks
        // isValid().
        for (Map.Entry<Structure, StructureStart> entry : chunk.getAllStarts().entrySet()) {
            StructureStart start = entry.getValue();
            if (start.isValid() && boxTouchesClaim(start.getBoundingBox())) {
                LOGGER.debug("Suppressing {} at {}: reaches into claimed ground",
                        entry.getKey(), chunk.getPos());
                chunk.setStartForStructure(entry.getKey(), StructureStart.INVALID_START);
            }
        }
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
        // performs during decoration); wet road columns report the deck.
        GeoTile roadTile = dataset.tileAt(x, z).orElse(null);
        if (roadTile != null && roadTile.hasRoad()) {
            int road = roadTile.roadClass(dataset.localCoord(x), dataset.localCoord(z));
            if (road != ROAD_NONE) {
                BlockState roadBlock = roadBlock(road);
                int bed = surface + delta;
                if (depth > 0) {
                    int deck = bed + depth + 1;
                    if (deck <= topY) {
                        shifted[deck - minY] = roadBlock;
                        changed = true;
                    }
                } else {
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
        }
        // A compiled elevated deck reports its slab + under-block at the
        // solved height (same writes paveDeck performs at decoration).
        if (roadTile != null && roadTile.hasRoadDeck()) {
            int deckY = roadTile.roadDeckY(dataset.localCoord(x),
                    dataset.localCoord(z));
            if (deckY != GeoTile.NO_DATA && deckY - 1 > minY && deckY <= topY) {
                int cls = roadTile.roadDeckClass(dataset.localCoord(x),
                        dataset.localCoord(z));
                shifted[deckY - minY] =
                        roadBlock(cls != ROAD_NONE ? cls : ROAD_ASPHALT);
                shifted[deckY - 1 - minY] = DECK_UNDER;
                changed = true;
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
