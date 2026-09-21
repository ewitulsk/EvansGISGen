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
# Compile a dataset (synthetic terrain + circular influence)
python -m geoworld_compiler build --out ../datasets/synthetic.geoworld \
    --name synthetic --anchor 40.2681,-96.7470 \
    --datum-elevation 381 --base-elevation 381 \
    --radius-full 800 --radius-edge 1400

# Real build: DEM + all GIS layers + the US-77 corridor (Beatrice ->
# Lincoln north, Beatrice -> Marysville KS south) + Lincoln metro.
# --bounds is the dataset rect in geo meters (default +-radius square);
# --corridor adds a CorridorRamp strip along motorway/trunk ways in --roads;
# --region adds extra claimed rects composited by max — Lincoln metro, and
# the south corridor band holding Oketo + Marysville + 347 8th Rd (which
# sits ~4 km off US-77, beyond the corridor ramp's reach).
python -m geoworld_compiler build --source dem \
    --dem ../datasets/raw/USGS_1M_*.tif ../datasets/raw/USGS_one_meter_*.tif \
    --anchor 40.2681,-96.7470 --crs EPSG:32614 \
    --radius 4000 --ramp 600 \
    --bounds "-4600,-53000,17000,70400" --corridor \
    --region "-5500,52400,12000,69200" \
    --region "3000,-52000,17000,-25000" \
    --hydro ../datasets/raw/water_all.json \
    --roads ../datasets/raw/roads_all.json \
    --landuse ../datasets/raw/landuse_all.json \
    --buildings ../datasets/raw/buildings_all.json \
    --ms-buildings ../datasets/raw/buildings_ms_all.json \
    --pois ../datasets/raw/pois_all.json \
    --reclassify-outbuildings \
    --out ../datasets/beatrice.geoworld --name beatrice

# Data fetches (all take --bbox 'minLon,minLat,maxLon,maxLat')
python -m geoworld_compiler fetch              --bbox ... --out DIR   # USGS 1 m DEM
python -m geoworld_compiler fetch-water        --bbox ... --out FILE  # OSM waterways
python -m geoworld_compiler fetch-roads        --bbox ... --out FILE  # OSM highways
python -m geoworld_compiler fetch-landuse      --bbox ... --out FILE  # OSM land use
python -m geoworld_compiler fetch-buildings    --bbox ... --out FILE  # OSM buildings
python -m geoworld_compiler fetch-buildings-ms --bbox ... --out FILE  # MS GlobalML
python -m geoworld_compiler fetch-pois         --bbox ... --out FILE  # OSM POI nodes

# Parcels (Phase 12): ArcGIS parcel layer -> GeoJSON, then into the dataset
python -m geoworld_compiler fetch-parcels \
    --service https://gis.lincoln.ne.gov/public/rest/services/Assessor/TaxParcels/FeatureServer \
    --layer 0 --bbox "minLon,minLat,maxLon,maxLat" --out ../sources/parcels_lancaster.geojson
python -m geoworld_compiler compile-parcels --in ../sources/parcels_lancaster.geojson \
    --anchor 40.2681,-96.7470 --id-field PARCELID --address-field SITEADDRESS \
    --id-prefix lan_ --out ../datasets/beatrice.geoworld/parcels.json
python -m geoworld_compiler compile-parcels --in ../sources/parcels_gage.geojson \
    --anchor 40.2681,-96.7470 --id-field PID --address-field "" \
    --id-prefix gage_ --append --out ../datasets/beatrice.geoworld/parcels.json

# Lot outline .nbt for Structure Lab — query is 'e,n', an address, or a
# rect 'e0,n0,e1,n1' exporting every intersecting parcel (a whole block)
python -m geoworld_compiler export-parcel \
    --parcels ../datasets/beatrice.geoworld/parcels.json \
    --query "1100 O ST" --out /tmp/lot.nbt

# Write a known-pattern .gwt tile (consumed by the mod's GeoTileTest)
python -m geoworld_compiler fixture --out ../mod/src/test/resources/fixture.gwt

# Render layers to PNGs for eyeballing (requires: pip install pillow)
python -m geoworld_compiler preview --dataset ../datasets/synthetic.geoworld

# Tests
python -m pytest tests/ -q
```

## Layout

- `transform.py` — mirror of the mod's `GeoTransform` + `Projection` (pyproj)
- `tileio.py` — `.gwt` binary writer/reader
- `manifest.py` — `manifest.json` writer
- `influence.py` — composable fields: `BoxRamp`, `DiscRamp`, `CorridorRamp`,
  `RasterField` (rasterized corridor weights), `combine_max`/`combine_sum`
- `sources.py` — synthetic elevation/influence source
- `dem.py` — DEM mosaic + reproject + resample onto the geo grid
- `hydro.py` / `roads.py` / `landuse.py` / `buildings.py` — OSM/MS vector
  sources rasterized into per-cell layers over the dataset rect
- `fetch.py` — USGS TNM DEM downloader; Overpass fetchers live beside their
  sources
- `parcels.py` — ArcGIS parcel fetch (`fetch-parcels`), `parcels.json`
  compile (`compile-parcels`), address gazetteer, lot-outline `.nbt`
  export (`export-parcel`, single parcel or rect multi-parcel)
- `nbtwrite.py` — minimal vanilla structure-template `.nbt` writer
- `build.py` — tile coverage (rect bounds) + sampling + emission
- `fixture.py` — deterministic test tile for cross-language format tests
- `preview.py` — PNG debug renders
