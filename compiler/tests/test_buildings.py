"""BuildingSource tests: OSM building polygons -> per-cell class + levels."""

import json

import pytest

from geoworld_compiler.buildings import (
    BUILDING_CIVIC, BUILDING_GENERIC, BUILDING_INDUSTRIAL, BUILDING_NONE,
    BUILDING_OUTBUILDING, BUILDING_RESIDENTIAL, BuildingSource,
)
from geoworld_compiler.transform import Projection

CRS = "EPSG:32614"
ANCHOR = (40.2681, -96.7470)


@pytest.fixture(scope="module")
def projection():
    return Projection(CRS, *ANCHOR)


def _way(el_id, tags, coords):
    """coords are (east, north) meters relative to the anchor."""
    import pyproj
    proj = Projection(CRS, *ANCHOR)
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    geom = [{"lat": lat, "lon": lon}
            for e, n in coords
            for lon, lat in [inv.transform(e + proj.anchor_east,
                                          n + proj.anchor_north)]]
    return {"type": "way", "id": el_id, "tags": tags, "geometry": geom}


def _poly(cx, cy, half):
    return [(cx - half, cy - half), (cx + half, cy - half),
            (cx + half, cy + half), (cx - half, cy + half),
            (cx - half, cy - half)]


def _write(tmp_path, elements):
    p = tmp_path / "buildings.json"
    p.write_text(json.dumps({"elements": elements}))
    return p


def test_class_and_default_levels(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"building": "house"}, _poly(0.0, 0.0, 10.0)),
        _way(2, {"building": "warehouse"}, _poly(60.0, 0.0, 15.0)),
        _way(3, {"building": "school"}, _poly(0.0, 60.0, 15.0)),
        _way(4, {"building": "shed"}, _poly(60.0, 60.0, 5.0)),
        _way(5, {"building": "yes"}, _poly(-60.0, 0.0, 10.0)),
    ])
    src = BuildingSource(path, projection, extent_m=200.0)
    assert src.building_class(0.0, 0.0) == BUILDING_RESIDENTIAL
    assert src.building_levels(0.0, 0.0) == 2
    assert src.building_class(60.0, 0.0) == BUILDING_INDUSTRIAL
    assert src.building_class(0.0, 60.0) == BUILDING_CIVIC
    assert src.building_class(60.0, 60.0) == BUILDING_OUTBUILDING
    assert src.building_levels(60.0, 60.0) == 1
    assert src.building_class(-60.0, 0.0) == BUILDING_GENERIC
    assert src.building_class(-60.0, 60.0) == BUILDING_NONE
    assert src.building_levels(-60.0, 60.0) == 0


def test_levels_tag_override(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"building": "apartments", "building:levels": "6"},
             _poly(0.0, 0.0, 10.0)),
        _way(2, {"building": "commercial", "height": "15"},
             _poly(60.0, 0.0, 10.0)),
    ])
    src = BuildingSource(path, projection, extent_m=200.0)
    assert src.building_class(0.0, 0.0) == BUILDING_RESIDENTIAL
    assert src.building_levels(0.0, 0.0) == 6
    # height 15 m -> 5 floors.
    assert src.building_levels(60.0, 0.0) == 5


def test_open_ring_is_closed(tmp_path, projection):
    # OSM ways should close, but tolerate an unclosed ring.
    ring = [(0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0)]
    path = _write(tmp_path, [
        _way(1, {"building": "house"}, ring),
    ])
    src = BuildingSource(path, projection, extent_m=200.0)
    assert src.building_class(10.0, 10.0) == BUILDING_RESIDENTIAL
