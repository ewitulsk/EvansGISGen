package com.evansgisgen.geoworld.geo;

/**
 * Runtime view of a precompiled {@code .geoworld} dataset produced by the
 * offline compiler (Phase 2+).
 *
 * <p>Phase 1: carries the {@link GeoTransform} that anchors projected
 * geographic coordinates to Minecraft block coordinates. Tile data still does
 * not exist, so every dataset is "empty" and the
 * {@link com.evansgisgen.geoworld.worldgen.GeoChunkGenerator} falls back to its
 * vanilla delegate plus a hardcoded proof-of-concept deformation.
 */
public final class GeoDataset {
    private final GeoTransform transform;

    public GeoDataset(GeoTransform transform) {
        this.transform = transform;
    }

    public static GeoDataset empty() {
        return new GeoDataset(GeoTransform.DEFAULT);
    }

    public boolean isEmpty() {
        return true;
    }

    public GeoTransform transform() {
        return transform;
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
