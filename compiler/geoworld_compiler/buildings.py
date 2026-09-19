"""Buildings (Phase 8): OSM building polygons -> per-cell class + levels.

`fetch_buildings` pulls `building` ways via Overpass; `BuildingSource`
rasterizes each footprint into two aligned grids:

    building         u8 building class (0 = none)
    building_levels  u8 floor count (from building:levels / height tags,
                     else a per-class default)

Classes are semantic — the runtime's build theme picks wall/floor/roof
materials. Only footprint outline, class, and levels survive into the
dataset: enough for a recognizable urban mass without geometry libraries
in the runtime.
"""

from __future__ import annotations

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
    """Per-cell building class + floor count rasterized from OSM footprints."""

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 extent_m: float, resolution_m: float = 1.0):
        data = json.loads(Path(osm_json_path).read_text())

        self.extent_m = extent_m
        self.resolution_m = resolution_m
        size = math.ceil(2.0 * extent_m / resolution_m)
        transform = Affine(resolution_m, 0.0, -extent_m,
                           0.0, -resolution_m, extent_m)
        self._size = size

        # Group footprints by (class, levels, priority) — one rasterize per
        # distinct combo, painted in ascending priority order.
        groups: dict[tuple[int, int, int], list[dict]] = {}
        for el in data.get("elements", []):
            if el.get("type") != "way":
                continue
            geom = el.get("geometry")
            if not geom or len(geom) < 4:
                continue
            cls, levels, prio = _classify(el.get("tags", {}))
            coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            groups.setdefault((cls, levels, prio), []).append(
                {"type": "Polygon", "coordinates": [coords]})

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
