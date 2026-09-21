"""CLI: python -m geoworld_compiler <command>

Commands:
    build    Compile a dataset (currently: synthetic terrain + influence).
    fixture  Write a known-pattern .gwt tile for cross-language format tests.
    preview  Render dataset layers to PNGs (requires Pillow).
"""

from __future__ import annotations

import argparse
import json
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
    b.add_argument("--ms-buildings", default=None,
                   help="Microsoft GlobalML footprint JSON from "
                        "fetch-buildings-ms; merged under OSM as GENERIC")
    b.add_argument("--pois", default=None,
                   help="OSM POI node JSON from fetch-pois; reclassifies "
                        "weak footprints by contained POI (Phase 16)")
    b.add_argument("--reclassify-outbuildings", action="store_true",
                   help="reclassify small ML-only footprints near houses "
                        "as garages/sheds (Phase 17)")
    b.add_argument("--bounds", default=None,
                   help="'eMin,nMin,eMax,nMax' dataset rect in geo meters "
                        "(default: +-radius square)")
    b.add_argument("--corridor", action="store_true",
                   help="add a CorridorRamp influence strip along the major "
                        "highways (motorway/trunk) in --roads — "
                        "the US-77 corridor (Phase 10)")
    b.add_argument("--corridor-full", type=float, default=500.0,
                   help="corridor: full-influence half-width (m)")
    b.add_argument("--corridor-ramp", type=float, default=1500.0,
                   help="corridor: blend-to-vanilla width beyond full (m)")
    b.add_argument("--region", action="append", default=None, metavar="R",
                   help="extra geographic region 'eMin,nMin,eMax,nMax' in "
                        "geo meters (repeatable) — a second city such as "
                        "Lincoln (Phase 11); influence fades over --ramp "
                        "inside the rect edge")

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

    fm = sub.add_parser("fetch-buildings-ms",
                        help="download Microsoft GlobalML building footprints "
                             "for the quadkey tiles covering the bbox")
    fm.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fm.add_argument("--out", required=True, help="output JSON path")

    fp2 = sub.add_parser("fetch-pois",
                         help="download OSM POI nodes (amenity/shop/office/"
                              "tourism/leisure/healthcare/craft) as JSON")
    fp2.add_argument("--bbox", required=True,
                     help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fp2.add_argument("--out", required=True, help="output JSON path")

    fp = sub.add_parser("fetch-parcels",
                        help="paged bbox query against an ArcGIS "
                             "parcel layer (FeatureServer or MapServer)")
    fp.add_argument("--service", required=True,
                    help="ArcGIS service root URL, e.g. .../MapServer")
    fp.add_argument("--layer", type=int, required=True)
    fp.add_argument("--bbox", required=True,
                    help="'minLon,minLat,maxLon,maxLat' in WGS84 degrees")
    fp.add_argument("--out", required=True, help="output GeoJSON path")

    cp = sub.add_parser("compile-parcels",
                        help="merge fetched parcel GeoJSONs into a dataset "
                             "parcels.json (rings in geo meters + gazetteer)")
    cp.add_argument("--in", dest="inputs", nargs="+", required=True,
                    help="GeoJSON paths from fetch-parcels")
    cp.add_argument("--osm-addr", nargs="+", default=None,
                    help="OSM JSONs to mine for addr:* gazetteer entries")
    cp.add_argument("--anchor", required=True,
                    help="'lat,lon' anchoring the geo origin in the CRS")
    cp.add_argument("--crs", default="EPSG:32614")
    cp.add_argument("--id-field", default="PARCELID")
    cp.add_argument("--address-field", default="SITEADDRESS",
                    help="situs address property; '' when the layer has none")
    cp.add_argument("--id-prefix", default="",
                    help="namespace prefix for parcel ids (e.g. 'gage_')")
    cp.add_argument("--append", action="store_true",
                    help="merge into an existing parcels.json (multi-county)")
    cp.add_argument("--out", required=True, help="output parcels.json path")

    ep = sub.add_parser("export-parcel",
                        help="export a lot outline .nbt (parcel boundary + "
                             "building footprint) for Structure Lab handoff")
    ep.add_argument("--parcels", required=True, help="dataset parcels.json")
    ep.add_argument("--query", required=True,
                    help="'east,north' geo meters or an address string")
    ep.add_argument("--buildings", default=None,
                    help="optional OSM buildings GeoJSON for footprint edges")
    ep.add_argument("--anchor", default=None,
                    help="'lat,lon' (required with --buildings)")
    ep.add_argument("--crs", default="EPSG:32614")
    ep.add_argument("--out", required=True, help="output .nbt path")

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

    if args.command == "fetch-buildings-ms":
        from .buildings import fetch_buildings_ms

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_buildings_ms(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "fetch-pois":
        from .buildings import fetch_pois

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_pois(min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "fetch-parcels":
        from .parcels import fetch_parcels

        min_lon, min_lat, max_lon, max_lat = (
            float(s) for s in args.bbox.split(","))
        print(fetch_parcels(args.service, args.layer,
                            min_lon, min_lat, max_lon, max_lat, args.out))
        return 0

    if args.command == "compile-parcels":
        from .parcels import add_osm_addresses, compile_parcels
        from .transform import Projection

        lat, lon = (float(s) for s in args.anchor.split(","))
        projection = Projection(args.crs, lat, lon)
        out = compile_parcels(args.inputs, projection, args.out,
                              id_field=args.id_field,
                              address_field=args.address_field,
                              id_prefix=args.id_prefix,
                              append=args.append)
        if args.osm_addr:
            n = add_osm_addresses(args.osm_addr, projection, out)
            print(f"added {n} OSM address entries")
        return 0

    if args.command == "export-parcel":
        from .parcels import export_parcel
        from .transform import Projection

        projection = None
        if args.buildings:
            if not args.anchor:
                parser.error("--buildings requires --anchor lat,lon")
            lat, lon = (float(s) for s in args.anchor.split(","))
            projection = Projection(args.crs, lat, lon)
        print(export_parcel(args.parcels, args.query, args.out,
                            buildings_geojson=args.buildings,
                            projection=projection))
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

        # Dataset rect in geo meters; default keeps the legacy square.
        if args.bounds:
            bounds = tuple(float(s) for s in args.bounds.split(","))
        else:
            bounds = (-args.radius, -args.radius, args.radius, args.radius)

        if args.source == "dem":
            if not args.dem:
                parser.error("--source dem requires --dem <raster> [<raster> ...]")
            if projection is None:
                parser.error("--source dem requires --anchor lat,lon")
            from .dem import DemSource

            field = None
            if args.corridor or args.region:
                from .influence import (BoxRamp, RectRamp, corridor_field,
                                        combine_max)
                fields = [BoxRamp(args.radius, args.ramp)]
                if args.corridor:
                    if not args.roads:
                        parser.error("--corridor requires --roads")
                    from .roads import clip_polylines, corridor_polylines

                    # Clip the corridor one reach inside the dataset rect so
                    # the ramp fully decays before coverage ends — a polyline
                    # cut at the boundary would leave w=1 right at the edge.
                    reach = args.corridor_full + args.corridor_ramp
                    clip = (bounds[0] + reach, bounds[1] + reach,
                            bounds[2] - reach, bounds[3] - reach)
                    lines = clip_polylines(
                        corridor_polylines(args.roads, projection), clip)
                    fields.append(corridor_field(
                        lines, bounds, args.corridor_full,
                        args.corridor_ramp))
                    print(f"corridor: {len(lines)} major-road ways")
                for spec in args.region or []:
                    rect = tuple(float(s) for s in spec.split(","))
                    if len(rect) != 4:
                        parser.error("--region expects 'eMin,nMin,eMax,nMax'")
                    fields.append(RectRamp(rect, args.ramp))
                    print(f"region: {rect}")
                field = combine_max(*fields)

            source = DemSource(
                args.dem,
                geo_crs=args.crs,
                anchor_east=anchor_east,
                anchor_north=anchor_north,
                bounds_m=bounds,
                ramp_m=args.ramp,
                influence=field,
            )
        else:
            from .sources import SyntheticSource

            source = SyntheticSource(args.base_elevation, args.radius_full, args.radius_edge)

        hydro = None
        roads = None
        landuse = None
        buildings = None
        if (args.hydro or args.roads or args.landuse or args.buildings
                or args.ms_buildings):
            if projection is None:
                parser.error("--hydro/--roads/--landuse/--buildings/"
                             "--ms-buildings require --anchor lat,lon")
            from .tileio import TILE_SIZE
            # Layer grids pad one tile past the dataset rect so edge cells
            # of boundary tiles still have real classifications to read.
            sb = (bounds[0] - TILE_SIZE, bounds[1] - TILE_SIZE,
                  bounds[2] + TILE_SIZE, bounds[3] + TILE_SIZE)
            if args.hydro:
                from .hydro import HydroSource
                hydro = HydroSource(args.hydro, projection, bounds=sb)
            if args.roads:
                from .roads import RoadSource, parse_rail_lines
                # Deck solving needs the bare-earth DEM (bridge floors),
                # water depths (river crossings) and rail centerlines
                # (under-features mined from the landuse fetch).
                rail = (parse_rail_lines(
                            json.loads(Path(args.landuse).read_text()),
                            projection)
                        if args.landuse else None)
                roads = RoadSource(args.roads, projection, bounds=sb,
                                   elev_m=getattr(source, "elevation_m", None),
                                   hydro=hydro, rail_lines=rail)
            if args.landuse:
                from .landuse import LanduseSource
                landuse = LanduseSource(args.landuse, projection, bounds=sb)
            if args.buildings or args.ms_buildings:
                from .buildings import BuildingSource
                from .landuse import SURFACE_PARKING

                def _access(east, north):
                    # Entrance solving: roads and mapped parking lots are
                    # both "street side" for a known business.
                    if roads is not None and roads.road_class(east, north):
                        return True
                    return (landuse is not None
                            and landuse.surface_class(east, north)
                            == SURFACE_PARKING)

                buildings = BuildingSource(
                    args.buildings, projection, bounds=sb,
                    ms_json_path=args.ms_buildings,
                    pois_json_path=args.pois,
                    reclassify_outbuildings=args.reclassify_outbuildings,
                    elev_m=getattr(source, "elevation_m", None),
                    transform=transform,
                    access=_access if (roads is not None or landuse is not None)
                    else None)

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
