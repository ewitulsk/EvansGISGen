package com.evansgisgen.geoworld.geo;

import com.google.common.cache.CacheBuilder;
import com.google.common.cache.CacheLoader;
import com.google.common.cache.LoadingCache;
import com.google.gson.Gson;
import com.google.gson.JsonObject;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Optional;
import org.jetbrains.annotations.Nullable;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Runtime view of a precompiled {@code .geoworld} dataset directory produced by
 * the offline compiler. A dataset is a {@code manifest.json} plus a
 * {@code tiles/} directory of binary {@link GeoTile} files covering
 * {@code tileSize x tileSize} block columns each.
 *
 * <p>Tiles are immutable and cached; lookups are plain array indexing. When no
 * dataset is present this object is "empty" and every lookup returns absent.
 */
public final class GeoDataset {
    private static final Logger LOGGER = LoggerFactory.getLogger(GeoDataset.class);
    private static final Gson GSON = new Gson();
    private static final int DEFAULT_TILE_SIZE = 256;
    private static final int MAX_CACHED_TILES = 512;

    @Nullable private final Path root;
    private final String name;
    private final int tileCount;
    private final GeoTransform transform;
    private final int tileSize;
    @Nullable private final GeoProjection projection;
    private final LoadingCache<Long, Optional<GeoTile>> tiles;

    private GeoDataset(@Nullable Path root, String name, int tileCount,
            GeoTransform transform, int tileSize,
            @Nullable GeoProjection projection) {
        this.root = root;
        this.name = name;
        this.tileCount = tileCount;
        this.transform = transform;
        this.tileSize = tileSize;
        this.projection = projection;
        this.tiles = CacheBuilder.newBuilder()
                .maximumSize(MAX_CACHED_TILES)
                .build(CacheLoader.from(this::loadTile));
    }

    public static GeoDataset empty() {
        return new GeoDataset(null, "<none>", 0, GeoTransform.DEFAULT,
                DEFAULT_TILE_SIZE, null);
    }

    /**
     * Loads a dataset directory. When {@code root} is null/missing/unreadable
     * the result is an empty dataset carrying the fallback transform.
     */
    public static GeoDataset load(@Nullable Path root, GeoTransform fallbackTransform) {
        if (root == null || !Files.isDirectory(root)) {
            return new GeoDataset(null, "<none>", 0, fallbackTransform, DEFAULT_TILE_SIZE, null);
        }
        Path manifestFile = root.resolve("manifest.json");
        if (!Files.isRegularFile(manifestFile)) {
            LOGGER.warn("Dataset at {} has no manifest.json; ignoring", root);
            return new GeoDataset(null, "<none>", 0, fallbackTransform, DEFAULT_TILE_SIZE, null);
        }
        try {
            JsonObject manifest = GSON.fromJson(Files.readString(manifestFile), JsonObject.class);
            GeoTransform transform = manifest.has("transform")
                    ? GeoWorldConfig.parseTransform(manifest.getAsJsonObject("transform"))
                    : fallbackTransform;
            int tileSize = manifest.has("tile_size") ? manifest.get("tile_size").getAsInt() : DEFAULT_TILE_SIZE;
            String name = manifest.has("name") ? manifest.get("name").getAsString() : root.getFileName().toString();
            int tileCount = manifest.has("tile_count") ? manifest.get("tile_count").getAsInt() : -1;
            GeoProjection projection = null;
            if (manifest.has("projection") && manifest.get("projection").isJsonObject()) {
                JsonObject proj = manifest.getAsJsonObject("projection");
                projection = GeoProjection.parse(
                        proj.has("crs") ? proj.get("crs").getAsString() : null,
                        proj.has("geo_anchor_east_m") ? proj.get("geo_anchor_east_m").getAsDouble() : 0.0,
                        proj.has("geo_anchor_north_m") ? proj.get("geo_anchor_north_m").getAsDouble() : 0.0);
            }
            LOGGER.info("Loaded GeoWorld dataset '{}' from {} ({} tiles)", name, root, tileCount);
            return new GeoDataset(root, name, tileCount, transform, tileSize, projection);
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read dataset manifest {}: {}", manifestFile, e.toString());
            return new GeoDataset(null, "<none>", 0, fallbackTransform, DEFAULT_TILE_SIZE, null);
        }
    }

