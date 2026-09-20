"""LanduseSource tests: OSM land-use polygons -> per-cell surface classes."""

import json

import pytest

from geoworld_compiler.landuse import (
    SURFACE_COMMERCIAL, SURFACE_FARMLAND, SURFACE_FOREST, SURFACE_NATURAL,
    SURFACE_PARK, SURFACE_PARKING, SURFACE_RAILWAY, SURFACE_RESIDENTIAL,
    LanduseSource,
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
    p = tmp_path / "landuse.json"
    p.write_text(json.dumps({"elements": elements}))
    return p


def test_polygon_classification(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"landuse": "farmland"}, _poly(0.0, 0.0, 20.0)),
        _way(2, {"landuse": "residential"}, _poly(60.0, 0.0, 20.0)),
        _way(3, {"natural": "wood"}, _poly(0.0, 60.0, 20.0)),
        _way(4, {"leisure": "park"}, _poly(60.0, 60.0, 20.0)),
        _way(5, {"amenity": "parking"}, _poly(-60.0, 0.0, 20.0)),
    ])
    src = LanduseSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.surface_class(0.0, 0.0) == SURFACE_FARMLAND
    assert src.surface_class(60.0, 0.0) == SURFACE_RESIDENTIAL
    assert src.surface_class(0.0, 60.0) == SURFACE_FOREST
    assert src.surface_class(60.0, 60.0) == SURFACE_PARK
    assert src.surface_class(-60.0, 0.0) == SURFACE_PARKING
    assert src.surface_class(-60.0, 60.0) == SURFACE_NATURAL


def test_priority_parking_over_zoning(tmp_path, projection):
    # A parking lot inside a commercial zone: parking wins.
    path = _write(tmp_path, [
        _way(1, {"landuse": "commercial"}, _poly(0.0, 0.0, 30.0)),
        _way(2, {"amenity": "parking"}, _poly(0.0, 0.0, 10.0)),
    ])
    src = LanduseSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.surface_class(0.0, 0.0) == SURFACE_PARKING
    assert src.surface_class(25.0, 25.0) == SURFACE_COMMERCIAL


def test_railway_line_buffered(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"railway": "rail"}, [(-80.0, 0.0), (80.0, 0.0)]),
    ])
    src = LanduseSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.surface_class(0.0, 0.0) == SURFACE_RAILWAY
    assert src.surface_class(0.0, 2.0) == SURFACE_RAILWAY
    assert src.surface_class(0.0, 8.0) == SURFACE_NATURAL


def test_railway_cuts_landuse(tmp_path, projection):
    # Rail corridor through farmland: rail wins at the track bed.
    path = _write(tmp_path, [
        _way(1, {"landuse": "farmland"}, _poly(0.0, 0.0, 40.0)),
        _way(2, {"railway": "rail"}, [(-80.0, 0.0), (80.0, 0.0)]),
    ])
    src = LanduseSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.surface_class(0.0, 0.0) == SURFACE_RAILWAY
    assert src.surface_class(0.0, 10.0) == SURFACE_FARMLAND
