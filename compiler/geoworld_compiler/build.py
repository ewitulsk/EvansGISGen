"""Dataset build: sample sources per block column, emit .gwt tiles + manifest.

The emit loop is vectorized: every source exposes a `*_grid(east, north)`
sampler returning an (H, W) array for the tile's cell-center coordinate
vectors, so a tile costs a handful of numpy ops instead of 65k Python-level
per-cell queries. Tiles are independent, so `jobs` threads compile them
concurrently — the source grids are read-only memmaps and numpy/zlib both
release the GIL, so threads scale well.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Protocol

import numpy as np

from .manifest import write_manifest
from .tileio import (NODATA, TILE_SIZE, pack_bitset, pack_elevation, pack_u16,
                     tile_filename, write_tile)
from .transform import GeoTransform, Projection


class Source(Protocol):
    """Elevation/influence provider in dataset geo space (see sources.py).

    The `*_grid` methods take 1-D cell-center coordinate vectors `east`
    (W,) and `north` (H,) in geo meters and return (H, W) arrays —
    the vectorized twins of the scalar per-point accessors.
    """

    def elevation_m(self, east: float, north: float) -> float | None: ...
    def influence(self, east: float, north: float) -> float: ...
    def elevation_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 elevation meters; NaN = no data."""
        ...
    def influence_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 influence weights 0..1."""
        ...
    def bounds(self) -> tuple[float, float, float, float]: ...
    def may_claim(self, rect: tuple[float, float, float, float]) -> bool: ...


class Hydro(Protocol):
    """Channel-depth provider in geo space (see hydro.py)."""

    def depth_m(self, east: float, north: float) -> float: ...
    def depth_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 channel depth meters; 0 = dry."""
        ...


class Roads(Protocol):
    """Road surface-class provider in geo space (see roads.py)."""

    def road_class(self, east: float, north: float) -> int: ...
    def deck_m(self, east: float, north: float) -> float: ...
    def deck_class(self, east: float, north: float) -> int: ...
    def road_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def deck_h_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray:
        """(H, W) float64 deck-top meters; <=0 = no deck."""
        ...
    def deck_c_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...


class Landuse(Protocol):
    """Surface-class provider in geo space (see landuse.py)."""

    def surface_class(self, east: float, north: float) -> int: ...
    def surface_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...


class Buildings(Protocol):
    """Building footprint provider in geo space (see buildings.py)."""

    def building_class(self, east: float, north: float) -> int: ...
    def building_levels(self, east: float, north: float) -> int: ...
    def building_id(self, east: float, north: float) -> int: ...
    def building_roof(self, east: float, north: float) -> int: ...
    def business_at(self, east: float, north: float) -> int: ...
    def interior_zone(self, east: float, north: float) -> int: ...
    def building_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def levels_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def id_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def roof_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def business_grid(self, east: np.ndarray, north: np.ndarray) -> np.ndarray: ...
    def interior_zone_grid(self, east: np.ndarray,
                           north: np.ndarray) -> np.ndarray: ...


def _tile_rect(transform: GeoTransform, tx: int, tz: int):
    """Geo rect covered by tile (tx, tz) — the may_claim probe rect."""
    return (transform.east_meters(tx * TILE_SIZE),
            transform.north_meters(tz * TILE_SIZE + TILE_SIZE),
            transform.east_meters(tx * TILE_SIZE + TILE_SIZE),
            transform.north_meters(tz * TILE_SIZE))


def _block_y(v: np.ndarray, transform: GeoTransform) -> np.ndarray:
    """Vectorized GeoTransform.block_y — same float64 op order."""
    return np.floor((v - transform.datum_elevation_meters)
                    / transform.vertical_meters_per_block + 0.5) \
        + transform.datum_y


