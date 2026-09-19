package com.evansgisgen.geoworld.geo;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.zip.DataFormatException;
import java.util.zip.Inflater;
import org.jetbrains.annotations.Nullable;

/**
 * A compiled GeoWorld tile: a {@code tileSize x tileSize} grid of block
 * columns containing per-layer raster data. See format/README.md.
 *
 * <p>Immutable after load; safe to share between worldgen worker threads.
 */
public final class GeoTile {
    static final int MAGIC = 0x4757544C; // "GWTL"
    static final int VERSION = 1;

    /** Elevation-layer sentinel: the compiler had no source data for this column. */
    public static final int NO_DATA = Short.MIN_VALUE;

    private static final int COMP_NONE = 0;
    private static final int COMP_DEFLATE = 1;

    static final int LAYER_ELEVATION = 0x01;
    static final int LAYER_INFLUENCE = 0x02;
    static final int LAYER_SURFACE = 0x04;
    static final int LAYER_ROAD = 0x08;
    static final int LAYER_WATER = 0x10;

    private final int tileX;
    private final int tileZ;
    private final int tileSize;
    @Nullable private final short[] elevation;
    @Nullable private final byte[] influence;
    @Nullable private final byte[] surface;
    @Nullable private final byte[] road;
    @Nullable private final byte[] waterBits;

    private GeoTile(int tileX, int tileZ, int tileSize, @Nullable short[] elevation,
            @Nullable byte[] influence, @Nullable byte[] surface, @Nullable byte[] road,
            @Nullable byte[] waterBits) {
        this.tileX = tileX;
        this.tileZ = tileZ;
        this.tileSize = tileSize;
        this.elevation = elevation;
        this.influence = influence;
        this.surface = surface;
        this.road = road;
        this.waterBits = waterBits;
    }

    public static GeoTile read(Path file, int tileSize) throws IOException {
        return parse(Files.readAllBytes(file), tileSize);
    }

    static GeoTile parse(byte[] data, int tileSize) throws IOException {
        ByteBuffer buf = ByteBuffer.wrap(data).order(ByteOrder.BIG_ENDIAN);
        if (buf.remaining() < 18 || buf.getInt() != MAGIC) {
            throw new IOException("bad tile magic");
        }
        int version = buf.getShort() & 0xFFFF;
        if (version != VERSION) {
            throw new IOException("unsupported tile version " + version);
        }
        int tileX = buf.getInt();
        int tileZ = buf.getInt();
        int mask = buf.getShort() & 0xFFFF;
        buf.getShort(); // reserved

        short[] elevation = null;
        byte[] influence = null;
        byte[] surface = null;
        byte[] road = null;
        byte[] water = null;

        for (int bit = 1; bit <= 0x10; bit <<= 1) {
            if ((mask & bit) == 0) {
                continue;
            }
            int codec = buf.get() & 0xFF;
            int rawLen = buf.getInt();
            int storedLen = buf.getInt();
            byte[] stored = new byte[storedLen];
            buf.get(stored);
            byte[] payload = decompress(codec, stored, rawLen);
            switch (bit) {
                case LAYER_ELEVATION -> {
                    elevation = new short[rawLen / 2];
                    ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN).asShortBuffer().get(elevation);
                }
                case LAYER_INFLUENCE -> influence = payload;
                case LAYER_SURFACE -> surface = payload;
                case LAYER_ROAD -> road = payload;
                case LAYER_WATER -> water = payload;
                default -> { /* unknown bit, already skipped by mask check */ }
            }
        }
        return new GeoTile(tileX, tileZ, tileSize, elevation, influence, surface, road, water);
    }

    private static byte[] decompress(int codec, byte[] stored, int rawLen) throws IOException {
        if (codec == COMP_NONE) {
            return stored;
        }
        if (codec != COMP_DEFLATE) {
            throw new IOException("unknown tile compression codec " + codec);
        }
        Inflater inflater = new Inflater();
        try {
            inflater.setInput(stored);
            byte[] out = new byte[rawLen];
            int off = 0;
            while (off < rawLen) {
                int n = inflater.inflate(out, off, rawLen - off);
                if (n <= 0) {
                    throw new IOException("truncated compressed tile section");
                }
                off += n;
            }
            return out;
        } catch (DataFormatException e) {
            throw new IOException("corrupt tile section", e);
        } finally {
            inflater.end();
        }
    }

    private int idx(int localX, int localZ) {
        return localZ * tileSize + localX;
    }

    public int tileX() {
        return tileX;
    }

    public int tileZ() {
        return tileZ;
    }

    public boolean hasElevation() {
        return elevation != null;
    }

    /** Target terrain height in block Y for this column. */
    public int elevation(int localX, int localZ) {
        return elevation[idx(localX, localZ)];
    }

    public boolean hasInfluence() {
        return influence != null;
    }

    /** Geographic influence weight, 0-255. */
    public int influenceWeight(int localX, int localZ) {
        return influence[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasSurface() {
        return surface != null;
    }

    public int surfaceClass(int localX, int localZ) {
        return surface[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasRoad() {
        return road != null;
    }

    public int roadClass(int localX, int localZ) {
        return road[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasWater() {
        return waterBits != null;
    }

    public boolean water(int localX, int localZ) {
        int i = idx(localX, localZ);
        return (waterBits[i >> 3] >> (i & 7) & 1) != 0;
    }
}
