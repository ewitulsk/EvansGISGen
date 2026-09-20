"""RoadSource tests: OSM highways -> per-cell road surface classes."""

import json
import math

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
    by_hw = sorted(roads, key=lambda r: r.params.half)
    # lanes=4 widened the second residential way past the default.
    assert any(f.params.half > 5.0 for f in roads)
    # sidewalk=both gave the trunk a curb + walk band.
    trunk = next(f for f in roads if f.params.prio == 90)
    assert trunk.params.curb > 0 and trunk.params.walk > 0 \
        and trunk.params.shoulder == 0


def test_cross_section(tmp_path, projection):
    # Residential road east-west through the origin.
    path = _write(tmp_path, [
        _way(1, {"highway": "residential"}, [(-60.0, 0.0), (60.0, 0.0)]),
    ])
    src = RoadSource(path, projection, bounds=(-90.0, -90.0, 90.0, 90.0))
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
    src = RoadSource(path, projection, bounds=(-80.0, -80.0, 80.0, 80.0))
    assert src.road_class(0.0, 0.0) == ROAD_TRACK
    assert src.road_class(0.0, 4.0) == ROAD_NONE


def test_priority_at_intersection(tmp_path, projection):
    # Secondary avenue north-south crossing a residential street.
    path = _write(tmp_path, [
        _way(1, {"highway": "residential"}, [(-60.0, 0.0), (60.0, 0.0)]),
        _way(2, {"highway": "secondary"}, [(0.0, -60.0), (0.0, 60.0)]),
    ])
    src = RoadSource(path, projection, bounds=(-90.0, -90.0, 90.0, 90.0))
    # At the crossing the secondary road's surface wins (prio 70 > 40).
    assert src.road_class(0.0, 0.0) == ROAD_MARKING
    # Residential sidewalk inside the avenue's asphalt is overwritten —
    # the sidewalk stops at the street it crosses.
    assert src.road_class(4.5, 4.5) != ROAD_SIDEWALK
    # But a sidewalk cell clear of the avenue survives.
    assert src.road_class(30.0, 5.0) == ROAD_SIDEWALK
    # And the avenue's own sidewalk band takes over beyond its curb.
    assert src.road_class(6.0, 4.0) == ROAD_SIDEWALK


def test_corridor_polylines(tmp_path, projection):
    # Only trunk/motorway ways become corridor primitives; primary and
    # residential roads rasterize but don't claim corridor influence.
    from geoworld_compiler.roads import corridor_polylines
    path = _write(tmp_path, [
        _way(1, {"highway": "trunk", "ref": "US 77"},
             [(0.0, -50.0), (0.0, 50.0)]),
        _way(2, {"highway": "primary", "ref": "US 136"},
             [(-50.0, 0.0), (50.0, 0.0)]),
        _way(3, {"highway": "residential"}, [(-30.0, 30.0), (30.0, 30.0)]),
    ])
    lines = corridor_polylines(path, projection)
    assert len(lines) == 1
    (e0, n0), (e1, n1) = lines[0]
    assert e0 == pytest.approx(0.0, abs=0.5)
    assert n0 == pytest.approx(-50.0, abs=0.5)
    assert n1 == pytest.approx(50.0, abs=0.5)


def test_link_classes_parse(projection):
    # motorway_link ramps were previously dropped — they carry the
    # cloverleaf loops, so they must parse with a narrow cross-section.
    roads = parse_osm_roads({"elements": [
        _way(1, {"highway": "motorway_link", "bridge": "yes", "layer": "1"},
             [(0.0, 0.0), (60.0, 0.0)]),
    ]}, projection)
    assert len(roads) == 1
    f = roads[0]
    assert f.bridge and f.level == 1 and f.params.half == 4.0


def test_bridge_deck_clears_crossing(tmp_path, projection):
    # An east-west bridge way crosses a north-south street at grade.
    # Flat DEM at 100 m: the deck must rise ~5 m over the street and
    # land back at grade at its endpoints (which touch the street).
    path = _write(tmp_path, [
        _way(1, {"highway": "secondary"},
             [(0.0, -80.0), (0.0, 80.0)]),
        _way(2, {"highway": "secondary", "bridge": "yes", "layer": "1"},
             [(-80.0, 0.0), (80.0, 0.0)]),
    ])
    elev = lambda e, n: 100.0
    src = RoadSource(path, projection,
                     bounds=(-120.0, -120.0, 120.0, 120.0),
                     elev_m=elev)
    # Over the crossing the deck floats ~5 m up.
    assert src.deck_m(0.0, 0.0) == pytest.approx(105.0, abs=1.0)
    assert src.deck_class(0.0, 0.0) in (ROAD_ASPHALT, ROAD_MARKING)
    # The lower street still paves at grade under the deck.
    assert src.road_class(0.0, 0.0) in (ROAD_ASPHALT, ROAD_MARKING)
    # Deck fades to grade along the span (8% envelope).
    assert src.deck_m(-79.0, 0.0) < 103.0
    # Clear of the crossing the bridge way's own band has no deck.
    assert src.deck_m(0.0, 30.0) < 0.0


def test_street_name_sign_at_intersection(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"highway": "residential", "name": "South 14th Street"},
             [(-60.0, 0.0), (0.0, 0.0), (60.0, 0.0)]),
        _way(2, {"highway": "secondary", "name": "Court Street"},
             [(0.0, -60.0), (0.0, 0.0), (0.0, 60.0)]),
    ])
    src = RoadSource(path, projection, bounds=(-90.0, -90.0, 90.0, 90.0))
    name_signs = [s for s in src.signs if s["type"] == "street_name"]
    assert len(name_signs) == 1
    s = name_signs[0]
    assert s["lines"] == ["COURT ST", "S 14TH ST"]
    # Offset past both road widths, off the pavement.
    assert math.hypot(s["e"], s["n"]) > 5.0


def test_stop_sign_from_node(tmp_path, projection):
    path = _write(tmp_path, [
        _way(1, {"highway": "residential"},
             [(-60.0, 0.0), (60.0, 0.0)]),
        {"type": "node", "id": 7, "tags": {"highway": "stop"},
         "lat": _way(0, {}, [(10.0, 0.0)])["geometry"][0]["lat"],
         "lon": _way(0, {}, [(10.0, 0.0)])["geometry"][0]["lon"]},
    ])
    src = RoadSource(path, projection, bounds=(-90.0, -90.0, 90.0, 90.0))
    stops = [s for s in src.signs if s["type"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["lines"] == ["STOP"]
    assert abs(stops[0]["e"] - 10.0) < 8.0  # offset right of the node
