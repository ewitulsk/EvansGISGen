"""RoadSource tests: OSM highways -> per-cell road surface classes."""

import json

import pytest

from geoworld_compiler.roads import (
    ROAD_ASPHALT, ROAD_CURB, ROAD_MARKING, ROAD_NONE, ROAD_SIDEWALK,
    ROAD_TRACK, RoadSource, parse_osm_roads,
)
from geoworld_compiler.transform import Projection

CRS = "EPSG:32614"
ANCHOR = (40.2681, -96.7470)


@pytest.fixture(scope="module")
def projection():
    return Projection(CRS, *ANCHOR)


def _way(el_id, tags, coords):
    import pyproj
    proj = Projection(CRS, *ANCHOR)
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    geom = [{"lat": lat, "lon": lon}
            for e, n in coords
            for lon, lat in [inv.transform(e + proj.anchor_east,
                                          n + proj.anchor_north)]]
    return {"type": "way", "id": el_id, "tags": tags, "geometry": geom}


def _write(tmp_path, elements):
    p = tmp_path / "roads.json"
    p.write_text(json.dumps({"elements": elements}))
    return p


def test_parse_filters_and_tags(projection):
    data = {"elements": [
        _way(1, {"highway": "residential"}, [(0.0, 0.0), (10.0, 0.0)]),
        _way(2, {"highway": "footway"}, [(0.0, 0.0), (10.0, 0.0)]),
        _way(3, {"highway": "residential", "lanes": "4"},
             [(0.0, 20.0), (10.0, 20.0)]),
        _way(4, {"highway": "trunk", "sidewalk": "both"},
             [(0.0, 40.0), (10.0, 40.0)]),
    ]}
    roads = parse_osm_roads(data, projection)
    assert len(roads) == 3                      # footway skipped
    by_hw = sorted(roads, key=lambda r: r[0].half)
    # lanes=4 widened the second residential way past the default.
    assert any(p.half > 5.0 for p, _ in roads)
    # sidewalk=both gave the trunk a curb + walk band.
    trunk = next(p for p, _ in roads if p.prio == 90)
    assert trunk.curb > 0 and trunk.walk > 0 and trunk.shoulder == 0


def test_cross_section(tmp_path, projection):
    # Residential road east-west through the origin.
    path = _write(tmp_path, [
        _way(1, {"highway": "residential"}, [(-60.0, 0.0), (60.0, 0.0)]),
    ])
    src = RoadSource(path, projection, extent_m=90.0)
    # Residential: half=3.5, curb=0.5, walk=1.5; unmarked (lanes=1).
    assert src.road_class(0.0, 0.0) == ROAD_ASPHALT
    assert src.road_class(0.0, 2.0) == ROAD_ASPHALT
    assert src.road_class(0.0, 4.0) == ROAD_CURB
    assert src.road_class(0.0, 5.0) == ROAD_SIDEWALK
    assert src.road_class(0.0, 7.0) == ROAD_NONE
    # Symmetric on the other side.
    assert src.road_class(0.0, -5.0) == ROAD_SIDEWALK


def test_unpaved_track(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"highway": "track"}, [(-40.0, 0.0), (40.0, 0.0)]),
    ])
    src = RoadSource(path, projection, extent_m=80.0)
    assert src.road_class(0.0, 0.0) == ROAD_TRACK
    assert src.road_class(0.0, 4.0) == ROAD_NONE


def test_priority_at_intersection(tmp_path, projection):
    # Secondary avenue north-south crossing a residential street.
    path = _write(tmp_path, [
        _way(1, {"highway": "residential"}, [(-60.0, 0.0), (60.0, 0.0)]),
        _way(2, {"highway": "secondary"}, [(0.0, -60.0), (0.0, 60.0)]),
    ])
    src = RoadSource(path, projection, extent_m=90.0)
    # At the crossing the secondary road's surface wins (prio 70 > 40).
    assert src.road_class(0.0, 0.0) == ROAD_MARKING
    # Residential sidewalk inside the avenue's asphalt is overwritten —
    # the sidewalk stops at the street it crosses.
    assert src.road_class(4.5, 4.5) != ROAD_SIDEWALK
    # But a sidewalk cell clear of the avenue survives.
    assert src.road_class(30.0, 5.0) == ROAD_SIDEWALK
    # And the avenue's own sidewalk band takes over beyond its curb.
    assert src.road_class(6.0, 4.0) == ROAD_SIDEWALK
