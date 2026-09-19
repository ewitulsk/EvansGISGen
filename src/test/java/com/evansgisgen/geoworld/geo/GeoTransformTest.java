package com.evansgisgen.geoworld.geo;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

/**
 * Verifies the geographic <-> Minecraft coordinate mapping using real
 * locations in Beatrice, Nebraska. The east/north offsets were produced
 * offline by projecting each lat/lon to UTM zone 14N (EPSG:32614) relative to
 * a datum at 6th &amp; Court, downtown Beatrice (40.2681 N, 96.7470 W;
 * UTM 691569.39 E, 4459949.59 N).
 */
class GeoTransformTest {
    // Origin inserted at minecraft (10000, -5000); 1 block = 1 meter.
    private static final GeoTransform T = new GeoTransform(10000, -5000, 1.0, 1.0, 0.0, 64);

    @Test
    void placesKnownLocations() {
        // name, eastMeters, northMeters, expectedBlockX, expectedBlockZ
        Object[][] cases = {
                {"6th & Court (datum)",        0.00,      0.00,    10000, -5000},
                {"Gage County Courthouse",     31.72,   -243.57,   10032, -4756},
                {"Beatrice High School",     -308.01,    736.42,    9692, -5736},
                {"Chautauqua Park",          1355.84,  -1109.55,   11356, -3890},
                {"Big Blue River @ Riverside", 1040.58, -762.15,   11041, -4238},
                {"Beatrice Municipal Airport", -696.93,  3659.10,   9303, -8659},
                {"Community Hospital",       1642.61,   2308.05,   11643, -7308},
                {"SCC Beatrice Campus",       -506.70, -3845.15,    9493, -1155},
                {"Homestead NHP",           -6590.54,   1579.18,    3409, -6579},
                {"Walmart Supercenter",      1966.76,  -1038.34,   11967, -3962},
        };
        for (Object[] c : cases) {
            String name = (String) c[0];
            double east = (double) c[1];
            double north = (double) c[2];
            int x = (int) c[3];
            int z = (int) c[4];
            assertEquals(x, T.blockX(east), name + " blockX");
            assertEquals(z, T.blockZ(north), name + " blockZ");
        }
    }

    @Test
    void northIsNegativeZ() {
        // A point 100 m north of origin must map to z = originZ - 100.
        assertEquals(-5100, T.blockZ(100.0));
        assertEquals(-4900, T.blockZ(-100.0));
    }

    @Test
    void roundTrips() {
        GeoPoint geo = T.geoPoint(11356, -3890);
        assertEquals(1356.0, geo.eastMeters(), 1e-9);
        assertEquals(-1110.0, geo.northMeters(), 1e-9);
        assertEquals(11356, T.blockX(geo.eastMeters()));
        assertEquals(-3890, T.blockZ(geo.northMeters()));
    }

    @Test
    void respectsOriginAndScale() {
        GeoTransform t = new GeoTransform(2000, 3000, 2.0, 1.0, 0.0, 64);
        // 2 m per block: 1000 m east = 500 blocks.
        assertEquals(2500, t.blockX(1000.0));
        assertEquals(2500, t.blockZ(1000.0));
        GeoPoint geo = t.geoPoint(2500, 2500);
        assertEquals(1000.0, geo.eastMeters(), 1e-9);
        assertEquals(1000.0, geo.northMeters(), 1e-9);
    }

    @Test
    void verticalDatum() {
        // Beatrice ground ~381 m maps to y=64 at 1 m/block.
        GeoTransform t = new GeoTransform(0, 0, 1.0, 1.0, 381.0, 64);
        assertEquals(64, t.blockY(381.0));
        assertEquals(74, t.blockY(391.0));
        assertEquals(391.0, t.elevationMeters(74), 1e-9);

        // 0.5 m per block doubles vertical relief.
        GeoTransform stretched = new GeoTransform(0, 0, 1.0, 0.5, 381.0, 64);
        assertEquals(84, stretched.blockY(391.0));
    }

    @Test
    void rejectsNonPositiveScale() {
        org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
                () -> new GeoTransform(0, 0, 0.0, 1.0, 0.0, 64));
        org.junit.jupiter.api.Assertions.assertThrows(IllegalArgumentException.class,
                () -> new GeoTransform(0, 0, 1.0, -1.0, 0.0, 64));
    }
}
