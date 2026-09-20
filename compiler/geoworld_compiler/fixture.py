"""Emit a tile with a known, documented pattern for cross-language tests.

The Java GeoTileTest asserts these exact values, proving the binary format
round-trips between the Python writer and the Java reader.

Pattern (i = local_z * 256 + local_x):
    elevation[i]    = (i * 7) % 500 - 250       (int16)
    influence[i]    = i % 256                   (u8)
    surface[i]      = i % 17                    (u8)
    road[i]         = i % 7                     (u8)
    water bit i     = set iff i % 3 == 0
    water_depth[i]  = i % 5                     (u8)
    building[i]     = i % 7                     (u8)
    building_levels[i] = i % 9                  (u8)
    roadz[i]        = NODATA if i % 5 == 0 else (i % 400) - 150   (i16)
    roade[i]        = i % 8                     (u8)
    building_id[i]  = i % 65536                 (u16)
    building_roof[i]= NODATA if i % 7 == 0 else (i % 300) - 100   (i16)
"""

from __future__ import annotations

from pathlib import Path

from .tileio import (NODATA, TILE_SIZE, pack_bitset, pack_elevation, pack_u16,
                     write_tile)


def write_fixture(path: str | Path) -> Path:
    n = TILE_SIZE * TILE_SIZE
    elevation = pack_elevation([(i * 7) % 500 - 250 for i in range(n)])
    influence = bytes(i % 256 for i in range(n))
    surface = bytes(i % 17 for i in range(n))
    road = bytes(i % 7 for i in range(n))
    water = pack_bitset([1 if i % 3 == 0 else 0 for i in range(n)])
    water_depth = bytes(i % 5 for i in range(n))
    building = bytes(i % 7 for i in range(n))
    building_levels = bytes(i % 9 for i in range(n))
    roadz = pack_elevation([NODATA if i % 5 == 0 else (i % 400) - 150
                            for i in range(n)])
    roade = bytes(i % 8 for i in range(n))
    building_id = pack_u16([i % 65536 for i in range(n)])
    building_roof = pack_elevation([NODATA if i % 7 == 0 else (i % 300) - 100
                                   for i in range(n)])
    return write_tile(path, 0, 0,
                      {"elevation": elevation, "influence": influence,
                       "surface": surface, "road": road, "water": water,
                       "water_depth": water_depth, "building": building,
                       "building_levels": building_levels,
                       "roadz": roadz, "roade": roade,
                       "building_id": building_id,
                       "building_roof": building_roof})
