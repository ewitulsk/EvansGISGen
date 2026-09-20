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
    static final int LAYER_WATER_DEPTH = 0x20;
    static final int LAYER_BUILDING = 0x40;
    static final int LAYER_BUILDING_LEVELS = 0x80;
    static final int LAYER_ROADZ = 0x100;
    static final int LAYER_ROADE = 0x200;
    static final int LAYER_BUILDING_ID = 0x400;
    static final int LAYER_BUILDING_ROOF = 0x800;

    private final int tileX;
    private final int tileZ;
    private final int tileSize;
    @Nullable private final short[] elevation;
    @Nullable private final byte[] influence;
    @Nullable private final byte[] surface;
    @Nullable private final byte[] road;
    @Nullable private final byte[] waterBits;
    @Nullable private final byte[] waterDepth;
    @Nullable private final byte[] building;
    @Nullable private final byte[] buildingLevels;
    @Nullable private final short[] roadDeck;
    @Nullable private final byte[] roadDeckClass;
    @Nullable private final char[] buildingId;
    @Nullable private final short[] buildingRoof;

    private GeoTile(int tileX, int tileZ, int tileSize, @Nullable short[] elevation,
            @Nullable byte[] influence, @Nullable byte[] surface, @Nullable byte[] road,
            @Nullable byte[] waterBits, @Nullable byte[] waterDepth,
            @Nullable byte[] building, @Nullable byte[] buildingLevels,
            @Nullable short[] roadDeck, @Nullable byte[] roadDeckClass,
            @Nullable char[] buildingId, @Nullable short[] buildingRoof) {
        this.tileX = tileX;
        this.tileZ = tileZ;
        this.tileSize = tileSize;
        this.elevation = elevation;
        this.influence = influence;
        this.surface = surface;
        this.road = road;
        this.waterBits = waterBits;
        this.waterDepth = waterDepth;
        this.building = building;
        this.buildingLevels = buildingLevels;
        this.roadDeck = roadDeck;
        this.roadDeckClass = roadDeckClass;
        this.buildingId = buildingId;
        this.buildingRoof = buildingRoof;
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
        byte[] waterDepth = null;
        byte[] building = null;
        byte[] buildingLevels = null;
        short[] roadDeck = null;
        byte[] roadDeckClass = null;
        char[] buildingId = null;
        short[] buildingRoof = null;

        for (int bit = 1; bit <= 0x800; bit <<= 1) {
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
                case LAYER_WATER_DEPTH -> waterDepth = payload;
                case LAYER_BUILDING -> building = payload;
                case LAYER_BUILDING_LEVELS -> buildingLevels = payload;
                case LAYER_ROADZ -> {
                    roadDeck = new short[rawLen / 2];
                    ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN).asShortBuffer().get(roadDeck);
                }
                case LAYER_ROADE -> roadDeckClass = payload;
                case LAYER_BUILDING_ID -> {
                    buildingId = new char[rawLen / 2];
                    ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN).asCharBuffer().get(buildingId);
                }
                case LAYER_BUILDING_ROOF -> {
                    buildingRoof = new short[rawLen / 2];
                    ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN).asShortBuffer().get(buildingRoof);
                }
                default -> { /* unknown bit, already skipped by mask check */ }
            }
        }
        return new GeoTile(tileX, tileZ, tileSize, elevation, influence, surface, road,
                water, waterDepth, building, buildingLevels, roadDeck, roadDeckClass,
                buildingId, buildingRoof);
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

    /** Surface class id for this column (0 = unclassified/none present). */
    public int surfaceClass(int localX, int localZ) {
        return surface == null ? 0 : surface[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasRoad() {
        return road != null;
    }

    /** Road class id for this column (0 = no road/none present). */
    public int roadClass(int localX, int localZ) {
        return road == null ? 0 : road[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasWater() {
        return waterBits != null || waterDepth != null;
    }

    /** Water footprint: bitset if present, else derived from depth > 0. */
    public boolean water(int localX, int localZ) {
        if (waterBits != null) {
            int i = idx(localX, localZ);
            return (waterBits[i >> 3] >> (i & 7) & 1) != 0;
        }
        return waterDepth(localX, localZ) > 0;
    }

    public boolean hasWaterDepth() {
        return waterDepth != null;
    }

    /**
     * Water depth in blocks for this column (0 = dry). The elevation layer
     * at wet columns is the channel bed; the runtime fills
     * {@code bed+1 .. bed+depth} with water.
     */
    public int waterDepth(int localX, int localZ) {
        return waterDepth == null ? 0 : waterDepth[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasBuilding() {
        return building != null;
    }

    /** Building class id for this column (0 = no footprint). */
    public int buildingClass(int localX, int localZ) {
        return building == null ? 0 : building[idx(localX, localZ)] & 0xFF;
    }

    /** Floor count for this column (0 outside footprints). */
    public int buildingLevels(int localX, int localZ) {
        return buildingLevels == null ? 0 : buildingLevels[idx(localX, localZ)] & 0xFF;
    }

    public boolean hasBuildingId() {
        return buildingId != null;
    }

    /**
     * Footprint-instance id for this column (0 = no footprint). Two
     * abutting OSM polygons carry different ids even when they share a
     * class, so the runtime can wall between them (Phase 15).
     */
    public int buildingId(int localX, int localZ) {
        return buildingId == null ? 0 : buildingId[idx(localX, localZ)];
    }

    public boolean hasBuildingRoof() {
        return buildingRoof != null;
    }

    /**
     * Uniform roof-top block-Y for this column's instance (NO_DATA when
     * the compiler had no DEM answer). One value per footprint — the roof
     * no longer steps with the terrain beneath it (Phase 15).
     */
    public int buildingRoofY(int localX, int localZ) {
        return buildingRoof == null ? NO_DATA : buildingRoof[idx(localX, localZ)];
    }

    public boolean hasRoadDeck() {
        return roadDeck != null;
    }

    /**
     * Elevated deck top block-Y for this column (NO_DATA = no deck). The
     * compiler emits a deck only where a bridge way resolves above the
     * bare-earth DEM — embanked ramps stay terrain.
     */
    public int roadDeckY(int localX, int localZ) {
        return roadDeck == null ? NO_DATA : roadDeck[idx(localX, localZ)];
    }

    /** Cross-section class of the elevated deck (0 = none). */
    public int roadDeckClass(int localX, int localZ) {
        return roadDeckClass == null ? 0 : roadDeckClass[idx(localX, localZ)] & 0xFF;
    }
}
