"""Elevation/influence sources.

Source protocol (geo space = meters east/north of the anchor):
    elevation_m(east, north) -> float | None   (None = no data)
    influence(east, north)   -> float 0..1
    bounds()                 -> (min_e, min_n, max_e, max_n) tile coverage rect

SyntheticSource exists to prove the pipeline end-to-end; DemSource (dem.py)
is the real Phase 3 terrain source.
"""

from __future__ import annotations

import math

import numpy as np

from .influence import DiscRamp, smootherstep

__all__ = ["SyntheticSource", "smootherstep"]


class SyntheticSource:
    """Deterministic rolling terrain + circular influence region.

    Produces gentle Nebraska-like relief (a few meters to tens of meters of
    elevation change) centered on the geo origin. Used until the real Beatrice
    DEM pipeline lands in Phase 3.
    """

    def __init__(self, base_elevation_m: float, radius_full_m: float, radius_edge_m: float):
        self.base_elevation_m = base_elevation_m
        self.radius_edge_m = radius_edge_m
        self._field = DiscRamp(0.0, 0.0, radius_full_m, radius_edge_m)

    def elevation_m(self, east: float, north: float) -> float:
        """Synthetic elevation: broad swells plus medium-frequency rolling."""
        return (
            self.base_elevation_m
            + 14.0 * math.sin(east / 340.0) * math.cos(north / 290.0)
            + 6.0 * math.sin(east / 95.0 + 1.3) * math.sin(north / 120.0 - 0.7)
            + 1.5 * math.sin(east / 31.0) * math.sin(north / 27.0)
        )

    def elevation_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 synthetic elevation for cell-center vectors."""
        ee, nn = np.meshgrid(east, north)
        return (
            self.base_elevation_m
            + 14.0 * np.sin(ee / 340.0) * np.cos(nn / 290.0)
            + 6.0 * np.sin(ee / 95.0 + 1.3) * np.sin(nn / 120.0 - 0.7)
            + 1.5 * np.sin(ee / 31.0) * np.sin(nn / 27.0)
        )

    def influence(self, east: float, north: float) -> float:
        """0..1 geographic influence: full inside radius_full, ramp to edge."""
        return self._field.weight(east, north)

    def influence_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        return self._field.weights(east, north)

    def bounds(self) -> tuple[float, float, float, float]:
        e = self.radius_edge_m
        return (-e, -e, e, e)

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        return self._field.may_claim(rect)
