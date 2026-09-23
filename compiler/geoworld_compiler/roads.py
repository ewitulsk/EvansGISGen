"""Roads (Phase 6/13): OSM highway vectors -> per-cell road surface classes
plus elevated-deck heights and street signs.

Like hydrology, the heavy geometry happens offline. `fetch_roads` pulls
`highway` ways plus stop/yield nodes via Overpass; `RoadSource` rasterizes
each class's centerlines, measures distance-to-centerline with an EDT, and
classifies every cell by its position in the road cross-section:

    MARKING   (center stripe, multi-lane roads)
    ASPHALT   (travel surface) / TRACK (unpaved)
    CURB      (urban roads)
    SIDEWALK  (urban roads)
    SHOULDER  (rural highways)

Phase 13 adds the vertical dimension. OSM carries no absolute deck heights —
just `layer` ordering and `bridge`/`tunnel` flags — so the compiler solves a
height profile per bridge way:

    required(v) = max(DEM(v), under_surface + clearance)

where under-features (lower-level road ways, rail lines from the landuse
fetch, water via the hydro depth raster) are detected by segment proximity.
A slope-limited envelope (~8% grade) then relaxes the profile between pins,
so a +5 m crossing spreads its rise ~60 m each way and deck endpoints land
on their approaches. Embankments need no work — they are terrain, and the
DEM already carries them.

Outputs per tile: the u8 `road` layer (at-grade surface class), plus
`deck_m`/`deck_class` accessors — deck top elevation in meters and the
deck's cross-section class — emitted only where the resolved deck sits
>= DECK_MIN_M above the DEM, so embanked ramps still render as ordinary
terrain. A `signs` list (street-name blades at named-way intersections,
stop/yield posts at their OSM nodes) is written beside the manifest as
`signs.json`.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import rasterize
from scipy.ndimage import distance_transform_edt

from .banded import (BandedGrid, gather2d, geom_bbox, run_bands,
                     select_pairs, sub_window)
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
    "motorway_link": RoadParams(half=4.0, shoulder=1.5, prio=94, lanes=1),
    "trunk":         RoadParams(half=7.0, shoulder=2.5, prio=90, lanes=2),
    "trunk_link":    RoadParams(half=3.5, shoulder=1.5, prio=89, lanes=1),
    "primary":       RoadParams(half=6.0, curb=0.6, walk=2.5, prio=80),
    "primary_link":  RoadParams(half=3.5, prio=79, lanes=1),
    "secondary":     RoadParams(half=5.0, curb=0.6, walk=2.0, prio=70),
    "secondary_link": RoadParams(half=3.25, prio=69, lanes=1),
    "tertiary":      RoadParams(half=4.5, curb=0.6, walk=2.0, prio=60),
    "tertiary_link": RoadParams(half=3.0, prio=59, lanes=1),
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

# Classes that carry street furniture — freeway links never get sign blades.
_SIGNABLE = frozenset({
    "primary", "secondary", "tertiary", "unclassified", "residential",
    "living_street",
})

# Vertical model constants (meters).
CLEAR_ROAD = 5.0      # deck clearance over a lower road
CLEAR_RAIL = 4.5      # over a railway line
CLEAR_WATER = 3.0     # over the water surface
CLEAR_UNKNOWN = 3.0   # bridge way with no detected under-feature
MAX_GRADE = 0.08      # envelope slope limit (8%)
DECK_MIN_M = 1.5      # emit a deck only where this far above the DEM
RESAMPLE_M = 3.0      # vertex spacing for height solving
SEG_CELL = 24.0       # spatial-hash cell size for crossing detection
CROSS_PAD_M = 4.0     # extra reach beyond summed half-widths


@dataclass
class RoadFeature:
    """A parsed highway way: cross-section + polyline + vertical attrs."""
    params: RoadParams
    coords: list[tuple[float, float]]
    layer: int = 0
    bridge: bool = False
    tunnel: bool = False
    name: str | None = None
    highway: str = ""

    @property
    def level(self) -> int:
        """Effective stacking level for crossing ordering."""
        if self.tunnel:
            return min(self.layer, 0) - 1
        if self.layer != 0:
            return self.layer
        return 1 if self.bridge else 0


def fetch_roads(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                out_path: str | Path) -> Path:
    """Download OSM highway ways + traffic-control nodes in a WGS84 bbox."""
    query = f"""[out:json][timeout:240];
