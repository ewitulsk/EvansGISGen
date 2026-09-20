package com.evansgisgen.geoworld.sign;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import com.evansgisgen.geoworld.geo.GeoDataset;
import com.evansgisgen.geoworld.geo.GeoTransform;

class SignIndexTest {
    @TempDir
    Path dir;

    private GeoDataset dataset() throws IOException {
        Files.writeString(dir.resolve("manifest.json"), """
                {
                  "format_version": 1,
                  "name": "test",
                  "tile_size": 256,
                  "transform": {
                    "origin": { "minecraft_x": 10000, "minecraft_z": -5000 },
                    "scale": { "horizontal_meters_per_block": 1.0, "vertical_meters_per_block": 1.0 },
                    "vertical_datum": { "elevation_meters": 381.0, "minecraft_y": 64 }
                  },
                  "tile_count": 0
                }
                """);
        return GeoDataset.load(dir, GeoTransform.DEFAULT);
    }

    @Test
    void emptyWhenMissing() throws IOException {
        SignIndex index = SignIndex.load(dataset());
        assertTrue(index.isEmpty());
        assertEquals(0, index.size());
        assertTrue(index.nearest(0, 0, 1000).isEmpty());
    }

    @Test
    void loadsAndQueries() throws IOException {
        Files.writeString(dir.resolve("signs.json"), """
                {"signs": [
                  {"e": 100.0, "n": 60.0, "type": "street_name",
                   "lines": ["O ST", "N 14TH ST"], "rot": 4},
                  {"e": 120.0, "n": 70.0, "type": "stop",
                   "lines": ["STOP"], "rot": 12}
                ]}
                """);
        SignIndex index = SignIndex.load(dataset());
        assertFalse(index.isEmpty());
        assertEquals(2, index.size());
        // e/n -> block: x = 10000 + e, z = -5000 - n
        var near = index.nearest(10105, -5055, 100);
        assertTrue(near.isPresent());
        SignIndex.Sign s = near.get();
        assertEquals(10100, s.x());
        assertEquals(-5060, s.z());
        assertEquals("street_name", s.type());
        assertEquals("O ST", s.lines()[0]);
        assertEquals("N 14TH ST", s.lines()[1]);
        assertEquals(4, s.rot());
        // The stop sign is nearer its own placement than the blade.
        var stop = index.nearest(10120, -5070, 50);
        assertTrue(stop.isPresent());
        assertEquals("stop", stop.get().type());
        assertEquals("STOP", stop.get().lines()[0]);
        assertTrue(index.nearest(0, 0, 50).isEmpty());
    }
}
