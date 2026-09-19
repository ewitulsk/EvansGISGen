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

## Current status: Phase 2

- `GeoChunkGenerator` wraps a vanilla `ChunkGenerator` (`geoworld:geoworld`)
  and delegates everything to it, then deforms terrain from geographic data.
- `GeoTransform` anchors projected meter offsets to block coordinates
  (`+east -> +x`, `+north -> -z`), with independent horizontal/vertical scale
  and a configurable vertical datum.
- `config/geoworld.json` picks the dataset (`dataset_path`) and the fallback
  origin/scale. The dataset manifest supplies the authoritative transform.
- `GeoDataset`/`GeoTile` load `.gwt` binary tiles on demand (guava cache,
  immutable) — `dataset.tileAt(x, z)` + `tile.elevation(lx, lz)` are plain
  array lookups; no GIS libraries at runtime.
- `geoworld_compiler` (Python) writes the format: `manifest.json` + 256x256
  tiles with zlib-compressed sections (elevation int16, influence u8,
  surface/road u8, water bitset). Currently emits synthetic rolling terrain +
  a circular influence field; real DEM ingestion lands in Phase 3.
- `datasets/synthetic.geoworld` exercises the full path end-to-end: with the
  dataset loaded, terrain follows tile data; without it, the Phase 0
  flattening circle remains as fallback.
- Debug commands: `/geoworld info`, `/geoworld geo`,
  `/geoworld geo <east> <north>`.

## Trying it

```
gradlew :mod:runClient
```

Create a world, select the **GeoWorld** world type. With the copied
`synthetic.geoworld` dataset, spawn sits inside rolling synthetic terrain
blending into vanilla at the edge.

## Compiler

```
cd compiler
pip install -r requirements.txt
python -m geoworld_compiler build --out ../datasets/synthetic.geoworld \
    --name synthetic --anchor 40.2681,-96.7470 \
    --datum-elevation 381 --base-elevation 381 \
    --radius-full 800 --radius-edge 1400
python -m unittest discover -s tests
```
