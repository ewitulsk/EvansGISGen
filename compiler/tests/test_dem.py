"""DemSource tests using a small synthetic GeoTIFF written via rasterio."""

import numpy as np
import pytest
import rasterio
from affine import Affine

from geoworld_compiler.dem import DemSource

CRS = "EPSG:32614"
ANCHOR_E = 691_569.0
ANCHOR_N = 4_459_949.0
SRC_RES = 10.0            # source pixels are 10 m
SRC_HALF = 640.0          # source covers anchor +/- 640 m
NODATA = -9999.0


@pytest.fixture(scope="module")
def dem_tif(tmp_path_factory):
    """128x128 px GeoTIFF at 10 m res centered on the anchor, values 300 + col/10.

    A 100x100 m nodata hole sits northeast of the anchor (cols 70-79, rows 40-49).
    """
    path = tmp_path_factory.mktemp("dem") / "dem.tif"
    cols = int(2 * SRC_HALF / SRC_RES)  # 128
    rows = cols
    data = np.empty((rows, cols), np.float32)
    for r in range(rows):
        for c in range(cols):
            data[r, c] = 300.0 + c / 10.0
    data[40:50, 70:80] = NODATA  # hole east of center
    transform = Affine(SRC_RES, 0, ANCHOR_E - SRC_HALF, 0, -SRC_RES, ANCHOR_N + SRC_HALF)
    with rasterio.open(
        path, "w", driver="GTiff", height=rows, width=cols, count=1,
        dtype="float32", crs=CRS, transform=transform, nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
    return path


@pytest.fixture(scope="module")
def source(dem_tif):
    return DemSource(
        [dem_tif], geo_crs=CRS, anchor_east=ANCHOR_E, anchor_north=ANCHOR_N,
        half_extent_m=256, ramp_m=64,
    )


def test_center_elevation(source):
    # Anchor column maps to source col ~64 -> 300 + 6.4.
    v = source.elevation_m(0.0, 0.0)
    assert v is not None
    assert abs(v - 306.4) < 2.0


def test_east_gradient(source):
    west = source.elevation_m(-100.0, 0.0)
    east = source.elevation_m(100.0, 0.0)
    assert west is not None and east is not None
    assert east - west == pytest.approx(2.0, abs=0.5)


def test_out_of_bounds_returns_none(source):
    # Grid is padded to half_extent + TILE_SIZE = 512 m; beyond that -> None.
    assert source.elevation_m(600.0, 0.0) is None
    assert source.elevation_m(0.0, -600.0) is None


def test_nodata_hole_is_filled(source):
    # Hole = source cols 70-79, rows 40-49 -> geo east +65..+155, north +145..+235.
    v = source.elevation_m(110.0, 190.0)
    assert v is not None
    assert 300.0 < v < 320.0  # interpolated, not a sentinel


def test_influence_ramp(source):
    assert source.influence(0.0, 0.0) == 1.0
    assert source.influence(256.0, 0.0) == 0.0        # at/ beyond edge
    mid = source.influence(200.0, 0.0)                # inside ramp zone
    assert 0.0 < mid < 1.0


def test_rect_bounds(dem_tif):
    # Rectangular bounds sample an asymmetric region - corridor support.
    src = DemSource(
        [dem_tif], geo_crs=CRS, anchor_east=ANCHOR_E, anchor_north=ANCHOR_N,
        bounds_m=(-100.0, -50.0, 100.0, 400.0), ramp_m=64,
    )
    assert src.elevation_m(0.0, 0.0) is not None
    assert src.elevation_m(0.0, 300.0) is not None   # tall north extent
    # bounds() unions the influence field's reach: the default BoxRamp
    # takes the max absolute bound (400) as its square half-extent.
    assert src.bounds() == (-400.0, -400.0, 400.0, 400.0)
