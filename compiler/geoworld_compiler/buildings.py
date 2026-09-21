"""Buildings (Phase 8/15): OSM + Microsoft ML footprints -> class + levels.

`fetch_buildings` pulls `building` ways via Overpass; `fetch_buildings_ms`
pulls Microsoft's GlobalMLBuildingFootprints (Bing imagery ML detections)
for the quadkey tiles covering the bbox. `BuildingSource` merges both into
four aligned grids:

    building         u8 building class (0 = none)
    building_levels  u8 floor count (from building:levels / height tags /
                     MS height estimates, else a per-class default)
    building_id      u16 footprint-instance id (0 = none) — Phase 15
    building_roof    i16 absolute roof top block-Y per column — Phase 15

Microsoft footprints paint at the lowest priority as GENERIC — OSM's real
classes and level tags win wherever both datasets cover the same cells.
`building=roof` polygons paint *below* even Microsoft: they are covering
shells of a larger structure, and painting them as siblings would carve a
donut into the parent footprint.

Phase 15 gives every polygon its own instance id (a deterministic hash of
its centroid, so ids are stable across builds) and a single roof height
computed from the highest DEM ground sampled across its bbox. The runtime
draws a wall wherever a neighbor has a different id — abutting row
buildings get party walls instead of merging — and raises one flat roof
per instance instead of per-cell roofs that tear on slopes.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from rasterio.features import rasterize

from .banded import BandedGrid, geom_bbox, rects_intersect
from .tileio import NODATA
from .transform import GeoTransform, Projection

OVERPASS = "https://overpass-api.de/api/interpreter"
MS_LINKS = ("https://minedbuildings.z5.web.core.windows.net/"
            "global-buildings/dataset-links.csv")

# Building class ids — shared with the Java runtime (BuildTheme).
BUILDING_NONE = 0
BUILDING_RESIDENTIAL = 1
BUILDING_COMMERCIAL = 2
BUILDING_INDUSTRIAL = 3
BUILDING_CIVIC = 4
BUILDING_OUTBUILDING = 5
BUILDING_GENERIC = 6
# Phase 16 taxonomy — use-specific classes from building=*/POI tags.
BUILDING_SUPERMARKET = 7
BUILDING_RESTAURANT = 8
BUILDING_FUEL = 9
BUILDING_SCHOOL = 10
BUILDING_CHURCH = 11
BUILDING_HOSPITAL = 12
BUILDING_HOTEL = 13
BUILDING_PARKING = 14
BUILDING_SPORTS = 15
BUILDING_AGRICULTURAL = 16
BUILDING_CAR = 17
BUILDING_STORAGE = 18

# Paint priorities: ascending draw order — highest wins per cell. MS
# footprints (5) sit under every OSM class; building=roof shells (3) sit
# under MS so they only fill ground nothing else claims.
_PRIO_MS = 5
_PRIO_ROOF_SHELL = 3

# building=* value -> (class, default levels, paint priority).
_BUILDING_CLASSES: list[tuple[frozenset[str], int, int, int]] = [
    (frozenset({"garage", "garages", "shed", "outbuilding", "carport",
                "hut", "storage_tank", "silo"}),
     BUILDING_OUTBUILDING, 1, 10),
    (frozenset({"house", "detached", "residential", "apartments", "terrace",
                "dormitory", "cabin", "bungalow", "semidetached_house",
                "farm", "static_caravan"}),
     BUILDING_RESIDENTIAL, 2, 20),
    (frozenset({"industrial", "warehouse", "manufacture"}),
     BUILDING_INDUSTRIAL, 2, 30),
    (frozenset({"retail", "commercial", "office", "shop", "kiosk"}),
     BUILDING_COMMERCIAL, 3, 40),
    (frozenset({"supermarket", "mall", "big_box"}),
     BUILDING_SUPERMARKET, 1, 42),
    (frozenset({"restaurant"}), BUILDING_RESTAURANT, 1, 42),
    (frozenset({"hotel"}), BUILDING_HOTEL, 4, 42),
    (frozenset({"parking"}), BUILDING_PARKING, 3, 42),
    (frozenset({"stadium", "grandstand", "sports_hall", "sports_centre"}),
     BUILDING_SPORTS, 2, 42),
    (frozenset({"barn", "farm_auxiliary", "stable", "cowshed", "sty",
                "greenhouse"}),
     BUILDING_AGRICULTURAL, 1, 42),
    (frozenset({"storage"}), BUILDING_STORAGE, 1, 42),
    (frozenset({"school", "college", "university", "kindergarten"}),
     BUILDING_SCHOOL, 2, 45),
    (frozenset({"church", "cathedral", "chapel", "religious"}),
     BUILDING_CHURCH, 2, 45),
    (frozenset({"hospital"}), BUILDING_HOSPITAL, 4, 45),
    (frozenset({"civic", "public", "government", "fire_station",
                "train_station", "transportation"}),
     BUILDING_CIVIC, 3, 50),
]
_GENERIC = (BUILDING_GENERIC, 2, 15)

# Use-specific tags on the way itself outrank a generic building= value:
# building=yes + amenity=restaurant is a restaurant, not a generic box.
# (tag key, value set) -> (class, default levels). Same table drives the
# POI-node join — a node inside a weak footprint reclassifies it.
_POI_TAG_CLASSES: list[tuple[str, frozenset[str], int, int]] = [
    ("amenity", frozenset({"fuel", "charging_station"}), BUILDING_FUEL, 1),
    ("amenity", frozenset({"hospital", "clinic"}), BUILDING_HOSPITAL, 4),
    ("amenity", frozenset({"doctors", "dentist", "pharmacy", "veterinary"}),
     BUILDING_HOSPITAL, 1),
    ("healthcare", frozenset({"hospital", "clinic", "centre"}),
     BUILDING_HOSPITAL, 3),
    ("amenity", frozenset({"restaurant", "fast_food", "cafe", "bar", "pub",
                           "food_court", "ice_cream", "biergarten"}),
     BUILDING_RESTAURANT, 1),
    ("amenity", frozenset({"school", "college", "university", "kindergarten",
                           "driving_school", "music_school"}),
     BUILDING_SCHOOL, 2),
    ("amenity", frozenset({"place_of_worship"}), BUILDING_CHURCH, 2),
    ("amenity", frozenset({"parking"}), BUILDING_PARKING, 3),
    ("amenity", frozenset({"car_wash", "car_rental", "vehicle_inspection"}),
     BUILDING_CAR, 1),
    ("shop", frozenset({"supermarket", "department_store", "mall",
                        "wholesale", "hypermarket"}),
     BUILDING_SUPERMARKET, 1),
    ("shop", frozenset({"car", "car_repair", "car_parts", "tyres",
                        "motorcycle"}), BUILDING_CAR, 1),
    ("shop", frozenset({"storage_rental"}), BUILDING_STORAGE, 1),
    ("tourism", frozenset({"hotel", "motel", "hostel", "guest_house"}),
     BUILDING_HOTEL, 4),
    ("leisure", frozenset({"sports_centre", "stadium", "fitness_centre",
                           "golf_course", "bowling_alley", "ice_rink"}),
     BUILDING_SPORTS, 2),
    ("healthcare", frozenset({"doctor", "dentist", "pharmacy"}),
     BUILDING_HOSPITAL, 1),
]

# Classes a POI node may reclassify — generic/weak classifications lose to
# a real use tag; specific ones (a church, a garage) stand.
_POI_UPGRADABLE = frozenset({BUILDING_GENERIC, BUILDING_COMMERCIAL,
                             BUILDING_RESIDENTIAL, BUILDING_INDUSTRIAL})

# Spacing (m) of the DEM sample grid used to find each instance's highest
# ground. The pad sits at the bbox maximum so no footprint cell buries its
# floor — a generous skirt is harmless, a buried floor is a torn building.
_ROOF_SAMPLE_STEP_M = 10.0


def fetch_buildings(min_lon: float, min_lat: float, max_lon: float,
                    max_lat: float, out_path: str | Path) -> Path:
    """Download OSM building footprints in a WGS84 bbox as raw JSON."""
    query = f"""[out:json][timeout:120];
