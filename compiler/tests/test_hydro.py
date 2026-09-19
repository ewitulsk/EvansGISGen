"""HydroSource tests: OSM JSON -> rasterized channel-depth field."""

import json

import pytest

from geoworld_compiler.hydro import HydroSource, parse_osm_water
from geoworld_compiler.transform import Projection

CRS = "EPSG:32614"
ANCHOR = (40.2681, -96.7470)


@pytest.fixture(scope="module")
def projection():
    return Projection(CRS, *ANCHOR)


def _osm(elements):
    return {"elements": elements}


def _way(el_id, tags, coords):
    """coords: list of (east, north) geo meters -> Overpass-like geometry."""
    import pyproj
    proj = Projection(CRS, *ANCHOR)
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    geom = []
    for e, n in coords:
        lon, lat = inv.transform(e + proj.anchor_east, n + proj.anchor_north)
        geom.append({"lat": lat, "lon": lon})
    return {"type": "way", "id": el_id, "tags": tags, "geometry": geom}


def test_parse_osm_water(projection):
    data = _osm([
        _way(1, {"waterway": "river"}, [(-50.0, 0.0), (50.0, 0.0)]),
        _way(2, {"natural": "water", "water": "pond"},
             [(100.0, 100.0), (120.0, 100.0), (120.0, 120.0),
              (100.0, 120.0), (100.0, 100.0)]),
        _way(3, {"highway": "residential"}, [(0.0, 0.0), (10.0, 0.0)]),
        {"type": "relation", "id": 4, "tags": {"waterway": "river"}},
    ])
    lines, areas = parse_osm_water(data, projection)
    assert [k for k, _ in lines] == ["river"]
    assert len(areas) == 1 and areas[0][0] == "pond"
    # Coordinates round-trip to geo meters near the given values.
    assert lines[0][1][0] == pytest.approx((-50.0, 0.0), abs=0.01)


def _write_osm(tmp_path, elements):
    p = tmp_path / "water.json"
    p.write_text(json.dumps(_osm(elements)))
    return p


def test_river_depth_profile(tmp_path, projection):
    # River centerline east-west through the origin.
    path = _write_osm(tmp_path, [
        _way(1, {"waterway": "river"}, [(-150.0, 0.0), (150.0, 0.0)]),
    ])
    hydro = HydroSource(path, projection, extent_m=180.0, bank_m=6.0)
    # Centerline: full river depth (half-width 15 > bank 6).
    assert hydro.depth_m(0.0, 0.0) == pytest.approx(4.0, abs=0.05)
    # Flat bed in the middle, then a monotonic falloff to the banks
    # (~15 m half-width; rasterization rounds the centerline to ~2 px).
    d10 = hydro.depth_m(0.0, 10.0)
    d14 = hydro.depth_m(0.0, 14.0)
    assert hydro.depth_m(0.0, 5.0) == pytest.approx(4.0, abs=0.05)
    assert d10 <= 4.0 and 0.0 < d14 < d10
    # Past the bank: dry.
    assert hydro.depth_m(0.0, 18.0) == 0.0
    assert hydro.depth_m(0.0, -18.0) == 0.0
    # Beyond the line's end + reach: dry.
    assert hydro.depth_m(0.0, 200.0) == 0.0
    assert hydro.depth_m(300.0, 0.0) == 0.0


def test_pond_polygon_depth(tmp_path, projection):
    # 40 m square pond centered at (100, 100).
    ring = [(80.0, 80.0), (120.0, 80.0), (120.0, 120.0),
            (80.0, 120.0), (80.0, 80.0)]
    path = _write_osm(tmp_path, [
        _way(1, {"natural": "water", "water": "pond"}, ring),
    ])
    hydro = HydroSource(path, projection, extent_m=200.0, bank_m=6.0)
    # Center: 20 m from the edge > bank_m -> full pond depth.
    assert hydro.depth_m(100.0, 100.0) == pytest.approx(2.5, abs=0.1)
    # Just inside the edge: shallower than center.
    assert 0.0 < hydro.depth_m(82.5, 100.0) < 2.5
    # Outside the polygon: dry.
    assert hydro.depth_m(70.0, 100.0) == 0.0


def test_class_widths(tmp_path, projection):
    # A ditch is much narrower than a river.
    path = _write_osm(tmp_path, [
        _way(1, {"waterway": "ditch"}, [(-100.0, 50.0), (100.0, 50.0)]),
    ])
    hydro = HydroSource(path, projection, extent_m=180.0, bank_m=6.0)
    # Bank capped at half-width: even a 2 m ditch reaches full depth.
    assert hydro.depth_m(0.0, 50.0) == pytest.approx(0.8, abs=0.15)
    # Wet only within a couple meters of the centerline.
    assert hydro.depth_m(0.0, 51.0) > 0.0
    assert hydro.depth_m(0.0, 56.0) == 0.0
