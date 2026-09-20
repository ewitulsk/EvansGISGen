"""parcels.py tests: address normalization, parcel compile, lookup, export."""

import gzip
import json
import struct

import pytest
import pyproj

from geoworld_compiler.parcels import (
    _point_in_rings,
    add_osm_addresses,
    compile_parcels,
    export_parcel,
    normalize_address,
    parcel_at,
    resolve_parcels_query,
)
from geoworld_compiler.transform import Projection

CRS = "EPSG:32614"
ANCHOR = (40.2681, -96.7470)


@pytest.fixture(scope="module")
def projection():
    return Projection(CRS, *ANCHOR)


def _poly_geojson(coords_m, projection):
    """(east, north) meters -> GeoJSON ring in WGS84."""
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    return [[list(inv.transform(e + projection.anchor_east,
                                n + projection.anchor_north))
             for e, n in coords_m]]


def _feature(pid, address, coords_m, projection):
    return {"type": "Feature",
            "properties": {"PARCELID": pid, "SITEADDRESS": address},
            "geometry": {"type": "Polygon",
                         "coordinates": _poly_geojson(coords_m, projection)}}


# A 20x30 m lot just east of the anchor: e in [100,120], n in [-80,-50].
LOT = [(100, -80), (120, -80), (120, -50), (100, -50), (100, -80)]


@pytest.fixture()
def parcels_json(tmp_path, projection):
    gj = tmp_path / "parcels.geojson"
    gj.write_text(json.dumps({"type": "FeatureCollection", "features": [
        _feature("P1", "420 N 6TH ST, BEATRICE, NE, 68310", LOT, projection),
        _feature("P2", None, [(200, -80), (220, -80), (220, -50), (200, -50)],
                 projection),
    ]}))
    out = tmp_path / "parcels.json"
    compile_parcels([gj], projection, out)
    return out


# --- normalization ----------------------------------------------------------

def test_normalize_folds_suffixes_and_tail():
    assert normalize_address("2819 S 16th Street, Lincoln, NE, 68502") \
        == "2819 S 16TH ST"
    assert normalize_address("420 N. 6th St") == "420 N 6TH ST"


def test_normalize_empty():
    assert normalize_address("") is None
    assert normalize_address(None) is None


# --- compile + lookup -------------------------------------------------------

def test_compile_projects_rings_and_bbox(parcels_json):
    data = json.loads(parcels_json.read_text())
    p1 = next(p for p in data["parcels"] if p["id"] == "P1")
    e0, n0, e1, n1 = p1["bbox"]
    assert e0 == pytest.approx(100, abs=0.5)
    assert e1 == pytest.approx(120, abs=0.5)
    assert n0 == pytest.approx(-80, abs=0.5)
    assert n1 == pytest.approx(-50, abs=0.5)


def test_gazetteer_from_situs(parcels_json):
    data = json.loads(parcels_json.read_text())
    addrs = {a["text"]: a for a in data["addresses"]}
    assert "420 N 6TH ST" in addrs
    assert addrs["420 N 6TH ST"]["parcel"] == "P1"


def test_parcel_at_coordinates(parcels_json):
    p = parcel_at(parcels_json, 110, -60)
    assert p is not None and p["id"] == "P1"
    # Just outside the lot line.
    assert parcel_at(parcels_json, 99, -60) is None


def test_resolve_query_by_address_and_coords(parcels_json):
    assert resolve_parcels_query(parcels_json, "420 N 6TH ST")["id"] == "P1"
    assert resolve_parcels_query(parcels_json, "110,-60")["id"] == "P1"
    assert resolve_parcels_query(parcels_json, "210,-60")["id"] == "P2"
    assert resolve_parcels_query(parcels_json, "999 nowhere") is None


def test_point_in_rings_hole():
    outer = [[0, 0], [10, 0], [10, 10], [0, 10]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6]]
    assert _point_in_rings(2, 2, [outer, hole])
    assert not _point_in_rings(5, 5, [outer, hole])


def test_osm_addresses_fill_gazetteer_gap(parcels_json, projection, tmp_path):
    inv = pyproj.Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    lon, lat = inv.transform(210 + projection.anchor_east,
                             -60 + projection.anchor_north)
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({"elements": [{
        "type": "node", "lat": lat, "lon": lon,
        "tags": {"addr:housenumber": "500", "addr:street": "N 6th St"}}]}))
    added = add_osm_addresses([osm], projection, parcels_json)
    assert added == 1
    # The unaddressed P2 parcel is now reachable through the OSM entry's point.
    data = json.loads(parcels_json.read_text())
    hit = next(a for a in data["addresses"] if a["text"] == "500 N 6TH ST")
    assert hit["parcel"] is None
    p = parcel_at(parcels_json, hit["east"], hit["north"])
    assert p["id"] == "P2"


# --- export ------------------------------------------------------------------

def _read_nbt_size(path):
    raw = gzip.decompress(path.read_bytes())
    # Root compound -> first tag is "size" int list: tid(9) + len + name +
    # int list header + 3 ints.
    tid, name_len = raw[0], struct.unpack(">H", raw[1:3])[0]
    assert tid == 10 and name_len == 0
    assert raw[3] == 9  # TAG_List
    ln = struct.unpack(">H", raw[4:6])[0]
    assert raw[6:6 + ln] == b"size"
    o = 6 + ln
    assert raw[o] == 3  # int payload
    count = struct.unpack(">i", raw[o + 1:o + 5])[0]
    vals = struct.unpack(">iii", raw[o + 5:o + 5 + 4 * count])
    return vals


def test_export_parcel_writes_outline_nbt(parcels_json, tmp_path):
    out = tmp_path / "lot.nbt"
    export_parcel(parcels_json, "420 N 6TH ST", out)
    sx, sy, sz = _read_nbt_size(out)
    assert sx == pytest.approx(21, abs=1)
    assert sz == pytest.approx(31, abs=1)
    assert sy == 2


def test_export_parcel_unknown_query(parcels_json, tmp_path):
    with pytest.raises(ValueError):
        export_parcel(parcels_json, "1 nonexistent ave",
                      tmp_path / "x.nbt")


def test_export_region_multi_parcel(parcels_json, tmp_path):
    from geoworld_compiler.parcels import parcels_in_rect

    # Rect spanning both P1 (100..120) and P2 (200..220) parcels.
    hits = parcels_in_rect(parcels_json, 90, -90, 230, -40)
    assert {p["id"] for p in hits} == {"P1", "P2"}

    out = tmp_path / "block.nbt"
    export_parcel(parcels_json, "90,-90,230,-40", out)
    sx, sy, sz = _read_nbt_size(out)
    assert sx == pytest.approx(121, abs=1)   # union bbox e[100,220]
    assert sz == pytest.approx(31, abs=1)



