package com.evansgisgen.geoworld.geo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

/**
 * Reads fixture.gwt — a tile written by the Python compiler
 * (geoworld_compiler fixture) with a documented pattern:
 *
 * <pre>
 *   elevation[i] = (i * 7) % 500 - 250   (int16)
 *   influence[i] = i % 256               (u8)
 *   surface[i]   = i % 17                (u8)
 *   road[i]      = i % 7                 (u8)
 *   water bit i  = set iff i % 3 == 0
 *   water_depth[i] = i % 5               (u8)
 *   building[i]  = i % 7                 (u8)
 *   building_levels[i] = i % 9           (u8)
 * </pre>
 *
 * i = localZ * 256 + localX.
 */
class GeoTileTest {
    private static GeoTile loadFixture() throws IOException {
        try (InputStream in = GeoTileTest.class.getResourceAsStream("/fixture.gwt")) {
            return GeoTile.parse(in.readAllBytes(), 256);
        }
    }

    @Test
    void header() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(0, tile.tileX());
        assertEquals(0, tile.tileZ());
        assertTrue(tile.hasElevation());
        assertTrue(tile.hasInfluence());
        assertTrue(tile.hasSurface());
        assertTrue(tile.hasRoad());
        assertTrue(tile.hasWater());
        assertTrue(tile.hasBuilding());
    }

    @Test
    void elevation() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(-250, tile.elevation(0, 0));          // i = 0
        assertEquals(-243, tile.elevation(1, 0));          // i = 1
        assertEquals(42, tile.elevation(0, 1));            // i = 256 -> 1792 % 500 - 250
        assertEquals(-5, tile.elevation(255, 255));        // i = 65535 -> 458745 % 500 - 250
    }

    @Test
    void influence() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(0, tile.influenceWeight(0, 0));
        assertEquals(255, tile.influenceWeight(255, 0));
        assertEquals(0, tile.influenceWeight(0, 1));       // i = 256 -> 256 % 256
        assertEquals(1, tile.influenceWeight(1, 1));       // i = 257 -> 257 % 256
    }

    @Test
    void surface() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(2, tile.surfaceClass(2, 0));
        assertEquals(1, tile.surfaceClass(0, 1));          // i = 256 -> 256 % 17
    }

    @Test
    void water() throws IOException {
        GeoTile tile = loadFixture();
        assertTrue(tile.water(0, 0));                      // i = 0, 0 % 3 == 0
        assertFalse(tile.water(1, 0));
        assertFalse(tile.water(2, 0));
        assertTrue(tile.water(3, 0));
        assertFalse(tile.water(0, 1));                     // i = 256, 256 % 3 != 0
        assertTrue(tile.water(2, 1));                      // i = 258, 258 % 3 == 0
    }

    @Test
    void waterDepth() throws IOException {
        GeoTile tile = loadFixture();
        assertTrue(tile.hasWaterDepth());
        assertEquals(0, tile.waterDepth(0, 0));
        assertEquals(1, tile.waterDepth(1, 0));
        assertEquals(4, tile.waterDepth(4, 0));
        assertEquals(1, tile.waterDepth(0, 1));            // i = 256 -> 256 % 5
        assertEquals(0, tile.waterDepth(255, 255));        // i = 65535 -> 65535 % 5
    }

    @Test
    void road() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(1, tile.roadClass(1, 0));
        assertEquals(6, tile.roadClass(6, 0));
        assertEquals(0, tile.roadClass(7, 0));
        assertEquals(6, tile.roadClass(2, 1));             // i = 258 -> 258 % 7
    }

    @Test
    void building() throws IOException {
        GeoTile tile = loadFixture();
        assertEquals(1, tile.buildingClass(1, 0));
        assertEquals(6, tile.buildingClass(6, 0));
        assertEquals(0, tile.buildingClass(7, 0));
        assertEquals(1, tile.buildingLevels(1, 0));
        assertEquals(8, tile.buildingLevels(8, 0));
        assertEquals(0, tile.buildingLevels(9, 0));
        assertEquals(4, tile.buildingLevels(0, 1));        // i = 256 -> 256 % 9
    }

    /**
     * Writes a minimal uncompressed .gwt containing only an elevation layer,
     * then verifies the NO_DATA sentinel round-trips unchanged.
     */
    @Test
    void noDataElevation() throws IOException {
        int n = 256 * 256;
        short[] values = new short[n];
        Arrays.fill(values, (short) GeoTile.NO_DATA);
        values[0] = 75;
        values[n - 1] = -12;
        byte[] elev = new byte[n * 2];
        ByteBuffer.wrap(elev).order(ByteOrder.BIG_ENDIAN).asShortBuffer().put(values);

        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        DataOutputStream d = new DataOutputStream(bytes);
        d.writeInt(0x4757544C);        // "GWTL"
        d.writeShort(1);               // version
        d.writeInt(3);                 // tileX
        d.writeInt(-2);                // tileZ
        d.writeShort(0x01);            // layer mask: elevation only
        d.writeShort(0);               // reserved
        d.writeByte(0);                // codec: COMP_NONE
        d.writeInt(elev.length);       // raw length
        d.writeInt(elev.length);       // stored length
        d.write(elev);
        d.flush();

        GeoTile tile = GeoTile.parse(bytes.toByteArray(), 256);
        assertEquals(3, tile.tileX());
        assertEquals(-2, tile.tileZ());
        assertTrue(tile.hasElevation());
        assertFalse(tile.hasInfluence());
        assertEquals(75, tile.elevation(0, 0));
        assertEquals(-12, tile.elevation(255, 255));
        assertEquals(GeoTile.NO_DATA, tile.elevation(1, 0));
        assertEquals(GeoTile.NO_DATA, tile.elevation(128, 200));
    }
}
