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


def test_interior_zones_anchor_to_entrance(tmp_path, projection):
    # Access east of the footprint -> entrance on the east edge; the
    # vestibule hugs it and the backroom sits at the far (west) side.
    def access(e, n):
        east_e, _ = projection.to_geo(40.270, -96.7494)
        return e >= east_e + 30
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, WM_RING,
             {"building": "retail", "brand": "Walmart"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500),
                         access=access)
    inst = src.instances[0]
    ex, ey = inst["entrance"]
    cx, cy = inst["centroid"]
    fx, fy = cx - ex, cy - ey
    d = (fx * fx + fy * fy) ** 0.5
    fx, fy = fx / d, fy / d
    # 3m inside from the entrance -> vestibule or its flanking cart area.
    assert src.interior_zone(ex + fx * 3, ey + fy * 3) in (1, 10)
    # Deep interior point near the far wall -> backroom.
    far_e, far_n = ex + fx * (d * 2 - 5), ey + fy * (d * 2 - 5)
    assert src.interior_zone(far_e, far_n) == 9
    # Centroid is some department — not zero.
    assert src.interior_zone(cx, cy) != 0
    # Outside the footprint -> no zone.
    assert src.interior_zone(cx, cy - 10000) == 0


def test_no_layout_business_has_no_zones(tmp_path, projection):
    # U.S. Bank carries no interior layout — business layer paints but
    # the interior raster stays empty.
    e0, n0 = projection.to_geo(40.8136085, -96.7030413)
    lat0, lon0 = 40.8136085, -96.7030413
    import math
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat0))
    ring = [(lon0 + de / m_per_deg_lon, lat0 + dn / m_per_deg_lat)
            for de, dn in ((-20, -15), (20, -15), (20, 15),
                           (-20, 15), (-20, -15))]
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(777, ring, {"building": "yes"})]})
    src = BuildingSource(osm, projection,
                         bounds=(e0 - 500, n0 - 500, e0 + 500, n0 + 500))
    inst = [i for i in src.instances if i["business"] == "us_bank"]
    assert len(inst) == 1
    assert src.interior_zone(inst[0]["centroid"][0],
                             inst[0]["centroid"][1]) == 0


def test_module_placements(tmp_path, projection):
    from geoworld_compiler.modules import (module_placements, modules_doc,
                                           write_module_nbts)
    ring = WM_RING
    def access(e, n):
        east_e, _ = projection.to_geo(40.270, -96.7494)
        return e >= east_e + 30
    osm = _write(tmp_path, "b.json", {"elements": [
        _way(593154777, ring,
             {"building": "retail", "brand": "Walmart"})]})
    src = BuildingSource(osm, projection, bounds=(-500, -500, 500, 500),
                         access=access)
    placements = module_placements(src)
    assert placements, "expected module placements"
    kinds = {p["module"] for p in placements}
    assert kinds <= {"shelf_aisle", "checkout_lane", "cart_corral",
                     "vestibule"}
    inst = src.instances[0]
    x0, z0, x1, z1 = inst["bbox"]
    for p in placements:
        assert x0 - 1 <= p["e"] <= x1 + 1
        assert z0 - 1 <= p["n"] <= z1 + 1
        assert p["building"] == inst["key"]
        # placements sit on a zoned cell
        assert src.interior_zone(p["e"], p["n"]) != 0
    doc = modules_doc(placements)
    assert "modules" in doc and "shelf_aisle" in doc["modules"]
    names = write_module_nbts(tmp_path)
    assert (tmp_path / "modules" / "shelf_aisle.nbt").exists()


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
