# GeoWorld

A NeoForge 1.21.1 mod + offline GIS compiler that will generate Minecraft
terrain from real-world data (Beatrice, NE → US-77 → Lincoln, NE as the first
dataset). See [PLAN.md](PLAN.md) for the full implementation plan.

## Repository layout

```
├── mod/        # NeoForge mod (Java, ModDevGradle subproject)
├── compiler/   # offline GIS -> .geoworld compiler (Python)
├── datasets/   # compiled .geoworld datasets
└── format/     # dataset format spec
```

## Current status: Phase 11

- `GeoChunkGenerator` wraps a vanilla `ChunkGenerator` (`geoworld:geoworld`)
  and delegates everything to it, then deforms terrain from geographic data:
  `lerp(vanilla, dataset_elevation, influence)` per column, shifting the whole
  column so strata/caves move with the surface — then seals the top
  `OVERBURDEN_M` (16 m, scaled by influence) into solid dirt/stone so caves
  and aquifers can't daylight through roads or floors. Deeper caves are left
  intact and still generate under claimed ground. `applyCarvers` is skipped
  on claimed chunks (no canyons slashing streets) and `createStructures`
  invalidates any vanilla structure start whose bounding box touches
  claimed cells — villages and shipwrecks never appear inside the
  geography. `getBaseHeight`, `getBaseColumn`, and `getFirstOccupiedHeight`
  report the adjusted terrain.
- `GeoTransform` anchors projected meter offsets to block coordinates
  (`+east -> +x`, `+north -> -z`), with independent horizontal/vertical scale
  and a configurable vertical datum.
- `config/geoworld.json` picks the dataset (`dataset_path`) and the fallback
  origin/scale. The dataset manifest supplies the authoritative transform.
- `GeoDataset`/`GeoTile` load `.gwt` binary tiles on demand (guava cache,
  immutable) — `dataset.tileAt(x, z)` + `tile.elevation(lx, lz)` are plain
  array lookups; no GIS libraries at runtime. `-32768` elevation cells are
  NODATA and fall back to vanilla.
- Influence is a generic precomputed `ScalarField`
  (`dataset.influenceField()`): the compiler bakes `0=vanilla / 1=geographic`
  weights into tiles from composable sources (`BoxRamp`, `DiscRamp`,
  `CorridorRamp` polylines, `combine_max`/`combine_sum` in `influence.py`),
  so cities and corridors are never special to worldgen — Phase 10's US-77
  corridor just adds another source. The survey's `blend_matches_field`
  assertions verify generated terrain follows `lerp(vanilla, geo, w)`
  across the boundary transect.
- Hydrology (Phase 5): `fetch-water` pulls OSM waterways/water bodies via
  Overpass; `hydro.py` rasterizes them into a per-cell channel-depth field
  (per-class widths, `smootherstep` bank profile via distance transforms).
  The build bakes the riverbed into `elevation` and stores `water_depth`
  (u8 blocks); the generator fills `bed+1..bed+depth` with water, so the
  Big Blue River is a real channel — not a DEM artifact. Vanilla surface
  water (ponds, seas) above the deformed surface is stripped; aquifer/cave
  water below it survives.
- Roads (Phase 6): `fetch-roads` pulls OSM `highway` ways via Overpass;
  `roads.py` rasterizes them into a per-cell road-class field — each highway
  class contributes a cross-section (travel lanes, curb, sidewalk or shoulder,
  track, center marking for multi-lane roads) painted by priority so
  intersections resolve deterministically. The runtime paves the top blocks
  of road columns before vanilla decoration (so vegetation can't plant on
  pavement) and strips decoration output — snow cover, intruding tree
  trunks/canopies — back off the surface afterwards. Where a road crosses
  water, the cell becomes a deck: a road slab one block above the waterline
  (bed + depth + 1 ≈ road grade) with the channel kept underneath, mirrored
  in `getBaseColumn`/height queries.
