"""PNG previews of compiled layers — catches data errors without launching MC.

Requires Pillow (optional dependency). Output goes to <dataset>/debug/*.png.
"""

from __future__ import annotations

import struct
from pathlib import Path

from .tileio import TILE_SIZE, read_tile, unpack_bitset


def render(dataset_dir: str | Path, out_dir: str | Path | None = None) -> list[Path]:
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("preview requires Pillow: pip install pillow")

    dataset_dir = Path(dataset_dir)
    out = Path(out_dir) if out_dir else dataset_dir / "debug"
    out.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for f in sorted((dataset_dir / "tiles").glob("*.gwt")):
        tx, tz, layers = read_tile(f)
        for name, payload in layers.items():
            img = Image.new("RGB", (TILE_SIZE, TILE_SIZE))
            px = img.load()
            for lz in range(TILE_SIZE):
                for lx in range(TILE_SIZE):
                    i = lz * TILE_SIZE + lx
                    if name == "elevation":
                        v = struct.unpack_from(">h", payload, i * 2)[0]
                        g = max(0, min(255, v))
                        px[lx, lz] = (0, g, 0)
                    elif name == "influence":
                        v = payload[i]
                        px[lx, lz] = (v, 0, 255 - v)
                    elif name == "water":
                        bits = unpack_bitset(payload)
                        px[lx, lz] = (0, 0, 255) if bits[i] else (0, 0, 0)
                    else:
                        v = payload[i]
                        px[lx, lz] = (v, v, v)
            p = out / f"{name}_{tx:+05d}_{tz:+05d}.png"
            img.save(p)
            written.append(p)
    return written
