"""Dataset build: sample sources per block column, emit .gwt tiles + manifest."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Protocol

from .manifest import write_manifest
from .tileio import (NODATA, TILE_SIZE, pack_bitset, pack_elevation, pack_u16,
                     tile_filename, write_tile)
from .transform import GeoTransform, Projection


class Source(Protocol):
    """Elevation/influence provider in dataset geo space (see sources.py)."""

    def elevation_m(self, east: float, north: float) -> float | None: ...
    def influence(self, east: float, north: float) -> float: ...
    def bounds(self) -> tuple[float, float, float, float]: ...
    def may_claim(self, rect: tuple[float, float, float, float]) -> bool: ...


class Hydro(Protocol):
    """Channel-depth provider in geo space (see hydro.py)."""

    def depth_m(self, east: float, north: float) -> float: ...


class Roads(Protocol):
    """Road surface-class provider in geo space (see roads.py)."""

    def road_class(self, east: float, north: float) -> int: ...
    def deck_m(self, east: float, north: float) -> float: ...
    def deck_class(self, east: float, north: float) -> int: ...


class Landuse(Protocol):
    """Surface-class provider in geo space (see landuse.py)."""

    def surface_class(self, east: float, north: float) -> int: ...


class Buildings(Protocol):
    """Building footprint provider in geo space (see buildings.py)."""

    def building_class(self, east: float, north: float) -> int: ...
    def building_levels(self, east: float, north: float) -> int: ...
    def building_id(self, east: float, north: float) -> int: ...
    def building_roof(self, east: float, north: float) -> int: ...
    def business_at(self, east: float, north: float) -> int: ...
    def interior_zone(self, east: float, north: float) -> int: ...


def build_dataset(
    out_dir: str | Path,
    *,
    name: str,
    transform: GeoTransform,
    source: Source,
    projection: Projection | None = None,
    hydro: Hydro | None = None,
    roads: Roads | None = None,
    landuse: Landuse | None = None,
    buildings: Buildings | None = None,
) -> Path:
    """Emit a .geoworld dataset directory. Returns the dataset dir."""
    out = Path(out_dir)
    tiles_dir = out / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    # Tile coverage: the source's geo-space bounds rect, in tiles.
    e0, n0, e1, n1 = source.bounds()
    bx0 = transform.block_x(e0)
    bx1 = transform.block_x(e1)
    bz0 = transform.block_z(n1)  # +north maps to -z
    bz1 = transform.block_z(n0)
    t_min_x = math.floor(bx0 / TILE_SIZE)
    t_max_x = math.floor(bx1 / TILE_SIZE)
    t_min_z = math.floor(bz0 / TILE_SIZE)
    t_max_z = math.floor(bz1 / TILE_SIZE)

    written: list[tuple[int, int]] = []
    any_water = False
    any_road = False
    any_deck = False
    any_surface = False
    any_building = False
    any_business = False
    any_interior = False
    n = TILE_SIZE * TILE_SIZE
    for tx in range(t_min_x, t_max_x + 1):
        for tz in range(t_min_z, t_max_z + 1):
            # Whole-tile reject: a tile the influence field cannot claim is
            # pure vanilla — skip it without 65k per-cell field queries.
            # (Bounds-spanning datasets like Beatrice+Lincoln are mostly
            # empty space between claimed regions.)
            tile_rect = (transform.east_meters(tx * TILE_SIZE),
                         transform.north_meters(tz * TILE_SIZE + TILE_SIZE),
                         transform.east_meters(tx * TILE_SIZE + TILE_SIZE),
                         transform.north_meters(tz * TILE_SIZE))
            if not source.may_claim(tile_rect):
                continue
            elevation = [NODATA] * n
            influence = bytearray(n)
            surface = bytearray(n)
            water_depth = bytearray(n)
            road = bytearray(n)
            roadz = [NODATA] * n
            roade = bytearray(n)
            building = bytearray(n)
            b_levels = bytearray(n)
            b_id = [0] * n
            b_roof = [NODATA] * n
            business = bytearray(n)
            interior = bytearray(n)
            any_influence = False
            any_tile_water = False
            any_tile_road = False
            any_tile_deck = False
            any_tile_surface = False
            any_tile_building = False
            any_tile_business = False
            any_tile_interior = False
            for lz in range(TILE_SIZE):
                bz = tz * TILE_SIZE + lz
                north = transform.north_meters(bz)
                for lx in range(TILE_SIZE):
                    bx = tx * TILE_SIZE + lx
                    east = transform.east_meters(bx)
                    w = source.influence(east, north)
                    if w <= 0.0:
                        continue  # vanilla column — no layers to paint
                    elev = source.elevation_m(east, north)
                    if elev is None:
                        # No-data columns are pure vanilla regardless of influence.
                        continue
                    i = lz * TILE_SIZE + lx
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
                        dm = roads.deck_m(east, north)
                        if dm > 0.0:
                            roadz[i] = transform.block_y(dm)
                            roade[i] = roads.deck_class(east, north)
                            any_tile_deck = True
                    if landuse is not None:
                        sc = landuse.surface_class(east, north)
                        if sc:
                            surface[i] = sc
                            any_tile_surface = True
                    if buildings is not None:
                        bc = buildings.building_class(east, north)
                        if bc:
                            building[i] = bc
                            b_levels[i] = buildings.building_levels(east, north)
                            b_id[i] = buildings.building_id(east, north)
                            b_roof[i] = buildings.building_roof(east, north)
                            any_tile_building = True
                            bus = buildings.business_at(east, north)
                            if bus:
                                business[i] = bus
                                any_tile_business = True
                            zone = buildings.interior_zone(east, north)
                            if zone:
                                interior[i] = zone
                                any_tile_interior = True
                    influence[i] = min(255, max(0, int(w * 255.0 + 0.5)))
                    any_influence = True
            if any_influence:
                layers = {"elevation": pack_elevation(elevation),
                          "influence": bytes(influence)}
                if any_tile_surface:
                    layers["surface"] = bytes(surface)
                if any_tile_water:
                    layers["water"] = pack_bitset([1 if d else 0 for d in water_depth])
                    layers["water_depth"] = bytes(water_depth)
                if any_tile_road:
                    layers["road"] = bytes(road)
                if any_tile_deck:
                    layers["roadz"] = pack_elevation(roadz)
                    layers["roade"] = bytes(roade)
                if any_tile_building:
                    layers["building"] = bytes(building)
                    layers["building_levels"] = bytes(b_levels)
                    layers["building_id"] = pack_u16(b_id)
                    layers["building_roof"] = pack_elevation(b_roof)
                if any_tile_business:
                    layers["business"] = bytes(business)
                if any_tile_interior:
                    layers["interior"] = bytes(interior)
                write_tile(tiles_dir / tile_filename(tx, tz), tx, tz, layers)
                written.append((tx, tz))
                any_water = any_water or any_tile_water
                any_road = any_road or any_tile_road
                any_deck = any_deck or any_tile_deck
                any_surface = any_surface or any_tile_surface
                any_building = any_building or any_tile_building
                any_business = any_business or any_tile_business
                any_interior = any_interior or any_tile_interior
            print(f"  tile {tx:+d},{tz:+d}: {'written' if any_influence else 'skipped'}",
                  flush=True)

    layers = (["elevation", "influence"]
              + (["surface"] if any_surface else [])
              + (["water", "water_depth"] if any_water else [])
              + (["road"] if any_road else [])
              + (["roadz", "roade"] if any_deck else [])
              + (["building", "building_levels",
                  "building_id", "building_roof"] if any_building else [])
              + (["business"] if any_business else [])
              + (["interior"] if any_interior else []))
    write_manifest(out, name=name, transform=transform, projection=projection,
                   layers=layers, tiles=written)
    # Street furniture sidecar: sign placements mined from the road ways
    # (intersection blades + stop/yield nodes) for the runtime SignIndex.
    signs = getattr(roads, "signs", None) if roads is not None else None
    signs = list(signs) if signs else []
    if buildings is not None:
        # Known-business pylon signs ride the same sidecar — the runtime
        # SignPlacer branches on `type` (Phase 19).
        signs += getattr(buildings, "business_signs", lambda: [])()
        doc = getattr(buildings, "businesses_doc", lambda: None)()
        if doc and doc.get("instances"):
            import json
            (out / "businesses.json").write_text(json.dumps(doc))
            print(f"businesses: {len(doc['instances'])} known instances")
            # Phase 21: interior modules — v1 NBTs generated here, plus a
            # placements sidecar the runtime stamps chunk-clamped.
            from .modules import module_placements, modules_doc, write_module_nbts
            placements = module_placements(buildings)
            if placements:
                write_module_nbts(out)
                (out / "modules.json").write_text(
                    json.dumps(modules_doc(placements)))
                print(f"modules: {len(placements)} placements")
    if signs:
        import json
        (out / "signs.json").write_text(json.dumps({"signs": signs}))
        print(f"signs: {len(signs)} placements")
    return out
