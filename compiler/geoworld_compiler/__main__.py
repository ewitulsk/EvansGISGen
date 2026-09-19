"""CLI: python -m geoworld_compiler <command>

Commands:
    build    Compile a dataset (currently: synthetic terrain + influence).
    fixture  Write a known-pattern .gwt tile for cross-language format tests.
    preview  Render dataset layers to PNGs (requires Pillow).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geoworld_compiler")
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="compile a .geoworld dataset")
    b.add_argument("--out", required=True, help="output dataset dir, e.g. datasets/synthetic.geoworld")
    b.add_argument("--name", default="unnamed")
    b.add_argument("--origin-x", type=int, default=0, help="minecraft x of geo origin")
    b.add_argument("--origin-z", type=int, default=0, help="minecraft z of geo origin")
    b.add_argument("--hscale", type=float, default=1.0, help="horizontal meters per block")
    b.add_argument("--vscale", type=float, default=1.0, help="vertical meters per block")
    b.add_argument("--datum-elevation", type=float, default=381.0,
                   help="real elevation (m) mapped to datum-y; ~Beatrice ground level")
    b.add_argument("--datum-y", type=int, default=64)
    b.add_argument("--base-elevation", type=float, default=381.0,
                   help="mean terrain elevation (m) for the synthetic source")
    b.add_argument("--radius-full", type=float, default=800.0,
                   help="fully-geographic radius (m) around the anchor")
    b.add_argument("--radius-edge", type=float, default=1400.0,
                   help="influence fades to vanilla at this radius (m)")
    b.add_argument("--crs", default="EPSG:32614", help="projected CRS (documentation)")
    b.add_argument("--anchor", default=None,
                   help="optional 'lat,lon' anchoring the geo origin in the CRS")

    f = sub.add_parser("fixture", help="write a known-pattern test tile")
    f.add_argument("--out", required=True, help="output .gwt path")

    p = sub.add_parser("preview", help="render dataset layers to PNGs")
    p.add_argument("--dataset", required=True, help="dataset dir")
    p.add_argument("--out", default=None, help="output dir (default <dataset>/debug)")

    args = parser.parse_args(argv)

    if args.command == "build":
        from .build import build_dataset
        from .sources import SyntheticSource
        from .transform import GeoTransform, Projection

        projection = None
        if args.anchor:
            lat, lon = (float(s) for s in args.anchor.split(","))
            projection = Projection(args.crs, lat, lon)

        transform = GeoTransform(
            origin_x=args.origin_x, origin_z=args.origin_z,
            horizontal_meters_per_block=args.hscale,
            vertical_meters_per_block=args.vscale,
            datum_elevation_meters=args.datum_elevation,
            datum_y=args.datum_y,
        )
        source = SyntheticSource(args.base_elevation, args.radius_full, args.radius_edge)
        out = build_dataset(args.out, name=args.name, transform=transform,
                            source=source, projection=projection)
        print(f"wrote dataset: {out}")
        return 0

    if args.command == "fixture":
        from .fixture import write_fixture

        print(f"wrote fixture: {write_fixture(args.out)}")
        return 0

    if args.command == "preview":
        from .preview import render

        for p_out in render(args.dataset, args.out):
            print(p_out)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
