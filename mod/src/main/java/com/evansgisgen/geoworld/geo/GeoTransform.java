package com.evansgisgen.geoworld.geo;

/**
 * Maps between projected geographic coordinates (meters east/north of the
 * projection origin) and Minecraft block coordinates.
 *
 * <p>Axis convention: geographic {@code +east} maps to Minecraft {@code +x},
 * and geographic {@code +north} maps to Minecraft {@code -z} (Minecraft's +z
 * axis points south).
 *
 * <p>Horizontal and vertical scales are independent so Nebraska's subtle relief
 * can be exaggerated vertically without distorting street geometry. Vertical
 * positions additionally need a datum: the real-world elevation that maps to a
 * chosen Minecraft Y (e.g. local ground level -> sea level).
 *
 * <p>Pure math only — no Minecraft types — so it can be unit tested without a
 * game runtime.
 */
public final class GeoTransform {
    public static final GeoTransform DEFAULT = new GeoTransform(0, 0, 1.0, 1.0, 0.0, 64);

    private final int originX;
    private final int originZ;
    private final double horizontalMetersPerBlock;
    private final double verticalMetersPerBlock;
    private final double datumElevationMeters;
    private final int datumY;

    public GeoTransform(int originX, int originZ, double horizontalMetersPerBlock, double verticalMetersPerBlock,
            double datumElevationMeters, int datumY) {
        if (horizontalMetersPerBlock <= 0.0 || verticalMetersPerBlock <= 0.0) {
            throw new IllegalArgumentException("meters-per-block scale must be positive");
        }
        this.originX = originX;
        this.originZ = originZ;
        this.horizontalMetersPerBlock = horizontalMetersPerBlock;
        this.verticalMetersPerBlock = verticalMetersPerBlock;
        this.datumElevationMeters = datumElevationMeters;
        this.datumY = datumY;
    }

    // --- geographic -> minecraft ---

    public int blockX(double eastMeters) {
        return originX + (int) Math.round(eastMeters / horizontalMetersPerBlock);
    }

    public int blockZ(double northMeters) {
        return originZ - (int) Math.round(northMeters / horizontalMetersPerBlock);
    }

    public int blockY(double elevationMeters) {
        return datumY + (int) Math.round((elevationMeters - datumElevationMeters) / verticalMetersPerBlock);
    }

    // --- minecraft -> geographic ---

    public double eastMeters(int x) {
        return (x - originX) * horizontalMetersPerBlock;
    }

    public double northMeters(int z) {
        return (originZ - z) * horizontalMetersPerBlock;
    }

    public double elevationMeters(int y) {
        return datumElevationMeters + (y - datumY) * verticalMetersPerBlock;
    }

    public GeoPoint geoPoint(int x, int z) {
        return new GeoPoint(eastMeters(x), northMeters(z));
    }

    public int originX() {
        return originX;
    }

    public int originZ() {
        return originZ;
    }

    public double horizontalMetersPerBlock() {
        return horizontalMetersPerBlock;
    }

    public double verticalMetersPerBlock() {
        return verticalMetersPerBlock;
    }

    public double datumElevationMeters() {
        return datumElevationMeters;
    }

    public int datumY() {
        return datumY;
    }
}
