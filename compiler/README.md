# geoworld_compiler

Offline GIS -> `.geoworld` dataset compiler. This is deliberately separate
from the mod: Minecraft never sees GeoTIFFs, shapefiles, EPSG codes, or OSM
data — only compiled binary tiles (see `../format/README.md`).

## Setup

```
pip install -r requirements.txt    # pyproj (pillow optional, for previews)
```

Run from this directory (`compiler/`).

## Commands

```
# Compile a dataset (currently synthetic terrain + circular influence;
# real DEM/GIS sources land in Phase 3+)
python -m geoworld_compiler build --out ../datasets/synthetic.geoworld \
    --name synthetic --anchor 40.2681,-96.7470 \
    --datum-elevation 381 --base-elevation 381 \
    --radius-full 800 --radius-edge 1400

# Write a known-pattern .gwt tile (consumed by the mod's GeoTileTest)
python -m geoworld_compiler fixture --out ../mod/src/test/resources/fixture.gwt

# Render layers to PNGs for eyeballing (requires: pip install pillow)
python -m geoworld_compiler preview --dataset ../datasets/synthetic.geoworld

# Tests
python -m unittest discover -s tests -v
```

## Layout

- `transform.py` — mirror of the mod's `GeoTransform` + `Projection` (pyproj)
- `tileio.py` — `.gwt` binary writer/reader
- `manifest.py` — `manifest.json` writer
- `sources.py` — elevation/influence sources (synthetic now, DEM later)
- `build.py` — tile coverage + sampling + emission
- `fixture.py` — deterministic test tile for cross-language format tests
- `preview.py` — PNG debug renders
