"""Emit a tile with a known, documented pattern for cross-language tests.

The Java GeoTileTest asserts these exact values, proving the binary format
round-trips between the Python writer and the Java reader.

Pattern (i = local_z * 256 + local_x):
    elevation[i] = (i * 7) % 500 - 250          (int16)
    influence[i] = i % 256                      (u8)
    surface[i]   = i % 17                       (u8)
    water bit i  = set iff i % 3 == 0
"""

from __future__ import annotations

from pathlib import Path

from .tileio import TILE_SIZE, pack_bitset, pack_elevation, write_tile


def write_fixture(path: str | Path) -> Path:
    n = TILE_SIZE * TILE_SIZE
    elevation = pack_elevation([(i * 7) % 500 - 250 for i in range(n)])
    influence = bytes(i % 256 for i in range(n))
    surface = bytes(i % 17 for i in range(n))
    water = pack_bitset([1 if i % 3 == 0 else 0 for i in range(n)])
    return write_tile(path, 0, 0,
                      {"elevation": elevation, "influence": influence,
                       "surface": surface, "water": water})
