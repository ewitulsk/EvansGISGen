"""Dataset build: sample sources per block column, emit .gwt tiles + manifest."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from .manifest import write_manifest
from .tileio import (NODATA, TILE_SIZE, pack_bitset, pack_elevation,
                     tile_filename, write_tile)
from .transform import GeoTransform, Projection


class Source(Protocol):
    """Elevation/influence provider in dataset geo space (see sources.py)."""

    def elevation_m(self, east: float, north: float) -> float | None: ...
    def influence(self, east: float, north: float) -> float: ...
    def extent_m(self) -> float: ...


class Hydro(Protocol):
    """Channel-depth provider in geo space (see hydro.py)."""

    def depth_m(self, east: float, north: float) -> float: ...


class Roads(Protocol):
    """Road surface-class provider in geo space (see roads.py)."""

    def road_class(self, east: float, north: float) -> int: ...


def build_dataset(
    out_dir: str | Path,
    *,
    name: str,
    transform: GeoTransform,
    source: Source,
    projection: Projection | None = None,
    hydro: Hydro | None = None,
    roads: Roads | None = None,
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
    any_water = False
    any_road = False
    n = TILE_SIZE * TILE_SIZE
    for tx in range(t_min_x, t_max_x + 1):
        for tz in range(t_min_z, t_max_z + 1):
            elevation = [0] * n
            influence = bytearray(n)
            water_depth = bytearray(n)
            road = bytearray(n)
            any_influence = False
            any_tile_water = False
            any_tile_road = False
            for lz in range(TILE_SIZE):
                bz = tz * TILE_SIZE + lz
                north = transform.north_meters(bz)
                for lx in range(TILE_SIZE):
                    bx = tx * TILE_SIZE + lx
                    east = transform.east_meters(bx)
                    w = source.influence(east, north)
                    i = lz * TILE_SIZE + lx
                    elev = source.elevation_m(east, north)
                    if elev is None:
                        # No-data columns are pure vanilla regardless of influence.
                        elevation[i] = NODATA
                        w = 0.0
                    else:
                        elevation[i] = transform.block_y(elev)
                        if hydro is not None:
                            depth_m = hydro.depth_m(east, north)
                            if depth_m > 0.0:
                                # Bake the riverbed into elevation: LiDAR
                                # water surface - channel depth = bed. The
                                # runtime fills water bed+1 .. bed+depth.
                                surface_y = transform.block_y(elev)
                                bed_y = transform.block_y(elev - depth_m)
                                depth_blocks = surface_y - bed_y
                                if depth_blocks > 0:
                                    elevation[i] = bed_y
                                    water_depth[i] = min(255, depth_blocks)
                                    any_tile_water = True
                    if roads is not None:
                        rc = roads.road_class(east, north)
                        if rc:
                            road[i] = rc
                            any_tile_road = True
                    influence[i] = min(255, max(0, int(w * 255.0 + 0.5)))
                    any_influence = any_influence or w > 0.0
            if any_influence:
                layers = {"elevation": pack_elevation(elevation),
                          "influence": bytes(influence)}
                if any_tile_water:
                    layers["water"] = pack_bitset([1 if d else 0 for d in water_depth])
                    layers["water_depth"] = bytes(water_depth)
                if any_tile_road:
                    layers["road"] = bytes(road)
                write_tile(tiles_dir / tile_filename(tx, tz), tx, tz, layers)
                written.append((tx, tz))
                any_water = any_water or any_tile_water
                any_road = any_road or any_tile_road
            print(f"  tile {tx:+d},{tz:+d}: {'written' if any_influence else 'skipped'}",
                  flush=True)

    layers = ["elevation", "influence"] + (["water", "water_depth"] if any_water else []) \
        + (["road"] if any_road else [])
    write_manifest(out, name=name, transform=transform, projection=projection,
                   layers=layers, tiles=written)
    return out
