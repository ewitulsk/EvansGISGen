package com.evansgisgen.geoworld.geo;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class GeoDatasetTest {
    @TempDir
    Path dir;

    private GeoDataset datasetWithFixture() throws IOException {
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
                  "tile_count": 1
                }
                """);
        Path tiles = Files.createDirectories(dir.resolve("tiles"));
        try (InputStream in = GeoDatasetTest.class.getResourceAsStream("/fixture.gwt")) {
            Files.write(tiles.resolve("+0000_+0000.gwt"), in.readAllBytes());
        }
        return GeoDataset.load(dir, GeoTransform.DEFAULT);
    }

    @Test
    void emptyWhenMissing() {
        GeoDataset ds = GeoDataset.load(dir.resolve("nonexistent"), GeoTransform.DEFAULT);
        assertTrue(ds.isEmpty());
        assertTrue(ds.tileAt(0, 0).isEmpty());
        assertEquals(GeoTransform.DEFAULT.originX(), ds.transform().originX());
    }

    @Test
    void loadsManifestTransform() throws IOException {
        GeoDataset ds = datasetWithFixture();
        assertFalse(ds.isEmpty());
        assertEquals(10000, ds.transform().originX());
        assertEquals(-5000, ds.transform().originZ());
        assertEquals(381.0, ds.transform().datumElevationMeters(), 1e-9);
    }

    @Test
    void tileLookup() throws IOException {
        GeoDataset ds = datasetWithFixture();
        assertTrue(ds.tileAt(0, 0).isPresent());
        assertTrue(ds.tileAt(255, 255).isPresent());
        assertTrue(ds.tileAt(-1, -1).isEmpty());   // tile -1,-1 absent
        assertTrue(ds.tileAt(5000, 0).isEmpty());
        GeoTile tile = ds.tileAt(0, 0).orElseThrow();
        assertEquals(-250, tile.elevation(0, 0));
    }

    @Test
    void influenceField() throws IOException {
        GeoDataset ds = datasetWithFixture();
        ScalarField field = ds.influenceField();
        // Fixture pattern: influence[i] = i % 256, i = lz*256 + lx.
        assertEquals(0.0f, field.sample(0, 0));
        assertEquals(1.0f / 255.0f, field.sample(1, 0), 1e-7);
        assertEquals(1.0f, field.sample(255, 0), 1e-7);
        assertEquals(0.0f, field.sample(0, 1));        // i=256 wraps to 0
        assertEquals(1.0f, field.sample(255, 255), 1e-7);
        assertEquals(0.0f, field.sample(5000, 0));     // no tile -> vanilla
        assertEquals(field.sample(7, 9), ds.influence(7, 9));
    }

    @Test
    void emptyDatasetHasZeroInfluence() {
        GeoDataset ds = GeoDataset.empty();
        assertEquals(0.0f, ds.influence(0, 0));
        assertEquals(0.0f, ds.influenceField().sample(100, -100));
    }

    @Test
    void localCoords() {
        GeoDataset ds = GeoDataset.empty();
        assertEquals(0, ds.localCoord(0));
        assertEquals(255, ds.localCoord(255));
        assertEquals(0, ds.localCoord(256));
        assertEquals(255, ds.localCoord(-1));      // floorMod handles negatives
    }
}
