"""Roads (Phase 6): OSM highway vectors -> per-cell road surface classes.

Like hydrology, the heavy geometry happens offline. `fetch_roads` pulls
`highway` ways via Overpass; `RoadSource` rasterizes each class's
centerlines, measures distance-to-centerline with an EDT, and classifies
every cell by its position in the road cross-section:

    MARKING   (center stripe, multi-lane roads)
    ASPHALT   (travel surface) / TRACK (unpaved)
    CURB      (urban roads)
    SIDEWALK  (urban roads)
    SHOULDER  (rural highways)

Output is a single u8 road layer per tile — the runtime's only question is
`(x, z) -> road class`; it never performs geometry. Intersections emerge
from priority ordering: lower-priority classes paint first, higher classes
overwrite, so a residential sidewalk is correctly cut by the avenue it
meets. The DEM already holds the pavement elevation, so no carving is
needed — roads ride the same terrain as everything else.
"""

from __future__ import annotations

import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt

from .transform import Projection

OVERPASS = "https://overpass-api.de/api/interpreter"

# Road surface class ids — shared with the Java runtime (GeoTile.roadClass).
ROAD_NONE = 0
ROAD_ASPHALT = 1
ROAD_CURB = 2
ROAD_SIDEWALK = 3
ROAD_SHOULDER = 4
ROAD_TRACK = 5
ROAD_MARKING = 6


@dataclass(frozen=True)
class RoadParams:
    """Cross-section parameters for a highway class (all meters)."""
    half: float          # half-width of the travel surface
    curb: float = 0.0    # curb band beyond the travel surface
    walk: float = 0.0    # sidewalk band beyond the curb
    shoulder: float = 0.0  # shoulder band (rural highways; after walk)
    paved: bool = True   # False -> ROAD_TRACK instead of ROAD_ASPHALT
    prio: int = 0        # paint order; higher overwrites at crossings
    lanes: int = 2       # >=2 lanes gets a center marking
    mark: float = 0.2    # half-width of the center marking


ROAD_CLASSES: dict[str, RoadParams] = {
    "motorway":      RoadParams(half=8.0, shoulder=3.0, prio=95, lanes=4),
    "trunk":         RoadParams(half=7.0, shoulder=2.5, prio=90, lanes=2),
    "primary":       RoadParams(half=6.0, curb=0.6, walk=2.5, prio=80),
    "secondary":     RoadParams(half=5.0, curb=0.6, walk=2.0, prio=70),
    "tertiary":      RoadParams(half=4.5, curb=0.6, walk=2.0, prio=60),
    "unclassified":  RoadParams(half=3.5, curb=0.5, walk=1.5, prio=50, lanes=1),
    "residential":   RoadParams(half=3.5, curb=0.5, walk=1.5, prio=40, lanes=1),
    "living_street": RoadParams(half=3.5, curb=0.5, walk=2.0, prio=35, lanes=1),
    "service":       RoadParams(half=3.0, prio=30, lanes=1),
    "track":         RoadParams(half=2.0, paved=False, prio=20, lanes=1),
}

# highway tags that are never rendered as roads in v1.
SKIP_HIGHWAYS = {
    "footway", "cycleway", "path", "steps", "pedestrian", "bridleway",
    "construction", "proposed", "raceway", "corridor", "elevator", "escape",
    "bus_guideway", "rest_area", "services",
}


def fetch_roads(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                out_path: str | Path) -> Path:
    """Download OSM highway ways in a WGS84 bbox as raw JSON."""
    query = f"""[out:json][timeout:120];
way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});
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


def parse_osm_roads(data: dict, projection: Projection):
    """Split Overpass JSON into (defaulted, overridden) road features.

    Returns a list of (RoadParams, [(east, north), ...]). Tag overrides:
    `width` sets the travel half-width directly, `lanes` widens it, and
    `sidewalk=*` forces an urban curb+walk on rural highway classes.
    """
    roads: list[tuple[RoadParams, list[tuple[float, float]]]] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        hw = tags.get("highway")
        if hw not in ROAD_CLASSES or hw in SKIP_HIGHWAYS:
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        params = ROAD_CLASSES[hw]
        try:
            if "width" in tags:
                params = replace(params, half=max(1.0, float(tags["width"]) / 2.0))
            elif "lanes" in tags:
                params = replace(params, half=max(params.half,
                                                  int(tags["lanes"]) * 3.3 / 2.0),
                                 lanes=int(tags["lanes"]))
            if tags.get("sidewalk") in ("both", "left", "right", "yes"):
                params = replace(params, curb=max(params.curb, 0.5),
                                 walk=max(params.walk, 1.5), shoulder=0.0)
        except (TypeError, ValueError):
            pass  # unparseable tag values fall back to class defaults
        coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
        roads.append((params, coords))
    return roads


class RoadSource:
    """Per-cell road surface class rasterized from OSM highway vectors.

    Grid lives in dataset geo space over [-extent_m, +extent_m]^2 at
    `resolution_m` per cell, mirroring HydroSource.
    """

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 extent_m: float, resolution_m: float = 1.0):
        data = json.loads(Path(osm_json_path).read_text())
        roads = parse_osm_roads(data, projection)

        self.extent_m = extent_m
        self.resolution_m = resolution_m
        size = math.ceil(2.0 * extent_m / resolution_m)
        self._transform = Affine(resolution_m, 0.0, -extent_m,
                                 0.0, -resolution_m, extent_m)
        self._size = size

        # Batch features that share identical params so each distinct
        # cross-section costs one EDT on the full grid.
        groups: dict[RoadParams, list[dict]] = {}
        for params, coords in roads:
            groups.setdefault(params, []).append(self._linestring(coords))

        road = np.zeros((size, size), dtype=np.uint8)
        for params in sorted(groups, key=lambda p: p.prio):
            zone = self._classify(groups[params], params)
            if zone is not None:
                road[zone > 0] = zone[zone > 0]
        self._road = road

    @staticmethod
    def _linestring(coords: list[tuple[float, float]]) -> dict:
        return {"type": "LineString", "coordinates": coords}

    def _classify(self, geoms: list[dict], p: RoadParams) -> np.ndarray | None:
        center = rasterize([(g, 1) for g in geoms], out_shape=(self._size, self._size),
                           transform=self._transform, fill=0,
                           all_touched=True, dtype=np.uint8).astype(bool)
        if not center.any():
            return None
        dc = distance_transform_edt(~center, sampling=self.resolution_m)
        zone = np.zeros((self._size, self._size), dtype=np.uint8)
        outer = p.half + p.curb + p.walk + p.shoulder
        band = dc <= outer
        if p.shoulder > 0:
            zone[band & (dc > outer - p.shoulder)] = ROAD_SHOULDER
        if p.walk > 0:
            zone[band & (dc <= outer - p.shoulder)
                 & (dc > outer - p.shoulder - p.walk)] = ROAD_SIDEWALK
        if p.curb > 0:
            zone[band & (dc <= p.half + p.curb) & (dc > p.half)] = ROAD_CURB
        zone[dc <= p.half] = ROAD_ASPHALT if p.paved else ROAD_TRACK
        if p.lanes >= 2 and p.paved:
            zone[dc <= p.mark] = ROAD_MARKING
        return zone

    def road_class(self, east: float, north: float) -> int:
        """Road surface class at geo coords; ROAD_NONE = no road."""
        col = math.floor((east + self.extent_m) / self.resolution_m)
        row = math.floor((self.extent_m - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._size or col >= self._size:
            return ROAD_NONE
        return int(self._road[row, col])
