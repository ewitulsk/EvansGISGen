"""DEM-backed elevation source: mosaic -> reproject -> sample at 1 m grid.

Reads real elevation rasters (e.g. USGS 3DEP 1 m GeoTIFFs fetched via
`geoworld_compiler fetch`), mosaics the needed extent, reprojects into dataset
geo space (meters east/north of the anchor), and answers per-column queries.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

from .banded import gather2d
from .tileio import TILE_SIZE
from pyproj import Transformer
from rasterio.fill import fillnodata
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject

from .influence import BoxRamp, Field


class DemSource:
    """Elevation field from DEM raster(s), resampled to the geo meter grid.

    east/north arguments are dataset geo coordinates (meters relative to the
    anchor); internally we work in the projected CRS the anchor was defined in.
    """

    def __init__(
        self,
        tif_paths: list[str | Path],
        *,
        geo_crs: str,
        anchor_east: float,
        anchor_north: float,
        bounds_m: tuple[float, float, float, float] | None = None,
        half_extent_m: float | None = None,
        ramp_m: float,
        resolution_m: float = 1.0,
        influence: Field | None = None,
    ):
        self.anchor_east = anchor_east
        self.anchor_north = anchor_north
        self.ramp_m = ramp_m
        self.resolution_m = resolution_m
        # bounds_m: (min_east, min_north, max_east, max_north) of the sampled
        # region in geo meters — rectangular so corridors stay narrow.
        # half_extent_m remains as the square shorthand.
        if bounds_m is None:
            if half_extent_m is None:
                raise ValueError("DemSource needs bounds_m or half_extent_m")
            bounds_m = (-half_extent_m, -half_extent_m,
                        half_extent_m, half_extent_m)
        # Influence is a composable field (influence.py); default = a box
        # ramp over the sampled rect. Corridors/regions compose via combine_max.
        if influence is None:
            influence = BoxRamp(max(abs(bounds_m[0]), abs(bounds_m[1]),
                                    abs(bounds_m[2]), abs(bounds_m[3])), ramp_m)
        self._influence_field = influence
        fb = influence.bounds()
        self._bounds = (min(bounds_m[0], fb[0]), min(bounds_m[1], fb[1]),
                        max(bounds_m[2], fb[2]), max(bounds_m[3], fb[3]))

        # Pad the sampling grid past the bounds so boundary tiles (rounded
        # up to 256-block edges) still have real data to read.
        x0 = anchor_east + bounds_m[0] - TILE_SIZE
        y0 = anchor_north + bounds_m[1] - TILE_SIZE
        x1 = anchor_east + bounds_m[2] + TILE_SIZE
        y1 = anchor_north + bounds_m[3] + TILE_SIZE
        self._x0, self._y1 = x0, y1  # west/north edges of the geo grid

        srcs = [rasterio.open(p) for p in tif_paths]
        src_crs = srcs[0].crs
        nodata = srcs[0].nodata
        # Per-source bounds for band-level coverage tests (a band beyond all
        # rasters stays NaN -> vanilla instead of a fake filled plateau).
        src_boxes = [tuple(s.bounds) for s in srcs]
        to_src = Transformer.from_crs(geo_crs, src_crs, always_xy=True)

        width = math.ceil((x1 - x0) / resolution_m)
        height = math.ceil((y1 - y0) / resolution_m)
        self._dst_transform = Affine(resolution_m, 0.0, x0, 0.0, -resolution_m, y1)

        # County-scale regions (Beatrice -> Lincoln is ~80 km) are far too big
        # to mosaic + reproject in one resident array. Process the destination
        # grid in horizontal bands backed by a disk memmap: peak RAM stays at
        # ~a few hundred MB regardless of region size.
        fd, tmp_path = tempfile.mkstemp(prefix="geoworld-dem-", suffix=".f32")
        os.close(fd)
        grid = np.memmap(tmp_path, dtype=np.float32, mode="w+",
                         shape=(height, width))
        self._grid_tmp = tmp_path

        band_rows = 2048
        for r0 in range(0, height, band_rows):
            r1 = min(height, r0 + band_rows)
            by1 = y1 - r0 * resolution_m          # band's north edge
            by0 = y1 - r1 * resolution_m          # band's south edge
            # Crop bounds in the source CRS — all four corners, since the geo
            # rect is slightly rotated there.
            corners = [to_src.transform(x, y)
                       for x in (x0, x1) for y in (by0, by1)]
            bb = (min(c[0] for c in corners), min(c[1] for c in corners),
                  max(c[0] for c in corners), max(c[1] for c in corners))
            if not any(bb[0] < sb[2] and bb[2] > sb[0]
                       and bb[1] < sb[3] and bb[3] > sb[1]
                       for sb in src_boxes):
                continue                            # no DEM in this band
            mosaic, mosaic_transform = merge(srcs, bounds=bb)
            band = np.full((r1 - r0, width), np.nan, dtype=np.float32)
            reproject(
                source=mosaic[0].astype(np.float32),
                src_transform=mosaic_transform,
                src_crs=src_crs,
                src_nodata=nodata,
                destination=band,
                dst_transform=Affine(resolution_m, 0.0, x0,
                                     0.0, -resolution_m, by1),
                dst_crs=geo_crs,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            # Fill uncovered pockets inside the band so the compiled region
            # has no holes; bands with no data at all keep NaN -> vanilla.
            valid = ~np.isnan(band)
            if valid.any() and not valid.all():
                fillnodata(band, mask=valid.astype(np.uint8))
            grid[r0:r1] = band
            print(f"  dem band rows {r0}-{r1}", flush=True)
            del mosaic, band
        grid.flush()
        for s in srcs:
            s.close()
        self._grid = grid

    def __del__(self):
        # Best effort: drop the memmap then unlink its backing file.
        try:
            del self._grid
            os.unlink(self._grid_tmp)
        except Exception:
            pass

    def bounds(self) -> tuple[float, float, float, float]:
        """(min_e, min_n, max_e, max_n) coverage rect in geo meters.

        Covers the influence field's reach (e.g. a corridor extending past
        the DEM box), not just the elevation source itself.
        """
        return self._bounds

    def elevation_m(self, east: float, north: float) -> float | None:
        col = math.floor((self.anchor_east + east - self._x0) / self.resolution_m)
        row = math.floor((self._y1 - (self.anchor_north + north)) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._grid.shape[0] or col >= self._grid.shape[1]:
            return None
        v = self._grid[row, col]
        return None if np.isnan(v) else float(v)

    def elevation_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 elevations for cell-center vectors east (W,) /
        north (H,); NaN outside coverage or at nodata cells."""
        out = gather2d(self._grid, self._x0, self._y1, self.resolution_m,
                       self.anchor_east + east, self.anchor_north + north,
                       np.nan)
        return np.asarray(out, dtype=np.float64)

    def influence_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        return self._influence_field.weights(east, north)

    def influence(self, east: float, north: float) -> float:
        return self._influence_field.weight(east, north)

    def may_claim(self, rect: tuple[float, float, float, float]) -> bool:
        """True if the influence field could claim anything inside `rect`
        — lets the build skip whole tiles without per-cell sampling."""
        return self._influence_field.may_claim(rect)
