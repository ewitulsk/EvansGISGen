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
    b.add_argument("--source", choices=["synthetic", "dem"], default="synthetic")
    b.add_argument("--dem", nargs="+", default=None,
                   help="DEM raster path(s) (GeoTIFF etc.) for --source dem")
    b.add_argument("--radius", type=float, default=4000.0,
                   help="dem: half-extent of the compiled region (m)")
    b.add_argument("--ramp", type=float, default=500.0,
                   help="dem: influence ramp width at the coverage edge (m)")
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
                   help="'lat,lon' anchoring the geo origin in the CRS "
                        "(required for --source dem)")
    b.add_argument("--hydro", default=None,
                   help="OSM water JSON from fetch-water; adds channel depth "
                        "and carves riverbeds into the elevation layer")
    b.add_argument("--roads", default=None,
                   help="OSM highway JSON from fetch-roads; emits the road "
                        "surface-class layer")
    b.add_argument("--landuse", default=None,
                   help="OSM land-use JSON from fetch-landuse; emits the "
                        "surface class layer")
    b.add_argument("--buildings", default=None,
                   help="OSM building JSON from fetch-buildings; emits the "
                        "building class/levels layers")

    fe = sub.add_parser("fetch", help="download DEM rasters from the USGS TNM API")
    fe.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fe.add_argument("--out", required=True, help="output dir for rasters")
    fe.add_argument("--dataset", default="Digital Elevation Model (DEM) 1 meter")

    fw = sub.add_parser("fetch-water",
                        help="download OSM waterways/water bodies (Overpass) as JSON")
    fw.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fw.add_argument("--out", required=True, help="output JSON path")

    fr = sub.add_parser("fetch-roads",
                        help="download OSM highway ways (Overpass) as JSON")
    fr.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fr.add_argument("--out", required=True, help="output JSON path")

    fl = sub.add_parser("fetch-landuse",
                        help="download OSM land-use polygons + rail (Overpass) as JSON")
    fl.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fl.add_argument("--out", required=True, help="output JSON path")

    fb = sub.add_parser("fetch-buildings",
                        help="download OSM building footprints (Overpass) as JSON")
    fb.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fb.add_argument("--out", required=True, help="output JSON path")

    f = sub.add_parser("fixture", help="write a known-pattern test tile")
    f.add_argument("--out", required=True, help="output .gwt path")

    p = sub.add_parser("preview", help="render dataset layers to PNGs")
    p.add_argument("--dataset", required=True, help="dataset dir")
    p.add_argument("--out", default=None, help="output dir (default <dataset>/debug)")

    args = parser.parse_args(argv)

    if args.command == "fetch":
        from .fetch import fetch_dem

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        for p in fetch_dem(min_lon, min_lat, max_lon, max_lat, args.out, args.dataset):
            print(p)
        return 0

    if args.command == "fetch-water":
        from .hydro import fetch_water

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_water(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "fetch-roads":
        from .roads import fetch_roads

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_roads(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "fetch-landuse":
        from .landuse import fetch_landuse

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_landuse(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "fetch-buildings":
        from .buildings import fetch_buildings

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_buildings(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "build":
        from .build import build_dataset
        from .transform import GeoTransform, Projection

        projection = None
        anchor_east = anchor_north = 0.0
        if args.anchor:
            lat, lon = (float(s) for s in args.anchor.split(","))
            projection = Projection(args.crs, lat, lon)
            anchor_east, anchor_north = projection.anchor_east, projection.anchor_north

        transform = GeoTransform(
            origin_x=args.origin_x, origin_z=args.origin_z,
            horizontal_meters_per_block=args.hscale,
            vertical_meters_per_block=args.vscale,
            datum_elevation_meters=args.datum_elevation,
            datum_y=args.datum_y,
        )

        if args.source == "dem":
            if not args.dem:
                parser.error("--source dem requires --dem <raster> [<raster> ...]")
            if projection is None:
                parser.error("--source dem requires --anchor lat,lon")
            from .dem import DemSource

            source = DemSource(
                args.dem,
                geo_crs=args.crs,
                anchor_east=anchor_east,
                anchor_north=anchor_north,
                half_extent_m=args.radius,
                ramp_m=args.ramp,
            )
        else:
            from .sources import SyntheticSource

            source = SyntheticSource(args.base_elevation, args.radius_full, args.radius_edge)

        hydro = None
        roads = None
        landuse = None
        buildings = None
        if args.hydro or args.roads or args.landuse or args.buildings:
            if projection is None:
                parser.error("--hydro/--roads/--landuse/--buildings require "
                             "--anchor lat,lon")
            from .tileio import TILE_SIZE
            if args.hydro:
                from .hydro import HydroSource
                hydro = HydroSource(args.hydro, projection,
                                    extent_m=args.radius + TILE_SIZE)
            if args.roads:
                from .roads import RoadSource
                roads = RoadSource(args.roads, projection,
                                   extent_m=args.radius + TILE_SIZE)
            if args.landuse:
                from .landuse import LanduseSource
                landuse = LanduseSource(args.landuse, projection,
                                        extent_m=args.radius + TILE_SIZE)
            if args.buildings:
                from .buildings import BuildingSource
                buildings = BuildingSource(args.buildings, projection,
                                           extent_m=args.radius + TILE_SIZE)

        out = build_dataset(args.out, name=args.name, transform=transform,
                            source=source, projection=projection,
                            hydro=hydro, roads=roads,
                            landuse=landuse, buildings=buildings)
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
