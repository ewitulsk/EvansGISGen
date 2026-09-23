"""Composable geographic influence fields (Phase 4).

An influence field maps geo coordinates (meters east/north of the anchor)
to a weight in [0, 1]:

    0.0 = completely vanilla terrain
    1.0 = completely geographic terrain

The compiler samples the field per block column and bakes it into each
tile's influence layer; at runtime the generator simply does

    height = lerp(vanilla_height, geo_height, w)

so cities and highway corridors are never special to world generation —
they merely contribute sources to the field. Sources are combined with
`combine_max` (a column is geographic if ANY source claims it), which is
what Phase 10 needs to fuse the Beatrice region with the US-77 corridor.
"""

from __future__ import annotations

import math
from typing import Protocol, Sequence

import numpy as np


def smootherstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _ss_array(t: np.ndarray) -> np.ndarray:
    """Vectorized smootherstep — identical float64 op order to smootherstep."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class Field(Protocol):
    """An influence source in dataset geo space."""

    def weight(self, east: float, north: float) -> float:
        """Influence weight 0..1 at geo coordinates (east, north) meters."""
        ...

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 weights for 1-D cell-center vectors `east` (W,)
        and `north` (H,) — the vectorized twin of `weight`."""
        ...

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_east, min_north, max_east, max_north) containing all weight > 0."""
        ...

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        """True if any point inside `rect` could have weight > 0 — a tile
        that fails this is skipped without per-cell sampling. Exact or
        conservative; never false-negative."""
        ...


def _rects_intersect(a: tuple[float, float, float, float],
                     b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


class BoxRamp:
    """Square coverage region: weight 1 inside, smootherstep ramp to 0.

    The standard edge treatment for a bounded dataset region (e.g. the
    DEM coverage box): full geographic control until `ramp_m` inside the
    boundary, fading to vanilla so no worldgen seam is visible.
    """

    def __init__(self, half_extent_m: float, ramp_m: float):
        self.half_extent_m = half_extent_m
        self.ramp_m = ramp_m

    def weight(self, east: float, north: float) -> float:
        edge_dist = self.half_extent_m - max(abs(east), abs(north))
        if edge_dist <= 0.0:
            return 0.0
        return smootherstep(min(1.0, edge_dist / self.ramp_m))

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        edge = self.half_extent_m - np.maximum(np.abs(east)[None, :],
                                               np.abs(north)[:, None])
        return _ss_array(np.clip(edge / self.ramp_m, 0.0, 1.0))

    def bounds(self) -> tuple[float, float, float, float]:
        e = self.half_extent_m
        return (-e, -e, e, e)

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        e = self.half_extent_m
        return _rects_intersect(rect, (-e, -e, e, e))


class RectRamp:
    """Rectangular coverage region: weight 1 inside, smootherstep ramp to 0.

    BoxRamp generalized to an arbitrary rect — a second city region (e.g.
    Lincoln) that isn't centered on the anchor. The ramp sits inside the
    rect edge, same as BoxRamp.
    """

    def __init__(self, bounds: tuple[float, float, float, float],
                 ramp_m: float):
        self.rect = bounds
        self.ramp_m = ramp_m

    def weight(self, east: float, north: float) -> float:
        e0, n0, e1, n1 = self.rect
        edge_dist = min(east - e0, north - n0, e1 - east, n1 - north)
        if edge_dist <= 0.0:
            return 0.0
        return smootherstep(min(1.0, edge_dist / self.ramp_m))

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        e = east[None, :]
        n = north[:, None]
        e0, n0, e1, n1 = self.rect
        edge = np.minimum(np.minimum(e - e0, e1 - e),
                          np.minimum(n - n0, n1 - n))
        return _ss_array(np.clip(edge / self.ramp_m, 0.0, 1.0))

    def bounds(self) -> tuple[float, float, float, float]:
        return self.rect

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        return _rects_intersect(rect, self.rect)


class DiscRamp:
    """Circular region: weight 1 within full_m, smootherstep to 0 at edge_m."""

    def __init__(self, center_east: float, center_north: float,
                 full_m: float, edge_m: float):
        self.center_east = center_east
        self.center_north = center_north
        self.full_m = full_m
        self.edge_m = edge_m

    def weight(self, east: float, north: float) -> float:
        d = math.hypot(east - self.center_east, north - self.center_north)
        if d >= self.edge_m:
            return 0.0
        if d <= self.full_m:
            return 1.0
        return smootherstep((self.edge_m - d) / (self.edge_m - self.full_m))

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        d = np.hypot(east[None, :] - self.center_east,
                     north[:, None] - self.center_north)
        t = np.clip((self.edge_m - d) / (self.edge_m - self.full_m),
                    0.0, 1.0)
        return _ss_array(t)

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.center_east - self.edge_m, self.center_north - self.edge_m,
                self.center_east + self.edge_m, self.center_north + self.edge_m)

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        # Conservative: the disc's bounding square.
        return _rects_intersect(rect, self.bounds())


class CorridorRamp:
    """Polyline corridor: weight 1 within full_m of the line, ramp to 0.

    For linear features like US-77: a strip of full geographic control
    along the centerline with a smootherstep falloff into vanilla on each
    side. `polylines` are (east, north) geo-meter point sequences — one
    per road way, so disconnected ways never create phantom segments.
    """

    def __init__(self, polylines: Sequence[Sequence[tuple[float, float]]],
                 full_m: float, ramp_m: float):
        lines = [list(p) for p in polylines if len(p) >= 2]
        if not lines:
            raise ValueError("corridor needs at least one two-point line")
        self.polylines = lines
        self.full_m = full_m
        self.ramp_m = ramp_m
        reach = full_m + ramp_m
        all_pts = [p for line in lines for p in line]
        self._bounds = (min(e for e, _ in all_pts) - reach,
                        min(n for _, n in all_pts) - reach,
                        max(e for e, _ in all_pts) + reach,
                        max(n for _, n in all_pts) + reach)
        # Bounding box per segment, inflated by reach, for cheap rejection.
        self._segments = []
        for points in lines:
            for (e0, n0), (e1, n1) in zip(points, points[1:]):
                bbox = (min(e0, e1) - reach, min(n0, n1) - reach,
                        max(e0, e1) + reach, max(n0, n1) + reach)
                self._segments.append((e0, n0, e1 - e0, n1 - n0, bbox))

    def _dist(self, east: float, north: float) -> float:
        best = math.inf
        for e0, n0, de, dn, (bx0, bn0, bx1, bn1) in self._segments:
            if east < bx0 or east > bx1 or north < bn0 or north > bn1:
                continue
            seg_len_sq = de * de + dn * dn
            t = 0.0 if seg_len_sq == 0.0 else max(
                0.0, min(1.0, ((east - e0) * de + (north - n0) * dn) / seg_len_sq))
            d = math.hypot(east - (e0 + t * de), north - (n0 + t * dn))
            if d < best:
                best = d
        return best

    def weight(self, east: float, north: float) -> float:
        d = self._dist(east, north)
        if d >= self.full_m + self.ramp_m:
            return 0.0
        if d <= self.full_m:
            return 1.0
        return smootherstep(1.0 - (d - self.full_m) / self.ramp_m)

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        # Scalar fallback — CorridorRamp is meant to be baked via
        # corridor_field() before a build; this exists only for correctness
        # if one is passed to a source directly.
        ee, nn = np.meshgrid(east, north)
        out = np.empty(ee.shape, dtype=np.float64)
        it = np.nditer(ee, flags=["multi_index"])
        for _ in it:
            i, j = it.multi_index
            out[i, j] = self.weight(float(ee[i, j]), float(nn[i, j]))
        return out

    def bounds(self) -> tuple[float, float, float, float]:
        return self._bounds

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        if not _rects_intersect(rect, self._bounds):
            return False
        for _, _, _, _, bbox in self._segments:
            if _rects_intersect(rect, bbox):
                return True
        return False


class RasterField:
    """Influence field sampled from a precomputed weight grid.

    Analytic fields like CorridorRamp cost a segment loop per query; over a
    dataset-sized cell loop that dominates build time. This field bakes any
    distance-derived weight into a coarse grid (weights stored as 0..255
    bytes) — the same trick the raster sources use for classifications.
    """

    def __init__(self, grid, bounds: tuple[float, float, float, float],
                 cell_m: float):
        self._grid = grid          # uint8 weights 0..255
        self._bounds = bounds
        self._inv = 1.0 / cell_m

    def weight(self, east: float, north: float) -> float:
        col = math.floor((east - self._bounds[0]) * self._inv)
        row = math.floor((self._bounds[3] - north) * self._inv)
        if (row < 0 or col < 0 or row >= self._grid.shape[0]
                or col >= self._grid.shape[1]):
            return 0.0
        return self._grid[row, col] * (1.0 / 255.0)

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        col = np.floor((east - self._bounds[0]) * self._inv).astype(np.int64)
        row = np.floor((self._bounds[3] - north) * self._inv).astype(np.int64)
        ok = ((row >= 0) & (row < self._grid.shape[0]))[:, None] & \
             ((col >= 0) & (col < self._grid.shape[1]))[None, :]
        r = np.clip(row, 0, self._grid.shape[0] - 1)
        c = np.clip(col, 0, self._grid.shape[1] - 1)
        out = self._grid[r[:, None], c[None, :]].astype(np.float64) \
            * (1.0 / 255.0)
        return np.where(ok, out, 0.0)

    def bounds(self) -> tuple[float, float, float, float]:
        return self._bounds

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        # Exact: the weight grid cells overlapping the rect.
        e0, n0, e1, n1 = self._bounds
        c0 = math.floor((rect[0] - e0) * self._inv)
        c1 = math.floor((rect[2] - e0) * self._inv)
        r0 = math.floor((n1 - rect[3]) * self._inv)
        r1 = math.floor((n1 - rect[1]) * self._inv)
        c0 = max(0, c0)
        r0 = max(0, r0)
        c1 = min(self._grid.shape[1] - 1, c1)
        r1 = min(self._grid.shape[0] - 1, r1)
        if r1 < r0 or c1 < c0:
            return False
        return bool(self._grid[r0:r1 + 1, c0:c1 + 1].any())


def corridor_field(polylines: Sequence[Sequence[tuple[float, float]]],
                   bounds: tuple[float, float, float, float],
                   full_m: float, ramp_m: float,
                   cell_m: float = 8.0) -> RasterField:
    """Rasterize corridor polylines into a weight grid: distance transform
    from the centerlines, then a smootherstep ramp — identical semantics to
    CorridorRamp but O(1) per query."""
    import numpy as np
    from affine import Affine
    from rasterio.features import rasterize
    from scipy.ndimage import distance_transform_edt

    e0, n0, e1, n1 = bounds
    shape = (math.ceil((n1 - n0) / cell_m), math.ceil((e1 - e0) / cell_m))
    transform = Affine(cell_m, 0.0, e0, 0.0, -cell_m, n1)
    mask = rasterize(
        [({"type": "LineString", "coordinates": [list(p) for p in line]}, 1)
         for line in polylines if len(line) >= 2],
        out_shape=shape, transform=transform, dtype=np.uint8)
    dist = distance_transform_edt(mask == 0) * cell_m
    t = np.clip(1.0 - (dist - full_m) / ramp_m, 0.0, 1.0)
    grid = np.round(t * t * t * (t * (t * 6.0 - 15.0) + 10.0) * 255.0
                    ).astype(np.uint8)
    return RasterField(grid, bounds, cell_m)


def combine_max(*fields: Field) -> Field:
    """A column is geographic if any source claims it (union of fields)."""
    return _Combined(fields, max, "max")


def combine_sum(*fields: Field) -> Field:
    """Additive combination, capped at 1 — overlapping claims reinforce."""
    return _Combined(fields, lambda ws: min(1.0, sum(ws)), "sum")


class _Combined:
    def __init__(self, fields: tuple[Field, ...], op, kind: str):
        self._fields = fields
        self._op = op
        self._kind = kind

    def weight(self, east: float, north: float) -> float:
        return self._op(f.weight(east, north) for f in self._fields)

    def weights(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        ws = [f.weights(east, north) for f in self._fields]
        if not ws:
            return np.zeros((len(north), len(east)), dtype=np.float64)
        if self._kind == "max":
            return np.maximum.reduce(ws)
        return np.clip(np.sum(np.stack(ws), axis=0), 0.0, 1.0)

    def bounds(self) -> tuple[float, float, float, float]:
        bs = [f.bounds() for f in self._fields]
        if not bs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(b[0] for b in bs), min(b[1] for b in bs),
                max(b[2] for b in bs), max(b[3] for b in bs))

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        return any(f.may_claim(rect) for f in self._fields)