(
way["highway"]({min_lat},{min_lon},{max_lat},{max_lon});
node["highway"~"^(stop|give_way|traffic_signals)$"]
    ({min_lat},{min_lon},{max_lat},{max_lon});
);
out geom;"""
    body = urllib.parse.urlencode({"data": query}).encode()
    data = None
    for attempt in range(4):
        req = urllib.request.Request(
            OVERPASS, data=body,
            headers={"User-Agent": "geoworld-compiler/0.1"})
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                data = json.load(resp)
            break
        except Exception:
            if attempt == 3:
                raise
            import time
            time.sleep(20 * (attempt + 1))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data))
    return out


def corridor_polylines(osm_json_path: str | Path, projection: Projection,
                       highway_values: frozenset[str] = frozenset(
                           {"motorway", "trunk"})):
    """Centerline polylines of major roads from a fetch-roads payload —
    the US-77 corridor primitive for Phase 10 influence fields. Returns a
    list of (east, north) point sequences, one per way."""
    data = json.loads(Path(osm_json_path).read_text())
    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        tags = el.get("tags", {})
        if tags.get("highway") not in highway_values:
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        lines.append([projection.to_geo(p["lat"], p["lon"]) for p in geom])
    return lines


def clip_polylines(lines, bounds: tuple[float, float, float, float]):
    """Split polylines at a rect, keeping contiguous runs of >=2 in-bounds
    points. Clipping a corridor `reach` inside the dataset bounds lets the
    influence ramp fully decay before the data ends — otherwise the field
    would cut off mid-strip and seam."""
    e0, n0, e1, n1 = bounds
    out = []
    for line in lines:
        run = []
        for e, n in line:
            if e0 <= e <= e1 and n0 <= n <= n1:
                run.append((e, n))
                continue
            if len(run) >= 2:
                out.append(run)
            run = []
        if len(run) >= 2:
            out.append(run)
    return out


def _parse_int(value, default=0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def parse_osm_roads(data: dict, projection: Projection) -> list[RoadFeature]:
    """Split Overpass JSON into road features with vertical attributes.

    Tag overrides: `width` sets the travel half-width directly, `lanes`
    widens it, and `sidewalk=*` forces an urban curb+walk on rural highway
    classes. `layer`/`bridge`/`tunnel`/`name`/`ref` feed the deck solver
    and the sign pass.
    """
    roads: list[RoadFeature] = []
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
        roads.append(RoadFeature(
            params=params,
            coords=coords,
            layer=_parse_int(tags.get("layer")),
            bridge="bridge" in tags and tags["bridge"] != "no",
            tunnel="tunnel" in tags and tags["tunnel"] != "no",
            name=tags.get("name") or tags.get("ref"),
            highway=hw,
        ))
    return roads


def parse_osm_nodes(data: dict, projection: Projection) -> list[dict]:
    """Traffic-control nodes (stop/give_way) from a fetch-roads payload."""
    out = []
    for el in data.get("elements", []):
        if el.get("type") != "node":
            continue
        hw = el.get("tags", {}).get("highway")
        if hw in ("stop", "give_way"):
            out.append({"kind": hw,
                        "pos": projection.to_geo(el["lat"], el["lon"])})
    return out


def parse_rail_lines(data: dict, projection: Projection) -> list[list]:
    """`railway=rail` centerlines from a fetch-landuse payload — bridge
    under-features for clearance solving."""
    lines = []
    for el in data.get("elements", []):
        if el.get("type") != "way":
            continue
        if el.get("tags", {}).get("railway") != "rail":
            continue
        geom = el.get("geometry")
        if not geom or len(geom) < 2:
            continue
        lines.append([projection.to_geo(p["lat"], p["lon"]) for p in geom])
    return lines


def _resample(coords: list[tuple[float, float]], step: float):
    """Densify a polyline to <= `step` spacing. Returns (pts, cumdist)."""
    pts = [coords[0]]
    dists = [0.0]      # cumulative length at each emitted point
    next_at = step     # next sample distance along the polyline
    total = 0.0        # cumulative length at the current segment end
    for i in range(1, len(coords)):
        x0, y0 = coords[i - 1]
        x1, y1 = coords[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        while next_at < total + seg:
            f = (next_at - total) / seg
            pts.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
            dists.append(next_at)
            next_at += step
        total += seg
    pts.append(coords[-1])
    dists.append(total)
    return pts, dists


def _seg_point_dist(ax, ay, bx, by, px, py) -> float:
    """Distance from point p to segment ab."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + dx * t), py - (ay + dy * t))


