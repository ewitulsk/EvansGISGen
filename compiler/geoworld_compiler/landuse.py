"""Land use (Phase 7): OSM polygons/lines -> per-cell surface classes.

`fetch_landuse` pulls `landuse`/`leisure`/`natural`/`amenity` polygons and
`railway` lines via Overpass; `LanduseSource` rasterizes them into a u8
surface-class grid:

    GRASS       meadows, grassland, village greens
    FARMLAND    farmland, farmyards, orchards, allotments
    FOREST      woods (natural=wood, landuse=forest)
    RESIDENTIAL / COMMERCIAL / INDUSTRIAL   zoning polygons
    PARK        parks, playgrounds, rec grounds, school grounds
    PARKING     parking lots (amenity=parking)
    RAILWAY     rail corridors (lines buffered to ballast width)

The layer is semantic — class ids only, no block choices. The runtime's
surface theme maps classes to blocks, keeping classification separate from
block selection so other themes can reuse the same dataset.
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

from .banded import (BandedGrid, gather2d, geom_bbox, run_bands,
                     select_pairs, sub_window)

from .transform import Projection

OVERPASS = "https://overpass-api.de/api/interpreter"

# Surface class ids — shared with the Java runtime (SurfaceTheme).
SURFACE_NATURAL = 0      # no classification; keep the vanilla surface
SURFACE_GRASS = 1
SURFACE_FARMLAND = 2
SURFACE_FOREST = 3
SURFACE_RESIDENTIAL = 4
SURFACE_COMMERCIAL = 5
SURFACE_INDUSTRIAL = 6
SURFACE_PARKING = 7
SURFACE_RAILWAY = 8
SURFACE_PARK = 9

# (tag key, tag value) -> (class id, paint priority). Higher priority
# overwrites lower — e.g. a parking lot or rail line cuts through the
# zoning polygon it sits in.
_TAG_CLASSES: list[tuple[str, frozenset[str], int, int]] = [
    ("natural", frozenset({"grassland", "scrub", "heath"}), SURFACE_GRASS, 10),
    ("landuse", frozenset({"meadow", "grass", "greenfield", "village_green"}),
     SURFACE_GRASS, 10),
    ("landuse", frozenset({"farmland", "farmyard", "orchard", "vineyard",
                           "allotments", "plant_nursery", "animal_keeping"}),
     SURFACE_FARMLAND, 15),
    ("natural", frozenset({"wood"}), SURFACE_FOREST, 20),
    ("landuse", frozenset({"forest"}), SURFACE_FOREST, 20),
    ("landuse", frozenset({"residential"}), SURFACE_RESIDENTIAL, 30),
    ("landuse", frozenset({"commercial", "retail"}), SURFACE_COMMERCIAL, 30),
    ("landuse", frozenset({"industrial", "brownfield", "landfill", "quarry",
                           "depot"}), SURFACE_INDUSTRIAL, 30),
    ("landuse", frozenset({"recreation_ground", "cemetery", "religious"}),
     SURFACE_PARK, 40),
    ("leisure", frozenset({"park", "playground", "recreation_ground",
                           "golf_course", "dog_park", "garden", "pitch",
                           "nature_reserve"}), SURFACE_PARK, 40),
    ("amenity", frozenset({"school", "university", "college", "hospital"}),
     SURFACE_PARK, 40),
    ("amenity", frozenset({"parking", "parking_space"}), SURFACE_PARKING, 50),
    ("landuse", frozenset({"garages", "parking"}), SURFACE_PARKING, 50),
]

RAIL_HALF_WIDTH_M = 3.0  # ballast bed half-width for railway=rail lines


def fetch_landuse(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                  out_path: str | Path) -> Path:
    """Download OSM land-use polygons + rail lines in a WGS84 bbox as JSON."""
    query = f"""[out:json][timeout:120];
