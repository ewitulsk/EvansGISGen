"""Binary GeoWorld tile (.gwt) reader/writer. See format/README.md.

All multi-byte integers are big-endian. A tile covers TILE_SIZE x TILE_SIZE
block columns, stored row-major (index = local_z * TILE_SIZE + local_x).
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

MAGIC = b"GWTL"
VERSION = 1
TILE_SIZE = 256

COMP_NONE = 0
COMP_DEFLATE = 1

# Sentinel written into the elevation layer for columns with no source data.
NODATA = -32768

# Layer bit values; sections are written in ascending bit order.
LAYER_BITS = {
    "elevation": 0x01,   # i16[TILE^2]  target block Y per column
    "influence": 0x02,   # u8[TILE^2]   0..255 geographic influence
    "surface": 0x04,     # u8[TILE^2]   surface class ids
    "road": 0x08,        # u8[TILE^2]   road class ids
    "water": 0x10,       # bitset, ceil(TILE^2/8) bytes  (footprint mask)
    "water_depth": 0x20, # u8[TILE^2]   water depth in blocks (0 = dry)
}
_LAYER_ORDER = sorted(LAYER_BITS, key=LAYER_BITS.get)


def pack_elevation(ints: list[int] | tuple[int, ...]) -> bytes:
    """Pack 65536 block-Y values into big-endian int16."""
    import array

    a = array.array("h", ints)  # native int16
    if struct.pack("=h", 1) != struct.pack(">h", 1):  # little-endian host
        a.byteswap()
    return a.tobytes()


def pack_bitset(bits: bytes | bytearray | list[int] | tuple[int, ...]) -> bytes:
    """Pack per-column booleans (len TILE_SIZE^2) into a big-endian bitset.

    Bit i (column i) lives in byte i//8 at bit i%8 (LSB-first within a byte).
    """
    n = TILE_SIZE * TILE_SIZE
    if len(bits) != n:
        raise ValueError(f"bitset source must have {n} entries")
    out = bytearray(n // 8)
    for i, v in enumerate(bits):
        if v:
            out[i >> 3] |= 1 << (i & 7)
    return bytes(out)


def unpack_bitset(data: bytes) -> bytes:
    """Unpack a bitset payload back into one 0/1 byte per column."""
    out = bytearray(TILE_SIZE * TILE_SIZE)
    for i in range(len(out)):
        out[i] = (data[i >> 3] >> (i & 7)) & 1
    return bytes(out)


def _section(payload: bytes, compress: bool) -> bytes:
    if compress:
        stored = zlib.compress(payload, 9)
        codec = COMP_DEFLATE
    else:
        stored = payload
        codec = COMP_NONE
    return struct.pack(">BII", codec, len(payload), len(stored)) + stored


def tile_filename(tile_x: int, tile_z: int) -> str:
    return f"{tile_x:+05d}_{tile_z:+05d}.gwt"


def write_tile(path: str | Path, tile_x: int, tile_z: int, layers: dict[str, bytes],
               compress: bool = True) -> Path:
    mask = 0
    for name in layers:
        if name not in LAYER_BITS:
            raise ValueError(f"unknown layer {name!r}")
        mask |= LAYER_BITS[name]

    out = bytearray()
    out += MAGIC
    out += struct.pack(">HiiHH", VERSION, tile_x, tile_z, mask, 0)
    for name in _LAYER_ORDER:
        if name in layers:
            out += _section(layers[name], compress)

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


def read_tile(path: str | Path) -> tuple[int, int, dict[str, bytes]]:
    """Read a .gwt file. Returns (tile_x, tile_z, {layer_name: raw_bytes})."""
    data = Path(path).read_bytes()
    if data[:4] != MAGIC:
        raise ValueError(f"{path}: bad magic")
    version, tile_x, tile_z, mask, _reserved = struct.unpack(">HiiHH", data[4:18])
    if version != VERSION:
        raise ValueError(f"{path}: unsupported version {version}")

    layers: dict[str, bytes] = {}
    off = 18
    for name in _LAYER_ORDER:
        if not (mask & LAYER_BITS[name]):
            continue
        codec, raw_len, stored_len = struct.unpack(">BII", data[off:off + 9])
        off += 9
        stored = data[off:off + stored_len]
        off += stored_len
        if codec == COMP_DEFLATE:
            payload = zlib.decompress(stored)
        elif codec == COMP_NONE:
            payload = stored
        else:
            raise ValueError(f"{path}: unknown compression codec {codec}")
        if len(payload) != raw_len:
            raise ValueError(f"{path}: layer {name} length mismatch")
        layers[name] = payload
    return tile_x, tile_z, layers
