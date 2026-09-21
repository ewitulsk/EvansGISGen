"""BuildingSource tests: OSM building polygons -> per-cell class + levels."""

import json

import pytest

from geoworld_compiler.buildings import (
    BUILDING_CHURCH, BUILDING_CIVIC, BUILDING_FUEL, BUILDING_GENERIC,
    BUILDING_INDUSTRIAL, BUILDING_NONE, BUILDING_OUTBUILDING,
    BUILDING_RESIDENTIAL, BUILDING_RESTAURANT, BUILDING_SCHOOL,
    BUILDING_SUPERMARKET, BuildingSource,
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
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.building_class(0.0, 0.0) == BUILDING_RESIDENTIAL
    assert src.building_levels(0.0, 0.0) == 2
    assert src.building_class(60.0, 0.0) == BUILDING_INDUSTRIAL
    assert src.building_class(0.0, 60.0) == BUILDING_SCHOOL
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
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
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
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.building_class(10.0, 10.0) == BUILDING_RESIDENTIAL


def _ms(tmp_path, features):
    """features: list of (coords (east,north) meters, height)."""
    import pyproj
    proj = Projection(CRS, *ANCHOR)
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    feats = [{"coords": [list(inv.transform(e + proj.anchor_east,
                                           n + proj.anchor_north))
                         for e, n in coords],
              "height": h}
             for coords, h in features]
    p = tmp_path / "ms_buildings.json"
    p.write_text(json.dumps({"features": feats}))
    return p


def test_ms_footprints_generic(tmp_path, projection):
    ms = _ms(tmp_path, [
        (_poly(0.0, 0.0, 10.0), -1.0),          # no height -> default levels
        (_poly(60.0, 0.0, 10.0), 9.0),          # 9 m -> 3 floors
    ])
    src = BuildingSource(None, projection, bounds=(-200.0, -200.0, 200.0, 200.0),
                         ms_json_path=ms)
    assert src.building_class(0.0, 0.0) == BUILDING_GENERIC
    assert src.building_levels(0.0, 0.0) == 2
    assert src.building_class(60.0, 0.0) == BUILDING_GENERIC
    assert src.building_levels(60.0, 0.0) == 3
    assert src.building_class(-60.0, 0.0) == BUILDING_NONE


def test_osm_overrides_ms(tmp_path, projection):
    # Both datasets cover the cell: OSM's real class + levels must win.
    osm = _write(tmp_path, [
        _way(1, {"building": "school", "building:levels": "4"},
             _poly(0.0, 0.0, 10.0)),
    ])
    ms = _ms(tmp_path, [(_poly(0.0, 0.0, 12.0), -1.0)])
    src = BuildingSource(osm, projection, bounds=(-200.0, -200.0, 200.0, 200.0), ms_json_path=ms)
    assert src.building_class(0.0, 0.0) == BUILDING_SCHOOL
    assert src.building_levels(0.0, 0.0) == 4
    # MS polygon sticks out past the OSM one: residue stays GENERIC.
    assert src.building_class(11.0, 0.0) == BUILDING_GENERIC


def test_requires_at_least_one_input(tmp_path, projection):
    with pytest.raises(ValueError):
        BuildingSource(None, projection, bounds=(-200.0, -200.0, 200.0, 200.0))


def test_adjacent_buildings_get_distinct_ids(tmp_path, projection):
    # Row houses sharing an edge must carry different instance ids so the
    # runtime can wall between them.
    path = _write(tmp_path, [
        _way(1, {"building": "house"}, _poly(0.0, 0.0, 10.0)),
        _way(2, {"building": "house"},
             [(10.0, -10.0), (30.0, -10.0), (30.0, 10.0),
              (10.0, 10.0), (10.0, -10.0)]),
    ])
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    id_a = src.building_id(0.0, 0.0)
    id_b = src.building_id(20.0, 0.0)
    assert id_a != 0 and id_b != 0 and id_a != id_b
    assert src.building_id(-60.0, -60.0) == 0


def test_roof_uniform_per_instance(tmp_path, projection):
    # A footprint straddling a slope gets ONE roof height, padded to the
    # highest ground under it — no per-cell tearing.
    from geoworld_compiler.transform import GeoTransform

    def elev(east, north):
        return 100.0 + east * 0.5  # ground rises eastward

    transform = GeoTransform(origin_x=0, origin_z=0,
                             horizontal_meters_per_block=1.0,
                             vertical_meters_per_block=1.0,
                             datum_elevation_meters=0.0, datum_y=0)
    path = _write(tmp_path, [
        _way(1, {"building": "house", "building:levels": "2"},
             _poly(0.0, 0.0, 20.0)),
    ])
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0),
                         elev_m=elev, transform=transform)
    # Max ground over the bbox: e=+20 -> 110 m -> block 110; roof = 110+7.
    assert src.building_roof(-15.0, 0.0) == 117
    assert src.building_roof(15.0, 0.0) == 117


