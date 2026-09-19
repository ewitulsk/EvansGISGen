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

## Current status: Phase 3

- `GeoChunkGenerator` wraps a vanilla `ChunkGenerator` (`geoworld:geoworld`)
  and delegates everything to it, then deforms terrain from geographic data:
  `lerp(vanilla, dataset_elevation, influence)` per column, shifting the whole
  column so strata/caves move with the surface. `getBaseHeight`,
  `getBaseColumn`, and `getFirstOccupiedHeight` report the adjusted terrain.
- `GeoTransform` anchors projected meter offsets to block coordinates
  (`+east -> +x`, `+north -> -z`), with independent horizontal/vertical scale
  and a configurable vertical datum.
- `config/geoworld.json` picks the dataset (`dataset_path`) and the fallback
  origin/scale. The dataset manifest supplies the authoritative transform.
- `GeoDataset`/`GeoTile` load `.gwt` binary tiles on demand (guava cache,
  immutable) — `dataset.tileAt(x, z)` + `tile.elevation(lx, lz)` are plain
  array lookups; no GIS libraries at runtime. `-32768` elevation cells are
  NODATA and fall back to vanilla.
- `geoworld_compiler` (Python) writes the format: `manifest.json` + 256x256
  tiles with zlib-compressed sections (elevation int16, influence u8,
  surface/road u8, water bitset). `dem.py` mosaics + reprojects real elevation
  rasters (rasterio/pyproj) onto the geo meter grid; `fetch.py` downloads
  USGS 3DEP 1 m DEM tiles from The National Map.
- `datasets/beatrice.geoworld` is real USGS 1 m LiDAR terrain for downtown
  Beatrice, NE (~8 km square, 1024 tiles, 372-428 m real elevation). Spawn is
  downtown Beatrice; the Big Blue River valley is visible east of the origin.
  `datasets/synthetic.geoworld` remains as the no-GIS pipeline test.
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