way["building"]({min_lat},{min_lon},{max_lat},{max_lon});
out geom;"""
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=body,
                                 headers={"User-Agent": "geoworld-compiler/0.1"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.load(resp)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))
    return out


def fetch_pois(min_lon: float, min_lat: float, max_lon: float,
               max_lat: float, out_path: str | Path) -> Path:
    """Download OSM POI nodes (amenity/shop/office/tourism/leisure/
    healthcare/craft) in a WGS84 bbox as raw JSON.

    Most real-world use tags live on *nodes inside* the building
    footprint rather than on the way — the node query is what lets the
    compiler reclassify a generic footprint as the restaurant or fuel
    station it actually is (Phase 16).
    """
    query = f"""[out:json][timeout:180];
(
node["amenity"]({min_lat},{min_lon},{max_lat},{max_lon});
node["shop"]({min_lat},{min_lon},{max_lat},{max_lon});
node["office"]({min_lat},{min_lon},{max_lat},{max_lon});
node["tourism"]({min_lat},{min_lon},{max_lat},{max_lon});
node["leisure"]({min_lat},{min_lon},{max_lat},{max_lon});
node["healthcare"]({min_lat},{min_lon},{max_lat},{max_lon});
node["craft"]({min_lat},{min_lon},{max_lat},{max_lon});
);
out body;"""
    body = urllib.parse.urlencode({"data": query}).encode()
    req = urllib.request.Request(OVERPASS, data=body,
                                 headers={"User-Agent": "geoworld-compiler/0.1"})
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.load(resp)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))
    return out


def _quadkey_bounds(qk: str) -> tuple[float, float, float, float]:
    """WGS84 (min_lon, min_lat, max_lon, max_lat) of a Bing quadkey tile."""
    x = y = 0
    z = len(qk)
    for i, c in enumerate(qk):
        bit = z - i - 1
        d = int(c)
        if d & 1:
            x |= 1 << bit
        if d & 2:
            y |= 1 << bit
    n = 1 << z
    lon0 = x / n * 360.0 - 180.0
    lon1 = (x + 1) / n * 360.0 - 180.0
    lat1 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat0 = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon0, lat0, lon1, lat1


def fetch_buildings_ms(min_lon: float, min_lat: float, max_lon: float,
                       max_lat: float, out_path: str | Path) -> Path:
    """Download Microsoft GlobalML footprints intersecting a WGS84 bbox.

    The dataset is partitioned by zoom-9 quadkey (each quadkey may span
    several gzipped GeoJSONL part files). Only tiles intersecting the bbox
    are downloaded; features are bbox-filtered and stored compactly as
    {"features": [{"coords": [[lon, lat], ...], "height": m}, ...]} with
    height = -1 where Microsoft has no estimate.
    """
    req = urllib.request.Request(MS_LINKS,
                                 headers={"User-Agent": "geoworld-compiler/0.1"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        rows = csv.DictReader(io.StringIO(resp.read().decode("utf-8",
                                                             "replace")))
        urls = [r["Url"] for r in rows
                if _quadkey_bounds(r["QuadKey"])[0] < max_lon
                and _quadkey_bounds(r["QuadKey"])[2] > min_lon
                and _quadkey_bounds(r["QuadKey"])[1] < max_lat
                and _quadkey_bounds(r["QuadKey"])[3] > min_lat]

    features = []
    for url in urls:
        req = urllib.request.Request(
            url, headers={"User-Agent": "geoworld-compiler/0.1"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            text = gzip.decompress(resp.read()).decode("utf-8", "replace")
        for line in text.splitlines():
            if not line.strip():
                continue
            feat = json.loads(line)
            ring = feat.get("geometry", {}).get("coordinates", [[]])[0]
            if not ring:
                continue
            lons = [p[0] for p in ring]
            lats = [p[1] for p in ring]
            if (min(lons) > max_lon or max(lons) < min_lon
                    or min(lats) > max_lat or max(lats) < min_lat):
                continue
            height = feat.get("properties", {}).get("height", -1.0)
            features.append({"coords": ring, "height": height})

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"features": features}))
    return out


def _poi_class(tags: dict) -> tuple[int, int, int] | None:
    """(class, default levels, rule rank) from use-specific tags, or None.

    The rank is the matched rule's position — when several POIs land in
    one weak footprint, the lowest-ranked (most specific) rule wins.
    """
    for i, (key, values, cls, levels) in enumerate(_POI_TAG_CLASSES):
        if tags.get(key) in values:
            return cls, levels, i
    # Bare shop/office tags are commercial use without a finer reading.
    if "shop" in tags or "office" in tags or "craft" in tags:
        return BUILDING_COMMERCIAL, 1, len(_POI_TAG_CLASSES)
    return None


def _classify(tags: dict) -> tuple[int, int, int]:
    """(class, levels, priority) for a building way."""
    cls, default_levels, prio = _GENERIC
    value = tags.get("building", "yes")
    for values, c, lv, p in _BUILDING_CLASSES:
        if value in values:
            cls, default_levels, prio = c, lv, p
            break
    # Use-specific tags (amenity/shop/...) outrank a weak building= read:
    # a building=yes carrying amenity=restaurant is a restaurant. They do
    # not override a specific building= value (a church stays a church).
    poi = _poi_class(tags)
    if poi is not None and cls in _POI_UPGRADABLE:
        cls, default_levels = poi[0], poi[1]
        prio = max(prio, 42)
    levels = default_levels
    try:
        if "building:levels" in tags:
            levels = max(1, int(float(tags["building:levels"])))
        elif "height" in tags:
            levels = max(1, round(float(tags["height"]) / 3.0))
    except (TypeError, ValueError):
        pass  # unparseable tag values keep the class default
    return cls, min(255, levels), prio


def _instance_id(coords: list[tuple[float, float]]) -> int:
    """Stable 1..65535 id for a footprint, hashed from its centroid.

    Ids only need to distinguish *adjacent* instances — a collision merges
    two touching buildings visually, which a uniform 16-bit spread keeps
    rare and deterministic across rebuilds.
    """
    ce = sum(p[0] for p in coords) / len(coords)
    cn = sum(p[1] for p in coords) / len(coords)
    h = (int(ce * 37) * 2654435761) ^ (int(cn * 37) * 40503)
    return 1 + ((h & 0x7FFFFFFF) % 65535)


def _ring_area(coords: list[tuple[float, float]]) -> float:
    """Signed-ring (shoelace) area of a polygon in geo meters."""
    a = 0.0
    for (x0, y0), (x1, y1) in zip(coords, coords[1:]):
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _point_in_ring(ring: list[tuple[float, float]],
                   east: float, north: float) -> bool:
    """Even-odd ray cast: is geo point (east, north) inside the ring?"""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if ((yi > north) != (yj > north)) and (
                east < (xj - xi) * (north - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _bbox_grid(polys: list[tuple[int, int, int, tuple, dict]],
               cell_m: float = 128.0
               ) -> dict[tuple[int, int], list[int]]:
    """Polygon indices bucketed by the grid cells their bbox overlaps —
    cheap spatial index for point/neighborhood lookups."""
    grid: dict[tuple[int, int], list[int]] = {}
    for i, (_prio, _cls, _lv, bbox, _g) in enumerate(polys):
        ce0, cn0 = int(bbox[0] // cell_m), int(bbox[1] // cell_m)
        ce1, cn1 = int(bbox[2] // cell_m), int(bbox[3] // cell_m)
        for ce in range(ce0, ce1 + 1):
            for cn in range(cn0, cn1 + 1):
                grid.setdefault((ce, cn), []).append(i)
    return grid


def _join_pois(pois_json_path: str | Path, projection: Projection,
               polys: list[tuple[int, int, int, tuple, dict]]) -> None:
    """Reclassify weak footprints by the POI nodes inside them (Phase 16).

    A `shop=supermarket` node inside a generic footprint makes it a
    supermarket. Every containing weak polygon upgrades (MS + OSM
    duplicates of the same building both qualify); a polygon keeps the
    class of its highest-ranked POI rule, and specific classifications
    (a church, a garage) are never overridden.
    """
    data = json.loads(Path(pois_json_path).read_text())
    pois = []
    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        hit = _poi_class(el.get("tags", {}))
        if hit is None:
            continue
        cls, _lv, rank = hit
        east, north = projection.to_geo(el["lat"], el["lon"])
        pois.append((rank, cls, east, north))
    if not pois:
        return
    pois.sort(key=lambda p: p[0])  # most specific rule first

    # Grid index over polygon bboxes for cheap candidate lookup.
    cell_m = 128.0
    grid = _bbox_grid(polys, cell_m)

    claimed: set[int] = set()
    for _rank, cls, east, north in pois:
        for i in grid.get((int(east // cell_m), int(north // cell_m)), ()):
            if i in claimed:
                continue
            prio, pcls, plv, bbox, g = polys[i]
            if (pcls not in _POI_UPGRADABLE
                    or not (bbox[0] <= east <= bbox[2]
                            and bbox[1] <= north <= bbox[3])):
                continue
            if _point_in_ring(g["coordinates"][0], east, north):
                polys[i] = (prio, cls, plv, bbox, g)
                claimed.add(i)


class BuildingSource:
    """Per-cell building class + floor count from merged footprints.

    Microsoft ML footprints paint first at the lowest priority as
    GENERIC; OSM ways then overwrite them wherever both datasets cover a
    cell, so OSM classes and level tags always win. Phase 15 additionally
    emits per-instance `building_id` and `building_roof` grids — ids let
    the runtime wall between abutting footprints, and one roof height per
    instance ends per-cell roof tearing on slopes.

    `elev_m` (callable geo -> meters | None) and `transform` are needed
    for roof solving; without them the id/roof grids are still emitted but
    roof cells read NODATA and the runtime falls back to per-cell heights.
    """

    def __init__(self, osm_json_path: str | Path | None,
                 projection: Projection,
                 bounds: tuple[float, float, float, float],
                 resolution_m: float = 1.0,
                 ms_json_path: str | Path | None = None,
                 pois_json_path: str | Path | None = None,
                 elev_m=None, transform: GeoTransform | None = None):
        if osm_json_path is None and ms_json_path is None:
            raise ValueError("BuildingSource needs at least one input")

        self.bounds = bounds
        self.resolution_m = resolution_m
        e0, n0, e1, n1 = bounds
        self._shape = (math.ceil((n1 - n0) / resolution_m),
                       math.ceil((e1 - e0) / resolution_m))

        # One record per footprint: (prio, cls, levels, bbox, geom). Draw
        # order = ascending prio, so the last writer per cell is the winner
        # and the id/class/levels/roof grids all agree on who that was.
        polys: list[tuple[int, int, int, tuple, dict]] = []

        def add(coords: list[tuple[float, float]],
                cls: int, levels: int, prio: int) -> None:
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            g = {"type": "Polygon", "coordinates": [coords]}
            polys.append((prio, cls, levels, geom_bbox(g), g))

        # Microsoft ML footprints: class unknown -> GENERIC at a priority
        # below every OSM group; levels from the height estimate when present.
        if ms_json_path is not None:
            ms = json.loads(Path(ms_json_path).read_text())
            for feat in ms.get("features", []):
                ring = feat.get("coords")
                if not ring or len(ring) < 4:
                    continue
                try:
                    h = float(feat.get("height", -1.0))
                except (TypeError, ValueError):
                    h = -1.0
                levels = (max(1, min(255, round(h / 3.0))) if h > 0
                          else _GENERIC[1])
                coords = [projection.to_geo(lat, lon)
                          for lon, lat in ring]
                add(coords, BUILDING_GENERIC, levels, _PRIO_MS)

        if osm_json_path is not None:
            data = json.loads(Path(osm_json_path).read_text())
            for el in data.get("elements", []):
                if el.get("type") != "way":
                    continue
                geom = el.get("geometry")
                if not geom or len(geom) < 4:
                    continue
                tags = el.get("tags", {})
                cls, levels, prio = _classify(tags)
                if tags.get("building") == "roof":
                    # Covering shell of a larger structure — painting it as
                    # a sibling instance would donut the parent. Below MS.
                    prio = _PRIO_ROOF_SHELL
                coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
                add(coords, cls, levels, prio)

        if pois_json_path is not None:
            _join_pois(pois_json_path, projection, polys)

        polys.sort(key=lambda p: p[0])

        # Lookup tables keyed by polygon position (1-based; slot 0 = none).
        # The 16-bit instance id is only the *emitted* identity — keying
        # the tables by it would let hash collisions overwrite each other's
        # class/levels/roof, and the footprint count exceeds the id space.
        n_polys = len(polys)
        id_lut = np.zeros(n_polys + 1, dtype=np.uint16)
        cls_lut = np.zeros(n_polys + 1, dtype=np.uint8)
        lvl_lut = np.zeros(n_polys + 1, dtype=np.uint8)
        roof_lut = np.full(n_polys + 1, NODATA, dtype=np.int16)
        features: list[tuple[tuple, dict, int]] = []  # (bbox, geom, index)
        for idx, (prio, cls, levels, bbox, g) in enumerate(polys, start=1):
            ring = g["coordinates"][0]
            id_lut[idx] = _instance_id(ring)
            cls_lut[idx] = cls
            lvl_lut[idx] = levels
            roof_lut[idx] = self._roof_y(ring, levels, elev_m, transform)
            features.append((bbox, g, idx))

        building = BandedGrid(bounds, resolution_m, np.uint8)
        levels_grid = BandedGrid(bounds, resolution_m, np.uint8)
        id_grid = BandedGrid(bounds, resolution_m, np.uint16)
        roof_grid = BandedGrid(bounds, resolution_m, np.int16)
        # Polygons need no context margin — there is no distance field.
        for r0, r1, w0, w1, wt in building.band_windows(margin_m=0.0):
            wrect = building.window_rect(w0, w1)
            geoms = [(g, idx) for b, g, idx in features
                     if rects_intersect(b, wrect)]
            if geoms:
                # Rasterize polygon indices (u32 — the footprint count can
                # exceed the emitted u16 id space), then resolve every
                # layer through the index-keyed LUTs so all four grids
                # agree on which polygon painted each cell.
                win_idx = rasterize(
                    geoms, out_shape=(w1 - w0, building.width),
                    transform=wt, fill=0, all_touched=True,
                    dtype=np.uint32)
                win_id = id_lut[win_idx]
                win_b = cls_lut[win_idx]
                win_l = lvl_lut[win_idx]
                win_r = roof_lut[win_idx]
            else:
                win_b = np.zeros((w1 - w0, building.width), dtype=np.uint8)
                win_l = np.zeros((w1 - w0, building.width), dtype=np.uint8)
                win_id = np.zeros((w1 - w0, building.width), dtype=np.uint16)
                win_r = np.full((w1 - w0, building.width), NODATA,
                                dtype=np.int16)
            building.commit(r0, r1, w0, win_b)
            levels_grid.commit(r0, r1, w0, win_l)
            id_grid.commit(r0, r1, w0, win_id)
            roof_grid.commit(r0, r1, w0, win_r)

        self._building = building
        self._levels = levels_grid
        self._ids = id_grid
        self._roofs = roof_grid

    @staticmethod
    def _roof_y(ring: list[tuple[float, float]], levels: int,
                elev_m, transform: GeoTransform | None) -> int:
        """Uniform roof top block-Y for an instance, or NODATA.

        Samples the DEM on a coarse grid over the footprint's bbox and
        pads the roof to the highest ground found: every cell's floor
        stays at or above its terrain (a skirt is fine, a buried floor is
        not). Returns NODATA when no DEM answer is available — the runtime
        then falls back to per-cell heights.
        """
        if elev_m is None or transform is None:
            return NODATA
        es = [p[0] for p in ring]
        ns = [p[1] for p in ring]
        top = None
        e = min(es)
        while e <= max(es):
            n = min(ns)
            while n <= max(ns):
                try:
                    v = elev_m(e, n)
                except Exception:
                    v = None
                if v is not None and (top is None or v > top):
                    top = v
                n += _ROOF_SAMPLE_STEP_M
            e += _ROOF_SAMPLE_STEP_M
        if top is None:
            return NODATA
        return transform.block_y(top) + levels * 3 + 1

    def _cell(self, east: float, north: float) -> tuple[int, int] | None:
        col = math.floor((east - self.bounds[0]) / self.resolution_m)
        row = math.floor((self.bounds[3] - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._shape[0] or col >= self._shape[1]:
            return None
        return row, col

    def building_class(self, east: float, north: float) -> int:
        """Building class at geo coords; BUILDING_NONE = no footprint."""
        cell = self._cell(east, north)
        return BUILDING_NONE if cell is None else int(self._building[cell])

    def building_levels(self, east: float, north: float) -> int:
        """Floor count at geo coords; 0 outside footprints."""
        cell = self._cell(east, north)
        return 0 if cell is None else int(self._levels[cell])

    def building_id(self, east: float, north: float) -> int:
        """Footprint-instance id at geo coords; 0 outside footprints."""
        cell = self._cell(east, north)
        return 0 if cell is None else int(self._ids[cell])

    def building_roof(self, east: float, north: float) -> int:
        """Uniform roof top block-Y at geo coords; NODATA when unsolved."""
        cell = self._cell(east, north)
        return NODATA if cell is None else int(self._roofs[cell])