def test_roof_nodata_without_dem(tmp_path, projection):
    # No DEM sampler: ids still emit, roofs read NODATA (runtime falls back).
    from geoworld_compiler.tileio import NODATA

    path = _write(tmp_path, [
        _way(1, {"building": "house"}, _poly(0.0, 0.0, 10.0)),
    ])
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.building_id(0.0, 0.0) != 0
    assert src.building_roof(0.0, 0.0) == NODATA


def test_roof_shell_does_not_donut_ms(tmp_path, projection):
    # OSM building=roof paints BELOW Microsoft: it must not carve a hole
    # in the MS footprint covering the same ground.
    osm = _write(tmp_path, [
        _way(1, {"building": "roof"}, _poly(0.0, 0.0, 12.0)),
    ])
    ms = _ms(tmp_path, [(_poly(0.0, 0.0, 12.0), -1.0)])
    src = BuildingSource(osm, projection,
                         bounds=(-200.0, -200.0, 200.0, 200.0),
                         ms_json_path=ms)
    assert src.building_class(0.0, 0.0) == BUILDING_GENERIC


def _node(el_id, tags, east, north):
    """POI node at geo (east, north) meters relative to the anchor."""
    import pyproj
    proj = Projection(CRS, *ANCHOR)
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    lon, lat = inv.transform(east + proj.anchor_east,
                             north + proj.anchor_north)
    return {"type": "node", "id": el_id, "tags": tags,
            "lat": lat, "lon": lon}


def _pois(tmp_path, nodes):
    p = tmp_path / "pois.json"
    p.write_text(json.dumps({"elements": nodes}))
    return p


def test_use_tags_reclassify(tmp_path, projection):
    # Phase 16: amenity/shop on the way outranks a weak building= value.
    path = _write(tmp_path, [
        _way(1, {"building": "yes", "amenity": "restaurant"},
             _poly(0.0, 0.0, 10.0)),
        _way(2, {"building": "yes", "shop": "supermarket"},
             _poly(60.0, 0.0, 15.0)),
        _way(3, {"building": "church", "amenity": "restaurant"},
             _poly(0.0, 60.0, 15.0)),
        _way(4, {"building": "yes", "amenity": "fuel"},
             _poly(60.0, 60.0, 8.0)),
    ])
    src = BuildingSource(path, projection, bounds=(-200.0, -200.0, 200.0, 200.0))
    assert src.building_class(0.0, 0.0) == BUILDING_RESTAURANT
    assert src.building_class(60.0, 0.0) == BUILDING_SUPERMARKET
    # building=church is a specific value — amenity does not override it.
    assert src.building_class(0.0, 60.0) == BUILDING_CHURCH
    assert src.building_class(60.0, 60.0) == BUILDING_FUEL


def test_poi_node_join(tmp_path, projection):
    # Phase 16: a POI node inside a weak footprint reclassifies it.
    osm = _write(tmp_path, [
        _way(1, {"building": "yes"}, _poly(0.0, 0.0, 15.0)),
        _way(2, {"building": "house"}, _poly(60.0, 0.0, 15.0)),
        _way(3, {"building": "church"}, _poly(0.0, 60.0, 15.0)),
    ])
    pois = _pois(tmp_path, [
        _node(1, {"amenity": "fuel"}, 0.0, 0.0),
        _node(2, {"amenity": "restaurant"}, 60.0, 0.0),
        _node(3, {"amenity": "restaurant"}, 0.0, 60.0),
    ])
    src = BuildingSource(osm, projection, bounds=(-200.0, -200.0, 200.0, 200.0),
                         pois_json_path=pois)
    assert src.building_class(0.0, 0.0) == BUILDING_FUEL
    assert src.building_class(60.0, 0.0) == BUILDING_RESTAURANT
    # A church is a specific classification — the POI cannot override it.
    assert src.building_class(0.0, 60.0) == BUILDING_CHURCH
