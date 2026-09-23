"""Banded raster grids for county-scale dataset rects.

At 1 m resolution a Beatrice+Lincoln rect is ~1.4 G cells — too big to hold
resident, and a full-grid distance transform is an ~11 GB float64.
`BandedGrid` materializes the raster band-by-band into a disk-backed memmap
so peak RAM stays ~a few hundred MB; `band_windows` yields scratch windows
with extra context rows so band-local distance transforms stay exact (set
the margin >= the field's farthest spatial reach).
"""

from __future__ import annotations

import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from affine import Affine


def rects_intersect(a: tuple[float, float, float, float],
                    b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def geom_bbox(geom: dict) -> tuple[float, float, float, float]:
    """(min_e, min_n, max_e, max_n) of a GeoJSON LineString/Polygon."""
    coords = geom["coordinates"]
    pts = coords if geom["type"] == "LineString" else coords[0]
    es = [p[0] for p in pts]
    ns = [p[1] for p in pts]
    return (min(es), min(ns), max(es), max(ns))


def select_pairs(geoms: list[tuple[tuple[float, float, float, float], dict]],
                 rect: tuple[float, float, float, float]) -> list[tuple[tuple, dict]]:
    """(bbox, geom) pairs intersecting `rect`."""
    return [(b, g) for b, g in geoms if rects_intersect(b, rect)]


def select(geoms: list[tuple[tuple[float, float, float, float], dict]],
           rect: tuple[float, float, float, float]) -> list[dict]:
    """Geometries (stored as (bbox, geom) pairs) intersecting `rect` —
    pre-filters features per band so rasterize doesn't walk the whole
    dataset for every window."""
    return [g for b, g in select_pairs(geoms, rect)]


def sub_window(bboxes: list[tuple[float, float, float, float]],
               margin_m: float, transform: Affine,
               shape: tuple[int, int]):
    """Clip `shape` (rows, cols of the band window) to the union of `bboxes`
    inflated by `margin_m`. Returns (r0, r1, c0, c1, sub_transform) or None.

    For distance-transform work the margin must cover the field's farthest
    reach (plus a couple of cells) — then every output cell outside the
    sub-window is provably zero and the transform stays exact inside it.
    """
    if not bboxes:
        return None
    e0 = min(b[0] for b in bboxes) - margin_m
    n0 = min(b[1] for b in bboxes) - margin_m
    e1 = max(b[2] for b in bboxes) + margin_m
    n1 = max(b[3] for b in bboxes) + margin_m
    res = transform.a
    c0 = max(0, int(math.floor((e0 - transform.c) / res)))
    c1 = min(shape[1], int(math.ceil((e1 - transform.c) / res)))
    r0 = max(0, int(math.floor((transform.f - n1) / res)))
    r1 = min(shape[0], int(math.ceil((transform.f - n0) / res)))
    if r1 <= r0 or c1 <= c0:
        return None
    sub_t = Affine(res, 0.0, transform.c + c0 * res,
                   0.0, -res, transform.f - r0 * res)
    return r0, r1, c0, c1, sub_t


def run_bands(windows, fn, workers: int = 1) -> None:
    """Run `fn(r0, r1, w0, w1, window_transform)` over band windows.

    Bands write disjoint grid rows, so `workers` > 1 runs them on a thread
    pool — rasterio/scipy release the GIL, so EDT- and rasterize-bound
    bodies scale well.
    """
    windows = list(windows)
    if workers <= 1 or len(windows) <= 1:
        for w in windows:
            fn(*w)
        return
    with ThreadPoolExecutor(max_workers=min(workers, len(windows))) as ex:
        list(ex.map(lambda w: fn(*w), windows))


def gather2d(grid, e0: float, n1: float, res: float,
             east: np.ndarray, north: np.ndarray, fill):
    """Sample `grid` at cell centers -> an (H, W) array.

    `east` (W,) and `north` (H,) are geo-meter coordinates, `e0`/`n1` the
    geo coords of the grid's west/north edges. Cells address as
    floor((e - e0)/res) columns / floor((n1 - n)/res) rows — the same math
    as the scalar per-cell accessors. Out-of-grid cells read `fill`.

    Takes a strided-view fast path when the request is a contiguous,
    fully in-bounds block (the common case at hscale == res).
    """
    col = np.floor((east - e0) / res).astype(np.int64)
    row = np.floor((n1 - north) / res).astype(np.int64)
    ok_c = (col >= 0) & (col < grid.shape[1])
    ok_r = (row >= 0) & (row < grid.shape[0])
    if ok_c.all() and ok_r.all() and len(col) > 1 and len(row) > 1 \
            and (col[1:] - col[:-1] == 1).all() \
            and (row[1:] - row[:-1] == 1).all():
        return grid[row[0]:row[-1] + 1, col[0]:col[-1] + 1]
    r = np.clip(row, 0, grid.shape[0] - 1)
    c = np.clip(col, 0, grid.shape[1] - 1)
    out = grid[r[:, None], c[None, :]]
    if fill is not None:
        out = np.where(ok_r[:, None] & ok_c[None, :], out, fill)
    return out


class BandedGrid:
    """A (height, width) raster built in horizontal bands on disk.

    The backing store is a numpy memmap in the temp dir — the grid may be
    far larger than RAM; per-cell queries just page through the file.
    """

    def __init__(self, bounds: tuple[float, float, float, float],
                 resolution_m: float, dtype, band_rows: int = 4096):
        self.bounds = bounds
        self.resolution_m = resolution_m
        e0, n0, e1, n1 = bounds
        self.width = math.ceil((e1 - e0) / resolution_m)
        self.height = math.ceil((n1 - n0) / resolution_m)
        self.transform = Affine(resolution_m, 0.0, e0,
                                0.0, -resolution_m, n1)
        self.band_rows = band_rows
        fd, tmp = tempfile.mkstemp(prefix="geoworld-grid-", suffix=".bin")
        os.close(fd)
        self._tmp = tmp
        self.grid = np.memmap(tmp, dtype=dtype, mode="w+",
                              shape=(self.height, self.width))

    def __del__(self):
        # Best effort: drop the map, then unlink the backing file.
        try:
            del self.grid
            os.unlink(self._tmp)
        except Exception:
            pass

    def __getitem__(self, idx):
        return self.grid[idx]

    def band_windows(self, margin_m: float = 0.0):
        """Yield (r0, r1, w0, w1, window_transform) per band.

        Rows [r0, r1) are the band to fill; [w0, w1) is the scratch window
        including up to `margin_m` of context rows either side (clamped to
        the grid). After filling a window (rasterize, EDT, ...), call
        `commit(r0, r1, w0, window)`.
        """
        m = math.ceil(margin_m / self.resolution_m)
        e0, _, _, n1 = self.bounds
        res = self.resolution_m
        for r0 in range(0, self.height, self.band_rows):
            r1 = min(self.height, r0 + self.band_rows)
            w0 = max(0, r0 - m)
            w1 = min(self.height, r1 + m)
            wt = Affine(res, 0.0, e0, 0.0, -res, n1 - w0 * res)
            yield r0, r1, w0, w1, wt

    def window_rect(self, w0: int, w1: int) -> tuple[float, float, float, float]:
        """Geo rect covered by scratch rows [w0, w1) — for `select()`."""
        e0, _, e1, n1 = self.bounds
        res = self.resolution_m
        return (e0, n1 - w1 * res, e1, n1 - w0 * res)

    def commit(self, r0: int, r1: int, w0: int, window: np.ndarray) -> None:
        self.grid[r0:r1] = window[r0 - w0: r0 - w0 + (r1 - r0)]
