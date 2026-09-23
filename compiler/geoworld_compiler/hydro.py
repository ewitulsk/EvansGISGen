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

from .banded import (BandedGrid, gather2d, geom_bbox, rects_intersect,
                     run_bands, select_pairs, sub_window)
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
    the rect `bounds` = (min_e, min_n, max_e, max_n) at `resolution_m` per
    cell — rectangular so corridors don't pay for a square.
    """

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 bounds: tuple[float, float, float, float],
                 resolution_m: float = 1.0, bank_m: float = 6.0,
                 workers: int = 1):
        data = json.loads(Path(osm_json_path).read_text())
        lines, areas = parse_osm_water(data, projection)

        self.bounds = bounds
        self.resolution_m = resolution_m
        self.bank_m = bank_m
        e0, n0, e1, n1 = bounds
        self._shape = (math.ceil((n1 - n0) / resolution_m),
                       math.ceil((e1 - e0) / resolution_m))
        self._transform = Affine(resolution_m, 0.0, e0,
                                 0.0, -resolution_m, n1)

        # Pre-bbox geometries so each band only rasterizes what it can see.
        line_geoms = {cls: [(geom_bbox(g), g) for g in
                            [self._linestring(c) for k, c in lines if k == cls]]
                      for cls in LINE_CLASSES}
        area_geoms = [(geom_bbox(g), g, AREA_DEPTHS.get(tag, AREA_DEPTHS[None]))
                      for tag, ring in areas for g in [self._polygon(ring)]]

        depth = BandedGrid(bounds, resolution_m, np.float32)
        self._depth = depth

        # Context must cover the farthest field reach: river half-width (15)
        # + bank ramp (6) for lines, bank_m inside a shoreline for areas.
        def _band(r0, r1, w0, w1, wt):
            win = np.zeros((w1 - w0, depth.width), dtype=np.float32)
            wrect = depth.window_rect(w0, w1)

            # Line features: one EDT per waterway class (half-width is
            # class-uniform), depth ramps from centerline to bank. Each
            # class works in a sub-window clipped to its visible extent —
            # cells past half_w + margin can never be wet.
            for cls, (half_w, max_depth) in LINE_CLASSES.items():
                pairs = select_pairs(line_geoms[cls], wrect)
                sub = sub_window([b for b, _g in pairs],
                                 half_w + 2 * resolution_m, wt, win.shape)
                if sub is None:
                    continue
                sr0, sr1, sc0, sc1, sub_t = sub
                center = rasterize([(g, 1) for _b, g in pairs],
                                   out_shape=(sr1 - sr0, sc1 - sc0),
                                   transform=sub_t, fill=0,
                                   all_touched=True,
                                   dtype=np.uint8).astype(bool)
                if not center.any():
                    continue
                # Distance to nearest centerline cell, in meters. The bank
                # slope is capped at the half-width so narrow channels
                # (ditches, streams) still reach full depth at centerline.
                dc = distance_transform_edt(~center, sampling=resolution_m)
                bank = min(bank_m, half_w)
                inside = np.clip((half_w - dc) / bank, 0.0, 1.0)
                w = win[sr0:sr1, sc0:sc1]
                np.maximum(w, max_depth * _ss_array(inside), out=w)

            # Area features: burn each polygon's max depth (deepest wins),
            # then ramp inside distance to the shoreline.
            visible = [(b, g, d) for b, g, d in area_geoms
                       if rects_intersect(b, wrect)]
            sub = sub_window([b for b, _g, _d in visible],
                             bank_m + 2 * resolution_m, wt, win.shape)
            if sub is not None:
                sr0, sr1, sc0, sc1, sub_t = sub
                # Sort ascending so deeper water overwrites shallower.
                area_max = rasterize(
                    [(g, d) for _b, g, d in sorted(visible, key=lambda t: t[2])],
                    out_shape=(sr1 - sr0, sc1 - sc0), transform=sub_t,
                    fill=0.0, dtype=np.float32)
                mask = area_max > 0.0
                if mask.any():
                    # Distance inside the polygon to its shoreline, in meters.
                    da = distance_transform_edt(mask, sampling=resolution_m)
                    inside = np.clip(da / bank_m, 0.0, 1.0)
                    w = win[sr0:sr1, sc0:sc1]
                    np.maximum(w, area_max * _ss_array(inside), out=w)

            depth.commit(r0, r1, w0, win)

        run_bands(depth.band_windows(margin_m=32.0), _band,
                  max(1, min(workers, 4)))

    @staticmethod
    def _linestring(coords: list[tuple[float, float]]) -> dict:
        return {"type": "LineString", "coordinates": coords}

    @staticmethod
    def _polygon(ring: list[tuple[float, float]]) -> dict:
        return {"type": "Polygon", "coordinates": [ring]}

    def depth_m(self, east: float, north: float) -> float:
        """Channel depth in meters at geo coords; 0 = dry column."""
        col = math.floor((east - self.bounds[0]) / self.resolution_m)
        row = math.floor((self.bounds[3] - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._shape[0] or col >= self._shape[1]:
            return 0.0
        return float(self._depth[row, col])

    def depth_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 channel depths for cell-center vectors."""
        out = gather2d(self._depth.grid, self.bounds[0], self.bounds[3],
                       self.resolution_m, east, north, 0.0)
        return np.asarray(out, dtype=np.float64)


def _ss_array(t: np.ndarray) -> np.ndarray:
    """Vectorized smootherstep matching influence.smootherstep."""
    t = np.clip(t, 0.0, 1.0)
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
