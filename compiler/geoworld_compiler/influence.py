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


def smootherstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class Field(Protocol):
    """An influence source in dataset geo space."""

    def weight(self, east: float, north: float) -> float:
        """Influence weight 0..1 at geo coordinates (east, north) meters."""
        ...

    def extent_m(self) -> float:
        """Half-extent of a square around the origin containing all weight > 0."""
        ...


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

    def extent_m(self) -> float:
        return self.half_extent_m


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

    def extent_m(self) -> float:
        return math.hypot(self.center_east, self.center_north) + self.edge_m


class CorridorRamp:
    """Polyline corridor: weight 1 within full_m of the line, ramp to 0.

    For linear features like US-77: a strip of full geographic control
    along the centerline with a smootherstep falloff into vanilla on each
    side. `points` are (east, north) geo meters.
    """

    def __init__(self, points: Sequence[tuple[float, float]],
                 full_m: float, ramp_m: float):
        if len(points) < 2:
            raise ValueError("corridor needs at least two points")
        self.points = list(points)
        self.full_m = full_m
        self.ramp_m = ramp_m
        reach = full_m + ramp_m
        self._extent = max(math.hypot(e, n) for e, n in points) + reach
        # Bounding box per segment, inflated by reach, for cheap rejection.
        self._segments = []
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

    def extent_m(self) -> float:
        return self._extent


def combine_max(*fields: Field) -> Field:
    """A column is geographic if any source claims it (union of fields)."""
    return _Combined(fields, max)


def combine_sum(*fields: Field) -> Field:
    """Additive combination, capped at 1 — overlapping claims reinforce."""
    return _Combined(fields, lambda ws: min(1.0, sum(ws)))


class _Combined:
    def __init__(self, fields: tuple[Field, ...], op):
        self._fields = fields
        self._op = op

    def weight(self, east: float, north: float) -> float:
        return self._op(f.weight(east, north) for f in self._fields)

    def extent_m(self) -> float:
        return max((f.extent_m() for f in self._fields), default=0.0)
