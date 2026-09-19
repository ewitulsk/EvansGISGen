package com.evansgisgen.geoworld.geo;

/**
 * Runtime view of a precompiled {@code .geoworld} dataset produced by the
 * offline compiler (Phase 2+).
 *
 * <p>Phase 0 stub: no tile data exists yet, so every dataset is empty and the
 * {@link com.evansgisgen.geoworld.worldgen.GeoChunkGenerator} effectively
 * behaves like its vanilla delegate plus a hardcoded proof-of-concept
 * deformation.
 */
public final class GeoDataset {
    private static final GeoDataset EMPTY = new GeoDataset();

    private GeoDataset() {}

    public static GeoDataset empty() {
        return EMPTY;
    }

    public boolean isEmpty() {
        return true;
    }
}
