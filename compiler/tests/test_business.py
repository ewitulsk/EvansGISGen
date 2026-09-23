"""Known-business capture tests (Phase 19)."""
import json
from pathlib import Path

import pytest

from geoworld_compiler.buildings import BuildingSource
from geoworld_compiler.businesses import (BUSINESSES, match_business,
                                          override_business)
from geoworld_compiler.transform import Projection


@pytest.fixture
def projection():
    # Beatrice NE anchor — same CRS as the real dataset.
    return Projection("EPSG:32614", 40.2681, -96.7470)


def _write(tmp_path, name, doc):
    p = tmp_path / name
    p.write_text(json.dumps(doc))
    return p


def _way(wid, ring, tags):
    return {"type": "way", "id": wid, "tags": tags,
            "geometry": [{"lat": la, "lon": lo} for lo, la in ring]}


def _node(nid, lat, lon, tags):
    return {"type": "node", "id": nid, "lat": lat, "lon": lon, "tags": tags}


# A ~60x40m Walmart-ish rectangle near the Beatrice anchor.
WM_RING = [(-96.750, 40.270), (-96.7494, 40.270), (-96.7494, 40.2704),
           (-96.750, 40.2704), (-96.750, 40.270)]


def test_brand_tagged_way_resolves(tmp_path, projection):
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, WM_RING,
             {"building": "retail", "brand": "Walmart",
              "name": "Walmart Supercenter", "shop": "supermarket"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500))
    inst = [i for i in src.instances if i["business"] == "walmart"]
    assert len(inst) == 1
    assert inst[0]["key"] == "osm:way/593154777"
    assert inst[0]["name"] == "Walmart Supercenter"
    # The business layer paints under the footprint.
    ce, cn = inst[0]["centroid"]
    assert src.business_at(ce, cn) == BUSINESSES["walmart"]["id"]


def test_poi_node_brand_fills_identity(tmp_path, projection):
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(1001, WM_RING, {"building": "retail"})]})
    pois = _write(tmp_path, "p.json", {"elements": [
        _node(9, 40.2702, -96.7497, {"shop": "supermarket",
                                     "brand": "Walmart"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500),
                         pois_json_path=pois)
    inst = [i for i in src.instances if i["business"] == "walmart"]
    assert len(inst) == 1
    assert inst[0]["key"] == "osm:way/1001"


def test_override_catches_untagged(tmp_path, projection):
    # Untagged footprint on the Beatrice Walmart centroid.
    e0, n0 = projection.to_geo(40.3036881, -96.7442309)
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, WM_RING, {"building": "yes"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500))
    # ring centroid isn't at the override point here -> no match
    assert not [i for i in src.instances if i["business"] == "walmart"]
    assert override_business(e0, n0, projection) == "walmart"


def test_match_business_table():
    assert match_business({"brand": "Walmart"}) == "walmart"
    assert match_business({"name": "Walmart Supercenter"}) == "walmart"
    assert match_business({"brand:wikidata": "Q483551"}) == "walmart"
    assert match_business({"brand": "McDonald's"}) == "mcdonalds"
    assert match_business({"name": "U.S. Bank"}) == "us_bank"
    assert match_business({"name": "Walmart Avenue"}) is None
    assert match_business({}) is None
    assert match_business(None) is None


def test_businesses_doc(tmp_path, projection):
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, WM_RING, {"building": "retail", "brand": "Walmart"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500))
    doc = src.businesses_doc()
    assert doc["businesses"]["walmart"]["id"] == 1
    assert doc["businesses"]["walmart"]["palette"]["accent"] == "blue_concrete"
    inst = doc["instances"]["osm:way/593154777"]
    assert inst["business"] == "walmart"
    assert len(inst["entrance"]) == 2
    assert 0.0 <= inst["axis_deg"] < 180.0
    signs = src.business_signs()
    assert len(signs) == 1
    assert signs[0]["type"] == "business_pylon"
    assert signs[0]["lines"] == ["Walmart Supercenter"]


def test_entrance_prefers_access_side(tmp_path, projection):
    # Access road along the ring's east edge -> entrance sits east.
    ring = WM_RING
    def access(e, n):
        east_e, _ = projection.to_geo(40.270, -96.7494)
        return e >= east_e + 30  # "road" column 30m east of the footprint
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, ring, {"building": "retail", "brand": "Walmart"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500),
                         access=access)
    inst = src.instances[0]
    ex, ey = inst["entrance"]
    # East edge midpoint has lower access distance than the others.
    east_e, _ = projection.to_geo(40.2702, -96.7494)
    assert ex > east_e - 1.0
