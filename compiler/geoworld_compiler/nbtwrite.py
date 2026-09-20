"""Minimal vanilla structure-template .nbt writer (gzipped, big-endian) —
the format StructureTemplate.load reads. Used by export-parcel to hand lot
outlines to the studio / Structure Lab.
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

DATA_VERSION = 3955  # Minecraft 1.21.1


def _named(tid: int, name: str, payload: bytes) -> bytes:
    return bytes([tid]) + struct.pack(">H", len(name)) + name.encode() + payload


def _t_int(name, v):
    return _named(3, name, struct.pack(">i", v))


def _t_str(name, v):
    return _named(8, name, struct.pack(">H", len(v)) + v.encode())


def _t_list(name, tid, items):
    return _named(9, name, bytes([tid]) + struct.pack(">i", len(items))
                  + b"".join(items))


def _t_cmp(name, entries):
    return _named(10, name, b"".join(entries) + b"\x00")


def _cmp_payload(entries):
    return b"".join(entries) + b"\x00"


def _ints(vals):
    return [struct.pack(">i", v) for v in vals]


def write_structure(path: str | Path, size: tuple[int, int, int],
                    blocks: list[tuple[int, int, int, str]]) -> Path:
    """blocks: (x, y, z, block_id) — cells not listed become air."""
    palette: list[str] = ["minecraft:air"]
    index = {"minecraft:air": 0}
    entries = []
    for x, y, z, name in blocks:
        if name not in index:
            index[name] = len(palette)
            palette.append(name)
        entries.append(_cmp_payload([
            _t_list("pos", 3, _ints([x, y, z])),
            _t_int("state", index[name]),
        ]))
    root = _t_cmp("", [
        _t_list("size", 3, _ints(list(size))),
        _t_list("blocks", 10, entries),
        _t_list("palette", 10,
                [_cmp_payload([_t_str("Name", n)]) for n in palette]),
        _t_list("entities", 10, []),
        _t_int("DataVersion", DATA_VERSION),
    ])
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(root))
    return path