- Land use (Phase 7): `fetch-landuse` pulls OSM `landuse`/`leisure`/
  `natural`/`railway`/`aeroway` polygons; `landuse.py` rasterizes them into
  a per-cell surface class (`0x04` layer: residential, farmland, forest,
  commercial, industrial, parking, park, railway, ...). The runtime maps
  semantic classes to blocks through a theme palette — applied *before*
  road paving so streets stay authoritative — and plants deterministic
  trees on `FOREST` cells. A fixed-plains biome source replaces vanilla
  biome noise while a dataset is loaded, killing seed-random jungles and
  frozen rivers (biomes are resolved lazily because the overworld's
  parameter list is unbound during preset decode).
- Buildings (Phase 8): two sources merged at the raster level.
  `fetch-buildings` pulls OSM `building` footprints (real classes);
  `fetch-buildings-ms` pulls Microsoft GlobalML footprints for the
  quadkey tiles covering the bbox — OSM coverage is sparse in small-town
  Nebraska (1,665 footprints), so ML detections fill in the rest (7,916
  footprints, most with height estimates). `buildings.py` paints MS
  footprints first at the lowest priority as GENERIC, then OSM classes
  overwrite them wherever both cover a cell. Output: a building class
  layer (`0x40`: residential/commercial/industrial/civic/outbuilding/
  generic) plus `building_levels` (`0x80`: OSM `building:levels`, MS
  `height`/3, or a per-class default). The runtime extrudes deterministic
  shells — floor at terrain, walls to `terrain + levels*4`, a window band
  on the second level, flat roof — respecting roads and water, and clearing
  vegetation/canopy overhang above roofs.
- Landmarks (Phase 9): `landmarks.json` in the dataset declares curated
  structures (id, `landmarks/*.nbt` template, geo position, template-space
  anchor, rotation, terrain policy, optional `replaces_building`).
  `LandmarkIndex` loads each `.nbt` via vanilla `StructureTemplate`,
  computes the transformed world bounding box, and indexes it into every
  intersecting chunk; `placeChunk` renders each chunk's slice with a
  clamped `StructurePlaceSettings` bounding box (the same slicing vanilla
  uses), so multi-chunk landmarks assemble deterministically. Terrain
  policies: `NONE`, `LEVEL_FOUNDATION` (fill dips + cut above the ground
  plane), `CUT_AND_FILL` (fill + trim terrain towering over the roof),
  `FOLLOW_TERRAIN` (extend columns to ground). The landmark's box also
  suppresses procedural building shells underneath it. A demo house sits
  at geo (200, -60) = block (200, 60), east of downtown.
- Corridor (Phase 10): dataset bounds are now a rect
  (`--bounds eMin,nMin,eMax,nMax`) instead of a square, and `--corridor`
  composes `max(BoxRamp, corridor_field(US-77))`: trunk/motorway ways
  from the roads JSON become centerline polylines, rasterized into an
  8 m weight grid via distance transform (`RasterField`) — full
  geographic control within `--corridor-full` meters of the road,
  smootherstep to vanilla over `--corridor-ramp`. Corridor polylines are
  clipped one reach inside the dataset bounds so the ramp fully decays
  before coverage ends — no seam where the real highway continues past
  the data.
- Lincoln (Phase 11): a second city region via `--region e,n,e,n`
  (repeatable → `RectRamp` fields combined with `combine_max`), plus the
  corridor extended north. The dataset bounds grew to ~17 × 80 km
  (Beatrice + US-77 through Cortland/Princeton + Lincoln metro), so two
  scale changes came with it: `DemSource` mosaics/reprojects the DEM in
  horizontal bands into a disk-backed memmap instead of one resident
  grid (a ~1.4 G-cell float32 grid would need ~5.5 GB), and the build
  skips whole tiles the influence field cannot claim via
  `Field.may_claim(rect)` — exact for `RasterField` (grid slice check),
  conservative rect tests for the ramps. OSM fetches for Lincoln were
  split into sub-bbox pulls (Overpass 504s on city-sized queries) and
  merged with `scripts/merge_osm.py`.
