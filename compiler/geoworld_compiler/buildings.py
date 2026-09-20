"""Buildings (Phase 8): OSM + Microsoft ML footprints -> class + levels.

`fetch_buildings` pulls `building` ways via Overpass; `fetch_buildings_ms`
pulls Microsoft's GlobalMLBuildingFootprints (Bing imagery ML detections)
for the quadkey tiles covering the bbox. `BuildingSource` merges both into
two aligned grids:

    building         u8 building class (0 = none)
    building_levels  u8 floor count (from building:levels / height tags /
                     MS height estimates, else a per-class default)

Microsoft footprints paint at the lowest priority as GENERIC — OSM's real
classes and level tags win wherever both datasets cover the same cells.
Classes are semantic — the runtime's build theme picks wall/floor/roof
materials. Only footprint outline, class, and levels survive into the
dataset: enough for a recognizable urban mass without geometry libraries
in the runtime.
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
from affine import Affine
from rasterio.features import rasterize

from .transform import Projection

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

# building=* value -> (class, default levels, paint priority).
_BUILDING_CLASSES: list[tuple[frozenset[str], int, int, int]] = [
    (frozenset({"garage", "garages", "shed", "barn", "outbuilding",
                "carport", "hut", "storage_tank", "silo"}),
     BUILDING_OUTBUILDING, 1, 10),
    (frozenset({"house", "detached", "residential", "apartments", "terrace",
                "dormitory", "cabin", "bungalow", "semidetached_house",
                "farm", "static_caravan"}),
     BUILDING_RESIDENTIAL, 2, 20),
    (frozenset({"industrial", "warehouse", "manufacture"}),
     BUILDING_INDUSTRIAL, 2, 30),
    (frozenset({"retail", "commercial", "office", "supermarket", "shop",
                "mall", "kiosk", "restaurant", "hotel"}),
     BUILDING_COMMERCIAL, 3, 40),
    (frozenset({"school", "church", "hospital", "civic", "public", "college",
                "university", "kindergarten", "government", "cathedral",
                "chapel", "fire_station"}),
     BUILDING_CIVIC, 3, 50),
]
_GENERIC = (BUILDING_GENERIC, 2, 15)


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


def _classify(tags: dict) -> tuple[int, int, int]:
    """(class, levels, priority) for a building way."""
    cls, default_levels, prio = _GENERIC
    value = tags.get("building", "yes")
    for values, c, lv, p in _BUILDING_CLASSES:
        if value in values:
            cls, default_levels, prio = c, lv, p
            break
    levels = default_levels
    try:
        if "building:levels" in tags:
            levels = max(1, int(float(tags["building:levels"])))
        elif "height" in tags:
            levels = max(1, round(float(tags["height"]) / 3.0))
    except (TypeError, ValueError):
        pass  # unparseable tag values keep the class default
    return cls, min(255, levels), prio


class BuildingSource:
    """Per-cell building class + floor count from merged footprints.

    Microsoft ML footprints paint first at the lowest priority as
    GENERIC; OSM ways then overwrite them wherever both datasets cover a
    cell, so OSM classes and level tags always win.
    """

    def __init__(self, osm_json_path: str | Path | None,
                 projection: Projection, extent_m: float,
                 resolution_m: float = 1.0,
                 ms_json_path: str | Path | None = None):
        if osm_json_path is None and ms_json_path is None:
            raise ValueError("BuildingSource needs at least one input")

        self.extent_m = extent_m
        self.resolution_m = resolution_m
        size = math.ceil(2.0 * extent_m / resolution_m)
        transform = Affine(resolution_m, 0.0, -extent_m,
                           0.0, -resolution_m, extent_m)
        self._size = size

        # Group footprints by (class, levels, priority) — one rasterize per
        # distinct combo, painted in ascending priority order.
        groups: dict[tuple[int, int, int], list[dict]] = {}

        def add(coords: list[tuple[float, float]],
                key: tuple[int, int, int]) -> None:
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            groups.setdefault(key, []).append(
                {"type": "Polygon", "coordinates": [coords]})

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
                add(coords, (BUILDING_GENERIC, levels, 5))

        if osm_json_path is not None:
            data = json.loads(Path(osm_json_path).read_text())
            for el in data.get("elements", []):
                if el.get("type") != "way":
                    continue
                geom = el.get("geometry")
                if not geom or len(geom) < 4:
                    continue
                cls, levels, prio = _classify(el.get("tags", {}))
                coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
                add(coords, (cls, levels, prio))

        building = np.zeros((size, size), dtype=np.uint8)
        b_levels = np.zeros((size, size), dtype=np.uint8)
        for (cls, levels, _prio), geoms in sorted(groups.items(),
                                                key=lambda kv: kv[0][2]):
            mask = rasterize([(g, 1) for g in geoms],
                             out_shape=(size, size), transform=transform,
                             fill=0, all_touched=True, dtype=np.uint8).astype(bool)
            building[mask] = cls
            b_levels[mask] = levels

        self._building = building
        self._levels = b_levels

    def _cell(self, east: float, north: float) -> tuple[int, int] | None:
        col = math.floor((east + self.extent_m) / self.resolution_m)
        row = math.floor((self.extent_m - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._size or col >= self._size:
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
