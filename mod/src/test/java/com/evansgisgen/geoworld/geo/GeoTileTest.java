package com.evansgisgen.geoworld.geo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.InputStream;
import org.junit.jupiter.api.Test;

/**
 * Reads fixture.gwt — a tile written by the Python compiler
 * (geoworld_compiler fixture) with a documented pattern:
 *
 * <pre>
 *   elevation[i] = (i * 7) % 500 - 250   (int16)
 *   influence[i] = i % 256               (u8)
 *   surface[i]   = i % 17                (u8)
 *   water bit i  = set iff i % 3 == 0
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
        assertFalse(tile.hasRoad());
        assertTrue(tile.hasWater());
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
}
