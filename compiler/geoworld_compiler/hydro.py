"""Hydrology (Phase 5): OSM waterways/water bodies -> per-cell channel depth.

Water is compiled separately from elevation — we never infer water from
`DEM below Y`. Instead, real GIS footprints (OSM `waterway` ways and
`natural=water` areas) define where water lives, and a distance transform
gives each wet column a channel depth:

    depth_m = max_depth * smootherstep(min(1, dist_to_bank / bank_m))

so rivers get a trapezoidal cross-section (sloped banks, flat bed) rather
than a DEM artifact. The build then bakes the riverbed into the elevation
layer (elevation = water surface - depth; LiDAR water surface is already
the right level) and stores the depth in the tile's `water_depth` layer —
the runtime just fills water from bed to bed + depth.

Data source: OpenStreetMap via the Overpass API (`fetch_water`), saved as
raw JSON (elements with `geometry`). Line features (river/stream/canal/
drain/ditch) are buffered by per-class half-widths; closed `natural=water`
ways are treated as polygons.
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
from scipy.ndimage import distance_transform_edt

from .influence import smootherstep
from .transform import Projection

OVERPASS = "https://overpass-api.de/api/interpreter"

# waterway class -> (half-width m, max channel depth m)
LINE_CLASSES: dict[str, tuple[float, float]] = {
    "river": (15.0, 4.0),
    "canal": (4.0, 1.5),
    "stream": (4.0, 1.5),
    "drain": (2.0, 1.0),
    "ditch": (2.0, 0.8),
}

# OSM `water` tag -> max depth m (areas); fallback for untagged water.
AREA_DEPTHS: dict[str | None, float] = {
    "river": 3.0,
    "lake": 4.0,
    "reservoir": 4.0,
    "pond": 2.5,
    "basin": 2.0,
    "oxbow": 3.0,
    None: 2.5,
}


def fetch_water(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                out_path: str | Path) -> Path:
    """Download OSM waterways + water bodies in a WGS84 bbox as raw JSON."""
    query = f"""[out:json][timeout:120];
(
  way["waterway"]({min_lat},{min_lon},{max_lat},{max_lon});
  way["natural"="water"]({min_lat},{min_lon},{max_lat},{max_lon});
);
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


def parse_osm_water(data: dict, projection: Projection):
    """Split Overpass JSON into (lines, areas) in geo-meter coordinates.

    lines: list of (waterway_class, [(east, north), ...])
    areas: list of (water_tag, [[(east, north), ...] exterior ring])
    Relations/multipolygons are skipped (v1); waterways carry the geometry.
    """
    lines: list[tuple[str, list[tuple[float, float]]]] = []
    areas: list[tuple[str | None, list[tuple[float, float]]]] = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
        waterway = tags.get("waterway")
        if waterway in LINE_CLASSES:
            lines.append((waterway, coords))
        elif tags.get("natural") == "water" and coords[0] == coords[-1]:
            areas.append((tags.get("water"), coords))
    return lines, areas


class HydroSource:
    """Per-cell channel depth (meters) rasterized from OSM water vectors.

    Grids live in dataset geo space (meters east/north of the anchor) over
    the square [-extent_m, +extent_m]^2 at `resolution_m` per cell.
    """

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 extent_m: float, resolution_m: float = 1.0, bank_m: float = 6.0):
        data = json.loads(Path(osm_json_path).read_text())
        lines, areas = parse_osm_water(data, projection)

        self.extent_m = extent_m
        self.resolution_m = resolution_m
        self.bank_m = bank_m
        size = math.ceil(2.0 * extent_m / resolution_m)
        # Geo meters -> pixels: col = (e + ext)/res, row = (ext - n)/res.
        self._transform = Affine(resolution_m, 0.0, -extent_m,
                                 0.0, -resolution_m, extent_m)
        self._size = size

        depth = np.zeros((size, size), dtype=np.float32)

        # Line features: one EDT per waterway class (half-width is
        # class-uniform), depth ramps from centerline to bank.
        for cls, (half_w, max_depth) in LINE_CLASSES.items():
            geoms = [self._linestring(c) for k, c in lines if k == cls]
            if not geoms:
                continue
            center = rasterize([(g, 1) for g in geoms], out_shape=(size, size),
                               transform=self._transform, fill=0,
                               all_touched=True, dtype=np.uint8).astype(bool)
            if not center.any():
                continue
            # Distance to nearest centerline cell, in meters. The bank slope
            # is capped at the half-width so narrow channels (ditches,
            # streams) still reach their full depth at the centerline.
            dc = distance_transform_edt(~center, sampling=resolution_m)
            bank = min(bank_m, half_w)
            inside = np.clip((half_w - dc) / bank, 0.0, 1.0)
            cls_depth = max_depth * _ss_array(inside)
            np.maximum(depth, cls_depth, out=depth)

        # Area features: burn each polygon's max depth (deepest wins),
        # then ramp inside distance to the shoreline.
        if areas:
            # Sort ascending so deeper water overwrites shallower.
            shapes = [(self._polygon(ring), AREA_DEPTHS.get(tag, AREA_DEPTHS[None]))
                      for tag, ring in sorted(
                          areas, key=lambda a: AREA_DEPTHS.get(a[0], AREA_DEPTHS[None]))]
            area_max = rasterize(shapes, out_shape=(size, size),
                                 transform=self._transform, fill=0.0,
                                 dtype=np.float32)
            mask = area_max > 0.0
            if mask.any():
                # Distance inside the polygon to its shoreline, in meters.
                da = distance_transform_edt(mask, sampling=resolution_m)
                inside = np.clip(da / bank_m, 0.0, 1.0)
                np.maximum(depth, area_max * _ss_array(inside), out=depth)

        self._depth = depth

    @staticmethod
    def _linestring(coords: list[tuple[float, float]]) -> dict:
        return {"type": "LineString", "coordinates": coords}

    @staticmethod
    def _polygon(ring: list[tuple[float, float]]) -> dict:
        return {"type": "Polygon", "coordinates": [ring]}

    def depth_m(self, east: float, north: float) -> float:
        """Channel depth in meters at geo coords; 0 = dry column."""
        col = math.floor((east + self.extent_m) / self.resolution_m)
        row = math.floor((self.extent_m - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._size or col >= self._size:
            return 0.0
        return float(self._depth[row, col])


def _ss_array(t: np.ndarray) -> np.ndarray:
    """Vectorized smootherstep matching influence.smootherstep."""
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
