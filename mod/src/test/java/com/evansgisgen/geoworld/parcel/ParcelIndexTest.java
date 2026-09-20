package com.evansgisgen.geoworld.parcel;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/**
 * ParcelIndex tests: parcels.json loading, point-in-polygon, normalized
 * address matching, and gazetteer suggestions — no Minecraft needed.
 */
class ParcelIndexTest {

    @TempDir
    Path dir;

    private ParcelIndex index() throws Exception {
        // P1: 20x30 lot e[100,120] n[-80,-50]; P2: adjacent, no address.
        String json = """
            {"parcels": [
              {"id": "gage_P1", "address": "420 N 6TH ST, BEATRICE, NE, 68310",
               "bbox": [100, -80, 120, -50],
               "rings": [[[100, -80], [120, -80], [120, -50], [100, -50]]]},
              {"id": "gage_P2", "address": null,
               "bbox": [200, -80, 220, -50],
               "rings": [[[200, -80], [220, -80], [220, -50], [200, -50]]]},
              {"id": "gage_HOLE", "address": null,
               "bbox": [0, 0, 10, 10],
               "rings": [[[0, 0], [10, 0], [10, 10], [0, 10]],
                         [[4, 4], [6, 4], [6, 6], [4, 6]]]}
            ],
             "addresses": [
              {"text": "420 N 6TH ST", "east": 110.0, "north": -65.0,
               "parcel": "gage_P1"},
              {"text": "500 N 6TH ST", "east": 210.0, "north": -65.0,
               "parcel": null},
              {"text": "425 N 6TH ST", "east": 110.0, "north": -55.0,
               "parcel": "gage_P1"}
            ]}""";
        Files.writeString(dir.resolve("parcels.json"), json);
        return ParcelIndex.load(dir);
    }

    @Test
    void loadsAndFindsByPoint() throws Exception {
        ParcelIndex idx = index();
        assertEquals(3, idx.size());
        Optional<Parcel> p = idx.at(110, -60);
        assertTrue(p.isPresent());
        assertEquals("gage_P1", p.get().id());
        assertFalse(idx.at(99, -60).isPresent());
    }

    @Test
    void resolvesAddresses() throws Exception {
        ParcelIndex idx = index();
        // Exact + normalized variants.
        assertEquals("gage_P1", idx.resolve("420 N 6TH ST").get().id());
        assertEquals("gage_P1", idx.resolve("420 n. 6th street").get().id());
        // Gazetteer point without a parcel id falls through to PIP.
        assertEquals("gage_P2", idx.resolve("500 N 6TH ST").get().id());
        assertFalse(idx.resolve("999 nowhere ave").isPresent());
    }

    @Test
    void resolvesCoordinates() throws Exception {
        ParcelIndex idx = index();
        assertEquals("gage_P1", idx.resolve("110,-60").get().id());
        assertEquals("gage_P2", idx.resolve("210, -65").get().id());
        assertFalse(idx.resolve("0,-60").isPresent());
    }

    @Test
    void holeIsExcluded() throws Exception {
        ParcelIndex idx = index();
        assertTrue(idx.at(2, 2).isPresent());
        assertFalse(idx.at(5, 5).isPresent());  // inside the hole ring
    }

    @Test
    void suggestsByPrefix() throws Exception {
        ParcelIndex idx = index();
        List<String> hits = idx.suggest("420 n 6", 10);
        assertEquals(List.of("420 N 6TH ST"), hits);
        assertEquals(2, idx.suggest("4", 10).size());
        assertTrue(idx.suggest("zzz", 10).isEmpty());
    }

    @Test
    void stableLandmarkId() throws Exception {
        ParcelIndex idx = index();
        Parcel p = idx.at(110, -60).get();
        assertEquals("parcel_gage_P1", p.landmarkId());
        Parcel weird = new Parcel("a b/c.d", null,
                new double[]{0, 0, 1, 1}, List.of());
        assertEquals("parcel_a_b_c_d", weird.landmarkId());
    }

    @Test
    void multiParcelSelectionStableId() throws Exception {
        ParcelIndex idx = index();
        Parcel p1 = idx.byId("gage_P1");
        Parcel p2 = idx.byId("gage_P2");
        var sel = ParcelSelection.of(List.of(p1, p2));
        // Order-independent: the same block re-selected in any order lands
        // on the same lot landmark key.
        assertEquals(sel.landmarkId(),
                ParcelSelection.of(List.of(p2, p1)).landmarkId());
        assertTrue(sel.landmarkId().startsWith("lot_"));
        // Union bbox spans both parcels.
        assertEquals(100, sel.minEast());
        assertEquals(220, sel.maxEast());
        assertTrue(sel.contains(110, -60));
        assertTrue(sel.contains(210, -60));
        assertFalse(sel.contains(160, -60));  // gap between the lots
        assertEquals("gage_P1,gage_P2", sel.idList());
    }

    @Test
    void neighborsWithinRect() throws Exception {
        ParcelIndex idx = index();
        List<Parcel> around = idx.within(90, -90, 230, -40);
        assertEquals(2, around.size());
    }

    @Test
    void emptyWhenMissing() {
        ParcelIndex idx = ParcelIndex.load(dir);
        assertTrue(idx.isEmpty());
    }
}