class _SegIndex:
    """Uniform-grid spatial hash over way segments for crossing detection."""

    def __init__(self, cell: float = SEG_CELL):
        self.cell = cell
        self._grid: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add_way(self, way_id: int, coords: list[tuple[float, float]]):
        for si in range(len(coords) - 1):
            x0, y0 = coords[si]
            x1, y1 = coords[si + 1]
            c0x, c0y = int(x0 // self.cell), int(y0 // self.cell)
            c1x, c1y = int(x1 // self.cell), int(y1 // self.cell)
            for cx in range(min(c0x, c1x), max(c0x, c1x) + 1):
                for cy in range(min(c0y, c1y), max(c0y, c1y) + 1):
                    self._grid.setdefault((cx, cy), []).append((way_id, si))

    def near(self, x: float, y: float, radius: float):
        c0 = (int((x - radius) // self.cell), int((y - radius) // self.cell))
        c1 = (int((x + radius) // self.cell), int((y + radius) // self.cell))
        for cx in range(c0[0], c1[0] + 1):
            for cy in range(c0[1], c1[1] + 1):
                yield from self._grid.get((cx, cy), ())


class RoadSource:
    """Per-cell road layers rasterized from OSM highway vectors.

    `road` (u8): at-grade cross-section class. `deck_m`/`deck_class`:
    elevated deck height in meters and its class, where a bridge way
    resolves above the DEM. Grid lives in dataset geo space over `bounds`
    at `resolution_m` per cell, mirroring HydroSource.

    `elev_m(e, n)` supplies bare-earth DEM elevation (deck floors); `hydro`
    supplies water depth for river crossings; `rail_lines` are extra
    under-features mined from the landuse fetch.
    """

    def __init__(self, osm_json_path: str | Path, projection: Projection,
                 bounds: tuple[float, float, float, float],
                 resolution_m: float = 1.0,
                 elev_m=None, hydro=None, rail_lines=None,
                 workers: int = 1):
        data = json.loads(Path(osm_json_path).read_text())
        features = parse_osm_roads(data, projection)
        self.nodes = parse_osm_nodes(data, projection)

        self.bounds = bounds
        self.resolution_m = resolution_m
        e0, n0, e1, n1 = bounds
        self._shape = (math.ceil((n1 - n0) / resolution_m),
                       math.ceil((e1 - e0) / resolution_m))
        self._transform = Affine(resolution_m, 0.0, e0,
                                 0.0, -resolution_m, n1)

        # --- Deck height solve (Phase 13) ---------------------------------
        # Only bridge ways get decks — embanked ramps are already terrain
        # in the DEM. Returns {way_idx: (resampled_pts, heights_m)}.
        decks: dict[int, tuple[list, list[float]]] = {}
        if elev_m is not None:
            decks = self._solve_decks(features, elev_m, hydro, rail_lines)

        # Batch ground features that share identical params so each distinct
        # cross-section costs one EDT per band. Elevated ways rasterize
        # separately so their decks can spread heights per cell.
        ground: dict[RoadParams, list[tuple[tuple, dict]]] = {}
        elevated: list[tuple[RoadFeature, dict, list, list[float]]] = []
        for wi, f in enumerate(features):
            g = self._linestring(f.coords)
            if wi in decks:
                pts, hh = decks[wi]
                elevated.append((f, g, pts, hh))
            else:
                ground.setdefault(f.params, []).append((geom_bbox(g), g))

        road = BandedGrid(bounds, resolution_m, np.uint8)
        deck_h = BandedGrid(bounds, resolution_m, np.float32)
        deck_c = BandedGrid(bounds, resolution_m, np.uint8)
        self._road = road
        self._deck_h = deck_h
        self._deck_c = deck_c

        # Cross-section reach is <= ~20 m even for tag-widened arterials.
        def _band(r0, r1, w0, w1, wt):
            win = np.zeros((w1 - w0, road.width), dtype=np.uint8)
            wrect = road.window_rect(w0, w1)
            deckmask = np.zeros(win.shape, dtype=bool)
            dh = np.full(win.shape, -1.0, dtype=np.float32)
            dc_cls = np.zeros(win.shape, dtype=np.uint8)
            # Elevated decks first (lowest level paints first — higher
            # decks overwrite where they cross). Each way works in a
            # sub-window clipped to its bbox so the per-way EDT stays small.
            for f, g, pts, hh in sorted(elevated,
                                        key=lambda t: (t[0].level,
                                                       t[0].params.prio)):
                sub = self._emit_deck(f, g, pts, hh, elev_m, w0, w1)
                if sub is None:
                    continue
                sr, sc, emit, h_near, zone = sub
                target_h = dh[sr, sc]
                target_c = dc_cls[sr, sc]
                target_m = deckmask[sr, sc]
                # A higher deck wins on overlap.
                overwrite = emit & (h_near >= target_h)  # -1 = unset
                target_h[overwrite] = h_near[overwrite]
                target_c[overwrite] = _deck_class(zone)[overwrite]
                target_m |= emit
            # At-grade surface: all ways paint `road` by priority; the
            # bridge way's own band only paints where no deck was emitted,
            # so nothing phantom-paves the ground under a floating span.
            for params in sorted(ground, key=lambda p: p.prio):
                sub = self._classify(select_pairs(ground[params], wrect),
                                     params, win.shape, wt)
                if sub is not None:
                    sr, sc, zone = sub
                    w = win[sr, sc]
                    w[zone > 0] = zone[zone > 0]
            for f, g, _pts, _hh in sorted(elevated,
                                          key=lambda t: t[0].params.prio):
                sub = self._classify(select_pairs([(geom_bbox(g), g)], wrect),
                                     f.params, win.shape, wt)
                if sub is not None:
                    sr, sc, zone = sub
                    zone = np.where(deckmask[sr, sc], 0, zone)
                    w = win[sr, sc]
                    w[zone > 0] = zone[zone > 0]
            road.commit(r0, r1, w0, win)
            deck_h.commit(r0, r1, w0, dh)
            deck_c.commit(r0, r1, w0, dc_cls)

        run_bands(road.band_windows(margin_m=64.0), _band,
                  max(1, min(workers, 4)))

        signs = self._compute_signs(features, self.nodes)
        # Clip to the dataset rect (signs outside just waste the sidecar).
        e0, n0, e1, n1 = bounds
        self.signs = [s for s in signs
                      if e0 - 64 <= s["e"] <= e1 + 64
                      and n0 - 64 <= s["n"] <= n1 + 64]

    # -- Deck solving -------------------------------------------------------

    def _solve_decks(self, features, elev_m, hydro, rail_lines):
        """Per-vertex deck heights (meters) for bridge ways."""
        index = _SegIndex()
        lowers: dict[int, tuple[list, float, int, float]] = {}
        for wi, f in enumerate(features):
            index.add_way(wi, f.coords)
            lowers[wi] = (f.coords, CLEAR_ROAD, f.level, f.params.half)
        rail_offset = len(features)
        for ri, line in enumerate(rail_lines or []):
            index.add_way(rail_offset + ri, line)
            lowers[rail_offset + ri] = (line, CLEAR_RAIL, -1, 3.0)

        # Shared-endpoint index: does a way end where another way passes?
        endpoint_levels: dict[tuple[int, int], list[int]] = {}
        for f in features:
            for pt in (f.coords[0], f.coords[-1]):
                key = (round(pt[0] * 4), round(pt[1] * 4))
                endpoint_levels.setdefault(key, []).append(f.level)

        heights: dict[int, tuple[list, list[float]]] = {}
        for wi, f in enumerate(features):
            if not f.bridge or elev_m is None:
                continue
            pts, dists = _resample(f.coords, RESAMPLE_M)
            base = np.array([_elev_or(elev_m, e, n) for e, n in pts],
                            dtype=np.float64)
            if np.isnan(base).all():
                continue
            # DEM nodata gaps: interpolate between valid neighbours.
            ok = ~np.isnan(base)
            if not ok.all():
                base = np.interp(np.arange(len(base)),
                                 np.nonzero(ok)[0], base[ok])
            req = base.copy()
            detected = np.zeros(len(pts), dtype=bool)
            reach = f.params.half + 12.0 + CROSS_PAD_M
            for vi, (e, n) in enumerate(pts):
                r = req[vi]
                for owi, si in index.near(e, n, reach):
                    if owi == wi:
                        continue
                    o = lowers.get(owi)
                    if o is None:
                        continue
                    oc, clear, olevel, ohalf = o
                    if olevel >= f.level:
                        continue
                    if si + 1 >= len(oc):
                        continue
                    d = _seg_point_dist(*oc[si], *oc[si + 1], e, n)
                    if d <= f.params.half + ohalf + CROSS_PAD_M:
                        cx = (oc[si][0] + oc[si + 1][0]) / 2.0
                        cy = (oc[si][1] + oc[si + 1][1]) / 2.0
                        cs = _elev_or(elev_m, cx, cy)
                        if cs == cs:
                            r = max(r, cs + clear)
                            detected[vi] = True
                if hydro is not None and hydro.depth_m(e, n) > 0:
                    r = max(r, base[vi] + CLEAR_WATER)
                    detected[vi] = True
                req[vi] = r
            # Plateau: viaducts are level — vertices between the first and
            # last detected crossing floor at the max requirement. With no
            # detection at all, lift the interior modestly (a bridge always
            # crosses something the data may not name: a ditch, a rail, a
            # valley floor).
            if detected.any():
                first = int(np.nonzero(detected)[0][0])
                last = int(np.nonzero(detected)[0][-1])
                floor = float(np.max(req))
                req[first:last + 1] = np.maximum(req[first:last + 1], floor)
            else:
                req[1:-1] = np.maximum(req[1:-1], base[1:-1] + CLEAR_UNKNOWN)
            # Endpoints pin to the DEM unless the way continues into
            # another elevated way (shared node, level > 0) — so a deck
            # lands on its approach or hands off to the next span, and a
            # dangling endpoint can never leave a floating cliff.
            for vi in (0, len(pts) - 1):
                key = (round(pts[vi][0] * 4), round(pts[vi][1] * 4))
                others = list(endpoint_levels.get(key, []))
                if f.level in others:
                    others.remove(f.level)
                if not others or min(others) <= 0:
                    req[vi] = base[vi]
            heights[wi] = (pts, _slope_envelope(req, dists, MAX_GRADE))
        return heights

    def _emit_deck(self, f, g, pts, heights, elev_m, w0, w1):
        """Rasterize one bridge way's deck inside a band window.

        Works in a sub-window clipped to the way's bbox (+cross-section
        reach) so the EDT stays small. Returns (row_slice, col_slice,
        emit_mask, heights, zones) into band-window coordinates, or None
        when the way misses the window.
        """
        res = self.resolution_m
        e0, _n0, _e1, n1 = self.bounds
        outer = (f.params.half + f.params.curb + f.params.walk
                 + f.params.shoulder) + 2.0
        bx0, bn0, bx1, bn1 = geom_bbox(g)
        # Sub-window in grid coords (rows measured down from n1).
        c0 = max(0, int((bx0 - outer - e0) / res))
        c1 = min(self._shape[1], int((bx1 + outer - e0) / res) + 1)
        sr0 = max(w0, int((n1 - (bn1 + outer)) / res))
        sr1 = min(w1, int((n1 - (bn0 - outer)) / res) + 1)
        if sr1 <= sr0 or c1 <= c0:
            return None
        rows, cols = sr1 - sr0, c1 - c0
        sub_t = Affine(res, 0.0, e0 + c0 * res,
                       0.0, -res, n1 - sr0 * res)
        seed = np.full((rows, cols), np.nan, dtype=np.float32)
        for i, (e, n) in enumerate(pts):
            col = int((e - (e0 + c0 * res)) / res)
            r = int(((n1 - sr0 * res) - n) / res)
            if 0 <= r < rows and 0 <= col < cols:
                h = heights[min(i, len(heights) - 1)]
                if np.isnan(seed[r, col]) or h > seed[r, col]:
                    seed[r, col] = h
        mask = ~np.isnan(seed)
        if not mask.any():
            return None
        dist, idx = distance_transform_edt(
            ~mask, sampling=res, return_indices=True)
        zone = self._zone_from_dist(dist, f.params)
        h_near = seed[idx[0], idx[1]]
        cand = (zone > 0) & ~np.isnan(h_near)
        emit = np.zeros((rows, cols), dtype=bool)
        if elev_m is None:
            emit[cand] = True
        else:
            rr, cc = np.nonzero(cand)
            sub_e0 = e0 + c0 * res
            sub_n1 = n1 - sr0 * res
            for r, c in zip(rr.tolist(), cc.tolist()):
                el = elev_m(sub_e0 + (c + 0.5) * res,
                            sub_n1 - (r + 0.5) * res)
                if el is not None and h_near[r, c] >= el + DECK_MIN_M:
                    emit[r, c] = True
        if not emit.any():
            return None
        return (slice(sr0 - w0, sr1 - w0), slice(c0, c1),
                emit, h_near, zone)

    @staticmethod
    def _linestring(coords: list[tuple[float, float]]) -> dict:
        return {"type": "LineString", "coordinates": coords}

    def _zone_from_dist(self, dc: np.ndarray, p: RoadParams) -> np.ndarray:
        zone = np.zeros(dc.shape, dtype=np.uint8)
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

    def _classify(self, pairs: list[tuple[tuple, dict]], p: RoadParams,
                  shape: tuple[int, int], transform: Affine):
        """Distance-to-centerline zone raster for (bbox, geom) `pairs`.

        Works in a sub-window clipped to the pairs' union bbox inflated by
        the cross-section reach — cells beyond that can never classify, so
        the EDT stays exact while skipping most of the band. Returns
        (row_slice, col_slice, zone) in window coords, or None."""
        if not pairs:
            return None
        outer = p.half + p.curb + p.walk + p.shoulder
        sub = sub_window([b for b, _g in pairs],
                         outer + 2 * self.resolution_m, transform, shape)
        if sub is None:
            return None
        r0, r1, c0, c1, sub_t = sub
        center = rasterize([(g, 1) for _b, g in pairs],
                           out_shape=(r1 - r0, c1 - c0),
                           transform=sub_t, fill=0,
                           all_touched=True, dtype=np.uint8).astype(bool)
        if not center.any():
            return None
        dc = distance_transform_edt(~center, sampling=self.resolution_m)
        return (slice(r0, r1), slice(c0, c1), self._zone_from_dist(dc, p))

    # -- Signs --------------------------------------------------------------

    def _compute_signs(self, features, nodes) -> list[dict]:
        signs: list[dict] = []
        # Street-name blades: shared vertices between differently-named
        # signable ways are intersections.
        vert_names: dict[tuple[int, int], set[str]] = {}
        vert_dirs: dict[tuple[int, int], dict[str, float]] = {}
        vert_half: dict[tuple[int, int], dict[str, float]] = {}
        for f in features:
            if not f.name or f.highway not in _SIGNABLE:
                continue
            name = normalize_street(f.name)
            if not name:
                continue
            for i, (e, n) in enumerate(f.coords):
                key = (round(e * 4), round(n * 4))
                vert_names.setdefault(key, set()).add(name)
                j = min(i + 1, len(f.coords) - 1)
                i2 = max(i - 1, 0)
                if j != i2:
                    b = math.degrees(math.atan2(
                        f.coords[j][0] - f.coords[i2][0],
                        f.coords[j][1] - f.coords[i2][1]))
                    vert_dirs.setdefault(key, {})[name] = b
                    vert_half.setdefault(key, {})[name] = (
                        f.params.half + f.params.curb + f.params.walk)
        seen: set[tuple[int, int]] = set()
        for key, names in vert_names.items():
            if len(names) < 2:
                continue
            cell = (key[0] // 80, key[1] // 80)  # ~20 m cluster
            if cell in seen:
                continue
            seen.add(cell)
            e, n = key[0] / 4.0, key[1] / 4.0
            names_s = sorted(names)
            dirs = vert_dirs[key]
            halfs = vert_half[key]
            b0 = math.radians(dirs.get(names_s[0], 0.0))
            b1 = math.radians(dirs.get(names_s[1], 90.0))
            # Offset to the corner right-of-both streets.
            off = (max(halfs.get(names_s[0], 6.0),
                       halfs.get(names_s[1], 6.0)) + 2.0)
            de = (math.sin(b0 + math.pi / 2) + math.sin(b1 + math.pi / 2)) * off
            dn = (math.cos(b0 + math.pi / 2) + math.cos(b1 + math.pi / 2)) * off
            norm = math.hypot(de, dn)
            if norm < 1.0:
                de, dn = off, off  # parallel ways — just push NE
            signs.append({
                "e": round(e + de, 2), "n": round(n + dn, 2),
                "type": "street_name",
                "lines": names_s[:2],
                "rot": _bearing_to_rot(math.degrees(b1)),
            })
        # Stop/yield posts at their OSM nodes, offset right of travel.
        ways = [f for f in features if not f.bridge and not f.tunnel]
        index = _SegIndex()
        for wi, f in enumerate(ways):
            index.add_way(wi, f.coords)
        for node in nodes:
            e, n = node["pos"]
            best = None
            for wi, si in index.near(e, n, 12.0):
                f = ways[wi]
                if si + 1 >= len(f.coords):
                    continue
                d = _seg_point_dist(*f.coords[si], *f.coords[si + 1], e, n)
                if best is None or d < best[0]:
                    best = (d, f, si)
            if best is None or best[0] > 10.0:
                continue
            _d, f, si = best
            (x0, y0), (x1, y1) = f.coords[si], f.coords[si + 1]
            bearing = math.atan2(x1 - x0, y1 - y0)
            # Right-of-travel offset; blade faces back at oncoming traffic.
            rx, ry = math.cos(bearing), -math.sin(bearing)
            off = f.params.half + 2.0
            signs.append({
                "e": round(e + rx * off, 2), "n": round(n + ry * off, 2),
                "type": "stop" if node["kind"] == "stop" else "yield",
                "lines": ["STOP" if node["kind"] == "stop" else "YIELD"],
                "rot": _bearing_to_rot(math.degrees(bearing) + 180.0),
            })
        return signs

    # -- Accessors ----------------------------------------------------------

    def _gather(self, grid, east: np.ndarray, north: np.ndarray, fill):
        return gather2d(grid, self.bounds[0], self.bounds[3],
                        self.resolution_m, east, north, fill)

    def road_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) u8 road classes for cell-center vectors."""
        return np.asarray(self._gather(self._road.grid, east, north, 0),
                          dtype=np.uint8)

    def deck_h_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 deck heights; <=0 = no deck (matches deck_m)."""
        return np.asarray(self._gather(self._deck_h.grid, east, north, -1.0),
                          dtype=np.float64)

    def deck_c_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) u8 deck classes for cell-center vectors."""
        return np.asarray(self._gather(self._deck_c.grid, east, north, 0),
                          dtype=np.uint8)

    def road_class(self, east: float, north: float) -> int:
        """Road surface class at geo coords; ROAD_NONE = no road."""
        row, col = self._cell(east, north)
        if row is None:
            return ROAD_NONE
        return int(self._road[row, col])

    def deck_m(self, east: float, north: float) -> float:
        """Deck top elevation (meters) at geo coords; <0 = no deck."""
        row, col = self._cell(east, north)
        if row is None:
            return -1.0
        return float(self._deck_h[row, col])

    def deck_class(self, east: float, north: float) -> int:
        row, col = self._cell(east, north)
        if row is None:
            return ROAD_NONE
        return int(self._deck_c[row, col])

    def _cell(self, east: float, north: float):
        col = math.floor((east - self.bounds[0]) / self.resolution_m)
        row = math.floor((self.bounds[3] - north) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._shape[0] or col >= self._shape[1]:
            return None, None
        return row, col


def _elev_or(elev_m, e: float, n: float) -> float:
    v = elev_m(e, n)
    return float("nan") if v is None else float(v)


def _slope_envelope(req: np.ndarray, dists: list[float], grade: float):
    """Minimum profile >= req with |dh| <= grade*ds (upper Lipschitz
    envelope: h*[i] = max_j req[j] - g*dist(i,j))."""
    h = req.astype(np.float64).copy()
    n = len(h)
    for _ in range(3):
        for i in range(1, n):
            d = dists[i] - dists[i - 1]
            if h[i] < h[i - 1] - grade * d:
                h[i] = h[i - 1] - grade * d
        for i in range(n - 2, -1, -1):
            d = dists[i + 1] - dists[i]
            if h[i] < h[i + 1] - grade * d:
                h[i] = h[i + 1] - grade * d
    return list(h)


def _deck_class(zone: np.ndarray) -> np.ndarray:
    """Deck cells never render gravel shoulders — concrete pavement."""
    out = zone.copy()
    out[out == ROAD_SHOULDER] = ROAD_ASPHALT
    return out


def _bearing_to_rot(bearing: float) -> int:
    """Geo bearing (0=N, 90=E, clockwise) -> MC sign rotation (0=S)."""
    return int(round(((bearing + 180.0) % 360.0) / 22.5)) & 15


_STREET_SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
    "CIRCLE": "CIR", "TERRACE": "TER", "HIGHWAY": "HWY", "PARKWAY": "PKWY",
    "TRAIL": "TRL",
}

_DIRECTIONALS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}


def normalize_street(name: str) -> str:
    """Canonical street-blade text: 'South 14th Street' -> 'S 14TH ST'.
    Only a leading directional and the trailing suffix normalize —
    'Court Street' keeps its name and only loses 'Street'."""
    tokens = re.sub(r"[.,]", "", name.upper()).split()
    if not tokens:
        return ""
    out = list(tokens)
    if len(out) > 1:
        out[0] = _DIRECTIONALS.get(out[0], out[0])
        out[-1] = _STREET_SUFFIXES.get(out[-1], out[-1])
    return " ".join(out)