- `geoworld_compiler` (Python) writes the format: `manifest.json` + 256x256
  tiles with zlib-compressed sections (elevation int16, influence u8,
  surface/road u8, water bitset, water depth u8, building u8 + levels u8). `dem.py` mosaics + reprojects real elevation
  rasters (rasterio/pyproj) onto the geo meter grid; `fetch.py` downloads
  USGS 3DEP 1 m DEM tiles from The National Map.
- `datasets/beatrice.geoworld` is real USGS 1 m LiDAR terrain for
  Beatrice, NE plus the US-77 corridor and Lincoln: the 8 km square
  around downtown, a ~4 km-wide strip running ~60 km north through
  Pickrell/Cortland/Princeton, and the Lincoln metro region
  (372-428 m real elevation). Spawn is downtown Beatrice; the Big Blue
  River valley is visible east of the origin.
  `datasets/synthetic.geoworld` remains as the no-GIS pipeline test.

### Beatrice landmarks (block coords)

Block `(x, z)` maps to geo `(east_m, -north_m)` relative to the anchor at
6th & Court downtown. `+x` = east, `+z` = south.

| Place                              | x     | z     |
|------------------------------------|-------|-------|
| 6th St & Court St (US-77 downtown) | 26    | 248   |
| 17th St & High St                  | 1337  | -128  |
| Orange Blvd & East Scott Rd        | 3046  | 519   |
| Orange Blvd south end (high school)| 3055  | 610   |
| Big Blue River channel             | 1243  | 1782  |
| Dusenbery-Doyle Reservoir          | -600  | 1985  |
| Demo landmark house                | 200   | 60    |
| US-77 road deck over stream        | 57    | 1279  |
| Cortland (corridor town)           | 1835  | -31062|
| Princeton (corridor town)          | 2716  | -34307|
| Downtown Lincoln (13th & P)        | 2196  | -60655|
| Nebraska State Capitol             | 2474  | -60052|
| Memorial Stadium (UNL)             | 1923  | -61426|
| Haymarket                          | 1431  | -60869|
- Debug commands: `/geoworld info`, `/geoworld geo`,
  `/geoworld geo <east> <north>`.

## Scripted in-game tests

Modeled on the scripted-run framework used by the Planetary Sable project:
a PowerShell launcher creates a project-owned dedicated server (no desktop
input, no user instance), generates a world with `level-type=geoworld:geoworld`,
and the mod's `terrain_survey` scenario (`-Dgeoworld.serverScenario`) force-
generates sample columns, dumps `GEOWORLD-SURVEY` evidence, evaluates
`GEOWORLD-ASSERT` checks (heights match dataset, no water on the geographic
surface, vanilla untouched outside coverage), prints `GEOWORLD-RESULT`, and
halts.

```
./scripts/Test-Terrain.ps1              # full run, asserts must pass
./scripts/Test-Terrain.ps1 -LevelType minecraft:normal -Tag control
                                        # vanilla control run for comparison
./scripts/Test-Terrain.ps1 -Seed <n>    # pin the world seed
```

Each run writes `artifacts/terrain-<timestamp>/` with the server log,
result.json assertion ledger, and the generated world.

## Trying it

```
gradlew :mod:runClient
```

Create a world, select the **GeoWorld** world type. With the copied
`beatrice.geoworld` dataset, spawn sits on real Beatrice terrain blending
into vanilla at the dataset edge (~4 km out).

## Compiler

```
cd compiler
pip install -r requirements.txt
python -m geoworld_compiler fetch \
    --bbox -96.805,40.223,-96.689,40.313 --out ../datasets/raw
python -m geoworld_compiler build --source dem --dem ../datasets/raw/*.tif \
    --out ../datasets/beatrice.geoworld --name beatrice \
    --anchor 40.2681,-96.7470 --datum-elevation 381 --datum-y 64 \
    --radius 4000 --ramp 500
python -m pytest tests/
```