(
  way["landuse"]({min_lat},{min_lon},{max_lat},{max_lon});
  way["leisure"]({min_lat},{min_lon},{max_lat},{max_lon});
  way["natural"]({min_lat},{min_lon},{max_lat},{max_lon});
  way["amenity"]({min_lat},{min_lon},{max_lat},{max_lon});
  way["railway"]({min_lat},{min_lon},{max_lat},{max_lon});
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


def _classify(tags: dict) -> tuple[int, int] | None:
    """Best (class, priority) for an element's tags, or None."""
    best = None
    for key, values, cls, prio in _TAG_CLASSES:
        if tags.get(key) in values:
            if best is None or prio > best[1]:
                best = (cls, prio)
    return best


class LanduseSource:
    """Per-cell surface class rasterized from OSM land-use polygons/lines.

    Grid lives in dataset geo space over the rect `bounds` =
    (min_e, min_n, max_e, max_n) at `resolution_m` per cell, mirroring
    HydroSource/RoadSource.
    """

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 bounds: tuple[float, float, float, float],
                 resolution_m: float = 1.0, workers: int = 1):
        data = json.loads(Path(osm_json_path).read_text())

        self.bounds = bounds
        self.resolution_m = resolution_m
        e0, n0, e1, n1 = bounds
        self._shape = (math.ceil((n1 - n0) / resolution_m),
                       math.ceil((e1 - e0) / resolution_m))
        self._transform = Affine(resolution_m, 0.0, e0,
                                 0.0, -resolution_m, n1)

        # Group by (class, priority): one rasterize per distinct class.
        # (bbox, geom) pairs let each band pre-filter to visible features.
        polys: dict[tuple[int, int], list[tuple[tuple, dict]]] = {}
        rail_lines: list[tuple[tuple, dict]] = []
        for el in data.get("elements", []):
            if el.get("type") != "way":
                continue
            tags = el.get("tags", {})
            geom = el.get("geometry")
            if not geom or len(geom) < 2:
                continue
            coords = [projection.to_geo(p["lat"], p["lon"]) for p in geom]
            if tags.get("railway") == "rail":
                g = {"type": "LineString", "coordinates": coords}
                rail_lines.append((geom_bbox(g), g))
                continue
            hit = _classify(tags)
            if hit is None:
                continue
            if len(coords) < 4:
                continue  # need a closed ring
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            g = {"type": "Polygon", "coordinates": [coords]}
            polys.setdefault(hit, []).append((geom_bbox(g), g))

        surface = BandedGrid(bounds, resolution_m, np.uint8)
        self._surface = surface

        # Context must cover the rail buffer reach (RAIL_HALF_WIDTH_M).
        def _band(r0, r1, w0, w1, wt):
            win = np.zeros((w1 - w0, surface.width), dtype=np.uint8)
            wrect = surface.window_rect(w0, w1)
            for (cls, _prio), pairs in sorted(polys.items(),
                                              key=lambda kv: kv[0][1]):
                visible = select_pairs(pairs, wrect)
                sub = sub_window([b for b, _g in visible],
                                 2 * resolution_m, wt, win.shape)
                if sub is None:
                    continue
                sr0, sr1, sc0, sc1, sub_t = sub
                mask = rasterize([(g, 1) for _b, g in visible],
                                 out_shape=(sr1 - sr0, sc1 - sc0),
                                 transform=sub_t, fill=0,
                                 all_touched=True, dtype=np.uint8)
                w = win[sr0:sr1, sc0:sc1]
                w[mask.astype(bool)] = cls

            # Rail corridors: distance-to-track <= ballast half-width.
            visible = select_pairs(rail_lines, wrect)
            sub = sub_window([b for b, _g in visible],
                             RAIL_HALF_WIDTH_M + 2 * resolution_m,
                             wt, win.shape)
            if sub is not None:
                sr0, sr1, sc0, sc1, sub_t = sub
                center = rasterize([(g, 1) for _b, g in visible],
                                   out_shape=(sr1 - sr0, sc1 - sc0),
                                   transform=sub_t, fill=0,
                                   all_touched=True,
                                   dtype=np.uint8).astype(bool)
                if center.any():
                    dc = distance_transform_edt(~center, sampling=resolution_m)
                    win[sr0:sr1, sc0:sc1][dc <= RAIL_HALF_WIDTH_M] = \
                        SURFACE_RAILWAY

            surface.commit(r0, r1, w0, win)

        run_bands(surface.band_windows(margin_m=32.0), _band,
                  max(1, min(workers, 4)))

    def surface_class(self, east: float, north: float) -> int:
        """Surface class at geo coords; SURFACE_NATURAL = unclassified."""
        col = math.floor((east - self.bounds[0]) / self.resolution_m)
        row = math.floor((self.bounds[3] - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._shape[0] or col >= self._shape[1]:
            return SURFACE_NATURAL
        return int(self._surface[row, col])

    def surface_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) u8 surface classes for cell-center vectors."""
        out = gather2d(self._surface.grid, self.bounds[0], self.bounds[3],
                       self.resolution_m, east, north, 0)
        return np.asarray(out, dtype=np.uint8)
