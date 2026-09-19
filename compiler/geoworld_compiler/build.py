"""Dataset build: sample sources per block column, emit .gwt tiles + manifest."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from .manifest import write_manifest
from .tileio import NODATA, TILE_SIZE, pack_elevation, tile_filename, write_tile
from .transform import GeoTransform, Projection


class Source(Protocol):
    """Elevation/influence provider in dataset geo space (see sources.py)."""

    def elevation_m(self, east: float, north: float) -> float | None: ...
    def influence(self, east: float, north: float) -> float: ...
    def extent_m(self) -> float: ...


def build_dataset(
    out_dir: str | Path,
    *,
    name: str,
    transform: GeoTransform,
    source: Source,
    projection: Projection | None = None,
) -> Path:
    """Emit a .geoworld dataset directory. Returns the dataset dir."""
    out = Path(out_dir)
    tiles_dir = out / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # Tile coverage: square bounding box of the source extent, in tiles.
    edge_blocks = math.ceil(source.extent_m() / transform.horizontal_meters_per_block)
    t_min_x = math.floor((transform.origin_x - edge_blocks) / TILE_SIZE)
    t_max_x = math.floor((transform.origin_x + edge_blocks) / TILE_SIZE)
    t_min_z = math.floor((transform.origin_z - edge_blocks) / TILE_SIZE)
    t_max_z = math.floor((transform.origin_z + edge_blocks) / TILE_SIZE)

    written: list[tuple[int, int]] = []
    n = TILE_SIZE * TILE_SIZE
    for tx in range(t_min_x, t_max_x + 1):
        for tz in range(t_min_z, t_max_z + 1):
            elevation = [0] * n
            influence = bytearray(n)
            any_influence = False
            for lz in range(TILE_SIZE):
                bz = tz * TILE_SIZE + lz
                north = transform.north_meters(bz)
                for lx in range(TILE_SIZE):
                    bx = tx * TILE_SIZE + lx
                    east = transform.east_meters(bx)
                    w = source.influence(east, north)
                    i = lz * TILE_SIZE + lx
                    elev = source.elevation_m(east, north)
                    elevation[i] = NODATA if elev is None else transform.block_y(elev)
                    # No-data columns are pure vanilla regardless of influence.
                    if elev is None:
                        w = 0.0
                    influence[i] = min(255, max(0, int(w * 255.0 + 0.5)))
                    any_influence = any_influence or w > 0.0
            if any_influence:
                write_tile(tiles_dir / tile_filename(tx, tz), tx, tz,
                           {"elevation": pack_elevation(elevation), "influence": bytes(influence)})
                written.append((tx, tz))
            print(f"  tile {tx:+d},{tz:+d}: {'written' if any_influence else 'skipped'}",
                  flush=True)

    write_manifest(out, name=name, transform=transform, projection=projection,
                   layers=["elevation", "influence"], tiles=written)
    return out