def _emit_tile(coord: tuple[int, int], *, transform: GeoTransform,
               source: Source, hydro: Hydro | None, roads: Roads | None,
               landuse: Landuse | None, buildings: Buildings | None,
               tiles_dir: Path) -> tuple[bool, bool, bool, bool, bool, bool,
                                        bool, bool]:
    """Compile and write one tile. Returns
    (wrote, any_water, any_road, any_deck, any_surface, any_building,
    any_business, any_interior)."""
    tx, tz = coord
    xs = tx * TILE_SIZE + np.arange(TILE_SIZE)
    zs = tz * TILE_SIZE + np.arange(TILE_SIZE)
    # Cell-center geo coords — same expressions as east_meters/north_meters.
    east = (xs - transform.origin_x) * transform.horizontal_meters_per_block
    north = (transform.origin_z - zs) * transform.horizontal_meters_per_block

    empty = (False,) * 8

    w = source.influence_grid(east, north)
    live = w > 0.0
    if not live.any():
        return empty
    elev = source.elevation_grid(east, north)
    valid = live & ~np.isnan(elev)
    if not valid.any():
        # Every claimable column has no source data -> pure vanilla tile.
        return empty

    surf_y = _block_y(elev, transform)
    elevation = np.where(valid, surf_y, NODATA)

    any_water = False
    water_depth = None
    if hydro is not None:
        depth = hydro.depth_grid(east, north)
        bed_y = _block_y(elev - depth, transform)
        dblk = surf_y - bed_y
        carve = valid & (depth > 0.0) & (dblk > 0.0)
        if carve.any():
            # Bake the riverbed into elevation: water surface - channel
            # depth = bed; the runtime fills water bed+1 .. bed+depth.
            any_water = True
            elevation = np.where(carve, bed_y, elevation)
            water_depth = np.where(carve, np.minimum(255.0, dblk), 0.0)
            water_depth = water_depth.astype(np.uint8)

    any_road = any_deck = False
    road = roadz = roade = None
    if roads is not None:
        road = np.where(valid, roads.road_grid(east, north), 0)
        road = road.astype(np.uint8)
        any_road = bool((road != 0).any())
        dh = roads.deck_h_grid(east, north)
        deck = valid & (dh > 0.0)
        if deck.any():
            any_deck = True
            roadz = np.where(deck, _block_y(dh, transform), NODATA)
            roade = np.where(deck, roads.deck_c_grid(east, north), 0)
            roade = roade.astype(np.uint8)

    any_surface = False
    surface = None
    if landuse is not None:
        surface = np.where(valid, landuse.surface_grid(east, north), 0)
        surface = surface.astype(np.uint8)
        any_surface = bool((surface != 0).any())

    any_building = any_business = any_interior = False
    building = b_levels = b_id = b_roof = business = interior = None
    if buildings is not None:
        bc = buildings.building_grid(east, north)
        bmask = valid & (bc != 0)
        if bmask.any():
            any_building = True
            building = np.where(bmask, bc, 0).astype(np.uint8)
            b_levels = np.where(bmask, buildings.levels_grid(east, north), 0)
            b_levels = b_levels.astype(np.uint8)
            b_id = np.where(bmask, buildings.id_grid(east, north), 0)
            b_id = b_id.astype(np.uint16)
            b_roof = np.where(bmask, buildings.roof_grid(east, north), NODATA)
            b_roof = b_roof.astype(np.int16)
            business = np.where(bmask, buildings.business_grid(east, north), 0)
            business = business.astype(np.uint8)
            any_business = bool((business != 0).any())
            interior = np.where(bmask,
                                buildings.interior_zone_grid(east, north), 0)
            interior = interior.astype(np.uint8)
            any_interior = bool((interior != 0).any())

    influence = np.where(
        valid, np.clip(np.floor(w * 255.0 + 0.5), 0.0, 255.0), 0.0)
    influence = influence.astype(np.uint8)

    layers = {"elevation": pack_elevation(elevation.astype(np.int16)),
              "influence": influence.tobytes()}
    if any_surface:
        layers["surface"] = surface.tobytes()
    if any_water:
        layers["water"] = pack_bitset(water_depth != 0)
        layers["water_depth"] = water_depth.tobytes()
    if any_road:
        layers["road"] = road.tobytes()
    if any_deck:
        layers["roadz"] = pack_elevation(roadz.astype(np.int16))
        layers["roade"] = roade.tobytes()
    if any_building:
        layers["building"] = building.tobytes()
        layers["building_levels"] = b_levels.tobytes()
        layers["building_id"] = pack_u16(b_id)
        layers["building_roof"] = pack_elevation(b_roof)
    if any_business:
        layers["business"] = business.tobytes()
    if any_interior:
        layers["interior"] = interior.tobytes()
    write_tile(tiles_dir / tile_filename(tx, tz), tx, tz, layers)
    return (True, any_water, any_road, any_deck, any_surface, any_building,
            any_business, any_interior)


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
    jobs: int = 1,
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

    # Whole-tile reject up front: a tile the influence field cannot claim is
    # pure vanilla — skip it without any per-cell work. (Bounds-spanning
    # datasets like Beatrice+Lincoln are mostly empty space between claimed
    # regions.)
    coords = [(tx, tz)
              for tx in range(t_min_x, t_max_x + 1)
              for tz in range(t_min_z, t_max_z + 1)
              if source.may_claim(_tile_rect(transform, tx, tz))]

    written: list[tuple[int, int]] = []
    any_water = any_road = any_deck = any_surface = any_building = False
    any_business = any_interior = False

    emit = partial(_emit_tile, transform=transform, source=source,
                   hydro=hydro, roads=roads, landuse=landuse,
                   buildings=buildings, tiles_dir=tiles_dir)
    if jobs > 1:
        pool = ThreadPoolExecutor(max_workers=jobs)
        results = pool.map(emit, coords)
    else:
        results = map(emit, coords)
    try:
        for (tx, tz), res in zip(coords, results):
            wrote, tw, tr, td, ts, tb, tbus, tint = res
            if wrote:
                written.append((tx, tz))
                any_water |= tw
                any_road |= tr
                any_deck |= td
                any_surface |= ts
                any_building |= tb
                any_business |= tbus
                any_interior |= tint
            print(f"  tile {tx:+d},{tz:+d}: "
                  f"{'written' if wrote else 'skipped'}", flush=True)
    finally:
        if jobs > 1:
            pool.shutdown()

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
