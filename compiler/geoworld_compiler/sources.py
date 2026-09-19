"""Elevation/influence sources.

Source protocol (geo space = meters east/north of the anchor):
    elevation_m(east, north) -> float | None   (None = no data)
    influence(east, north)   -> float 0..1
    extent_m()               -> float   (half-extent of tile coverage square)

SyntheticSource exists to prove the pipeline end-to-end; DemSource (dem.py)
is the real Phase 3 terrain source.
"""

from __future__ import annotations

import math


def smootherstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


class SyntheticSource:
    """Deterministic rolling terrain + circular influence region.

    Produces gentle Nebraska-like relief (a few meters to tens of meters of
    elevation change) centered on the geo origin. Used until the real Beatrice
    DEM pipeline lands in Phase 3.
    """

    def __init__(self, base_elevation_m: float, radius_full_m: float, radius_edge_m: float):
        self.base_elevation_m = base_elevation_m
        self.radius_full_m = radius_full_m
        self.radius_edge_m = radius_edge_m

    def elevation_m(self, east: float, north: float) -> float:
        """Synthetic elevation: broad swells plus medium-frequency rolling."""
        return (
            self.base_elevation_m
            + 14.0 * math.sin(east / 340.0) * math.cos(north / 290.0)
            + 6.0 * math.sin(east / 95.0 + 1.3) * math.sin(north / 120.0 - 0.7)
            + 1.5 * math.sin(east / 31.0) * math.sin(north / 27.0)
        )

    def influence(self, east: float, north: float) -> float:
        """0..1 geographic influence: full inside radius_full, ramp to edge."""
        d = math.hypot(east, north)
        if d >= self.radius_edge_m:
            return 0.0
        if d <= self.radius_full_m:
            return 1.0
        return smootherstep((self.radius_edge_m - d) / (self.radius_edge_m - self.radius_full_m))

    def extent_m(self) -> float:
        return self.radius_edge_m
