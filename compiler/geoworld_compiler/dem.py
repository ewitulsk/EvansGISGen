"""DEM-backed elevation source: mosaic -> reproject -> sample at 1 m grid.

Reads real elevation rasters (e.g. USGS 3DEP 1 m GeoTIFFs fetched via
`geoworld_compiler fetch`), mosaics the needed extent, reprojects into dataset
geo space (meters east/north of the anchor), and answers per-column queries.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine

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
        half_extent_m: float,
        ramp_m: float,
        resolution_m: float = 1.0,
        influence: Field | None = None,
    ):
        self.anchor_east = anchor_east
        self.anchor_north = anchor_north
        self.half_extent_m = half_extent_m
        self.ramp_m = ramp_m
        self.resolution_m = resolution_m
        # Influence is a composable field (influence.py); default = the
        # coverage box ramp. Corridors/regions are combined via combine_max.
        self._influence_field = influence or BoxRamp(half_extent_m, ramp_m)
        self._extent_m = max(half_extent_m, self._influence_field.extent_m())

        # Pad the sampling grid past the influence extent so boundary tiles
        # (rounded up to 256-block edges) still have real data to read.
        pad = half_extent_m + TILE_SIZE
        x0 = anchor_east - pad
        x1 = anchor_east + pad
        y0 = anchor_north - pad
        y1 = anchor_north + pad
        self._x0, self._y1 = x0, y1  # west/north edges of the geo grid

        srcs = [rasterio.open(p) for p in tif_paths]
        src_crs = srcs[0].crs
        # Crop bounds must be in the source CRS — transform all four corners of
        # the geo bbox (the square is slightly rotated in the source CRS, so
        # opposite corners alone would under-cover the region).
        to_src = Transformer.from_crs(geo_crs, src_crs, always_xy=True)
        corners = [to_src.transform(x, y)
                   for x in (x0, x1) for y in (y0, y1)]
        src_bounds = (min(c[0] for c in corners), min(c[1] for c in corners),
                      max(c[0] for c in corners), max(c[1] for c in corners))
        mosaic, mosaic_transform = merge(srcs, bounds=src_bounds)
        nodata = srcs[0].nodata
        for s in srcs:
            s.close()

        width = math.ceil((x1 - x0) / resolution_m)
        height = math.ceil((y1 - y0) / resolution_m)
        self._dst_transform = Affine(resolution_m, 0.0, x0, 0.0, -resolution_m, y1)
        grid = np.full((height, width), np.nan, dtype=np.float32)
        reproject(
            source=mosaic[0].astype(np.float32),
            src_transform=mosaic_transform,
            src_crs=src_crs,
            src_nodata=nodata,
            destination=grid,
            dst_transform=self._dst_transform,
            dst_crs=geo_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
        )
        # Fill any uncovered band at the edges by interpolation so the compiled
        # region has no holes; influence ramping still fades the rim to vanilla.
        valid = ~np.isnan(grid)
        if not valid.all():
            fillnodata(grid, mask=valid.astype(np.uint8))
        self._grid = grid

    def extent_m(self) -> float:
        """Half-extent of the square coverage region, in geo meters.

        Covers the influence field's reach (e.g. a corridor extending past
        the DEM box), not just the elevation source itself.
        """
        return self._extent_m

    def elevation_m(self, east: float, north: float) -> float | None:
        col = math.floor((self.anchor_east + east - self._x0) / self.resolution_m)
        row = math.floor((self._y1 - (self.anchor_north + north)) / self.resolution_m)
        if row < 0 or col < 0 or row >= self._grid.shape[0] or col >= self._grid.shape[1]:
            return None
        v = self._grid[row, col]
        return None if np.isnan(v) else float(v)

    def influence(self, east: float, north: float) -> float:
        return self._influence_field.weight(east, north)
