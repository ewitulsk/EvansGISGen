"""Dataset manifest.json writer/reader."""

from __future__ import annotations

import json
from pathlib import Path

from .transform import GeoTransform, Projection

FORMAT_VERSION = 1


def write_manifest(dataset_dir: str | Path, *, name: str, transform: GeoTransform,
                   projection: Projection | None, layers: list[str],
                   tiles: list[tuple[int, int]]) -> Path:
    dataset_dir = Path(dataset_dir)
    manifest = {
        "format_version": FORMAT_VERSION,
        "name": name,
        "tile_size": 256,
        "transform": transform.to_json(),
        "projection": projection.to_json() if projection else None,
        "layers": layers,
        "tile_count": len(tiles),
        "bounds": {
            "min_tile_x": min(t[0] for t in tiles),
            "max_tile_x": max(t[0] for t in tiles),
            "min_tile_z": min(t[1] for t in tiles),
            "max_tile_z": max(t[1] for t in tiles),
        } if tiles else None,
    }
    path = dataset_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path