    public boolean isEmpty() {
        return root == null;
    }

    public String name() {
        return name;
    }

    public int tileCount() {
        return tileCount;
    }

    @Nullable
    public Path root() {
        return root;
    }

    public GeoTransform transform() {
        return transform;
    }

    public int tileSize() {
        return tileSize;
    }

    /** Lat/lon -> geo-meters projection; absent when the manifest has none. */
    public Optional<GeoProjection> projection() {
        return Optional.ofNullable(projection);
    }

    /** Tile-local coordinate of a block position (0..tileSize-1). */
    public int localCoord(int blockCoord) {
        return Math.floorMod(blockCoord, tileSize);
    }

    /** The tile covering block position (x, z), if the dataset has it. */
    public Optional<GeoTile> tileAt(int blockX, int blockZ) {
        if (root == null) {
            return Optional.empty();
        }
        int tx = Math.floorDiv(blockX, tileSize);
        int tz = Math.floorDiv(blockZ, tileSize);
        long key = ((long) tx << 32) | (tz & 0xFFFFFFFFL);
        return tiles.getUnchecked(key);
    }

    /**
     * Geographic influence weight at block column (x, z) in [0, 1].
     *
     * <p>A tile carrying elevation but no explicit influence layer counts as
     * fully geographic; a missing tile or a tile with neither layer is
     * completely vanilla.
     */
    public float influence(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        if (tile == null) {
            return 0.0f;
        }
        if (!tile.hasInfluence()) {
            return tile.hasElevation() ? 1.0f : 0.0f;
        }
        return tile.influenceWeight(localCoord(x), localCoord(z)) / 255.0f;
    }

    /** The dataset's influence layer as a sampled {@link ScalarField}. */
    public ScalarField influenceField() {
        return isEmpty() ? ScalarField.ZERO : this::influence;
    }

    // Cross-tile column lookups — resolve the covering tile (if any) and read
    // the local layer cell. Missing tiles/layers read as "absent" (0).

    public int surfaceClassAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null || !tile.hasSurface()
                ? 0 : tile.surfaceClass(localCoord(x), localCoord(z));
    }

    public int roadClassAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null || !tile.hasRoad()
                ? 0 : tile.roadClass(localCoord(x), localCoord(z));
    }

    public int waterDepthAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null ? 0 : tile.waterDepth(localCoord(x), localCoord(z));
    }

    /** Minecraft Y of the dataset terrain surface; NO_DATA outside tiles. */
    public int elevationAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null || !tile.hasElevation()
                ? GeoTile.NO_DATA
                : tile.elevation(localCoord(x), localCoord(z));
    }

    public int buildingClassAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null ? 0 : tile.buildingClass(localCoord(x), localCoord(z));
    }

    public int buildingLevelsAt(int x, int z) {
        GeoTile tile = tileAt(x, z).orElse(null);
        return tile == null ? 0 : tile.buildingLevels(localCoord(x), localCoord(z));
    }

    private Optional<GeoTile> loadTile(long key) {
        int tx = (int) (key >> 32);
        int tz = (int) (long) key;
        Path file = root.resolve("tiles").resolve(tileFileName(tx, tz));
        if (!Files.isRegularFile(file)) {
            return Optional.empty();
        }
        try {
            return Optional.of(GeoTile.read(file, tileSize));
        } catch (IOException | RuntimeException e) {
            LOGGER.warn("Failed to read tile {}: {}", file, e.toString());
            return Optional.empty();
        }
    }

    static String tileFileName(int tileX, int tileZ) {
        return String.format(Locale.ROOT, "%+05d_%+05d.gwt", tileX, tileZ);
    }

    // Convenience wrappers matching the plan's runtime API.

    public int blockX(double eastMeters) {
        return transform.blockX(eastMeters);
    }

    public int blockZ(double northMeters) {
        return transform.blockZ(northMeters);
    }

    public GeoPoint minecraftToGeo(int x, int z) {
        return transform.geoPoint(x, z);
    }
}
