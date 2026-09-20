package com.evansgisgen.geoworld.geo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Optional;
import org.junit.jupiter.api.Test;

/**
 * GeoProjection UTM forward tests against pyproj-derived values: the dataset
 * anchor is 6th & Court, Beatrice (40.2681 N, 96.7470 W) -> UTM 14N
 * (691569.39 E, 4459949.59 N).
 */
class GeoProjectionTest {
    private static final GeoProjection P = new GeoProjection(
            "EPSG:32614", 691569.3902836657, 4459949.58687689);

    @Test
    void anchorMapsToZero() {
        double[] en = P.toGeo(40.2681, -96.7470).orElseThrow();
        assertEquals(0.0, en[0], 0.5);
        assertEquals(0.0, en[1], 0.5);
    }

    @Test
    void knownPoint() {
        // Downtown Lincoln 13th & P (40.8136 N, 96.7026 W) -> pyproj-verified
        // dataset meters (2196.4, 60655.4).
        double[] en = P.toGeo(40.8136, -96.7026).orElseThrow();
        assertEquals(2196.4, en[0], 1.0);
        assertEquals(60655.4, en[1], 1.0);
    }

    @Test
    void rejectsUnsupportedCrs() {
        assertTrue(new GeoProjection("EPSG:4326", 0, 0)
                .toGeo(40.0, -96.0).isEmpty());
        assertTrue(GeoProjection.parse("EPSG:4326", 0, 0) == null);
        assertTrue(GeoProjection.parse("EPSG:32614", 0, 0) != null);
    }
}
