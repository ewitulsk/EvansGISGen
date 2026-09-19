"""Dataset build: sample sources per block column, emit .gwt tiles + manifest."""

from __future__ import annotations

import math
from pathlib import Path

from .manifest import write_manifest
from .sources import SyntheticSource
from .tileio import TILE_SIZE, pack_elevation, tile_filename, write_tile
from .transform import GeoTransform, Projection


def build_dataset(
    out_dir: str | Path,
    *,
    name: str,
    transform: GeoTransform,
    source: SyntheticSource,
    projection: Projection | None = None,
) -> Path:
    """Emit a .geoworld dataset directory. Returns the dataset dir."""
    out = Path(out_dir)
    tiles_dir = out / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # Tile coverage: bounding box of the influence edge circle, in tiles.
    edge_blocks = math.ceil(source.radius_edge_m / transform.horizontal_meters_per_block)
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
                    elevation[i] = transform.block_y(source.elevation_m(east, north))
                    influence[i] = min(255, max(0, int(w * 255.0 + 0.5)))
                    any_influence = any_influence or w > 0.0
            if any_influence:
                write_tile(tiles_dir / tile_filename(tx, tz), tx, tz,
                           {"elevation": pack_elevation(elevation), "influence": bytes(influence)})
                written.append((tx, tz))

    write_manifest(out, name=name, transform=transform, projection=projection,
                   layers=["elevation", "influence"], tiles=written)
    return out
