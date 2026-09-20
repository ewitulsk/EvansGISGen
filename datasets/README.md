# datasets/

Compiled `.geoworld` datasets live here. They are build artifacts of the
compiler (`compiler/`), but small enough to commit.

- `beatrice.geoworld` — real terrain for Beatrice, NE plus the US-77
  corridor both directions — Lincoln metro north, Wymore/Oketo/Marysville
  KS south — compiled from USGS 3DEP 1 m DEM tiles
  (`NE_SouthernNE_2018_D19` + `NE_Eastern_UA_2016` + `KS_Statewide_2018`,
  fetched into `raw/` via `geoworld_compiler fetch`). Covers ~22 x 123 km
  anchored at Beatrice (40.2681 N, -96.7470 W); influence ramps to vanilla
  at region edges.
  Sidecars: `landmarks/` (curated .nbt), `parcels.json` (Phase 12 lot index),
  `signs.json` (Phase 13 street-name + stop/yield placements).
- `synthetic.geoworld` — generated test dataset: rolling synthetic terrain +
  a circular influence field (~800 m full, fading to vanilla by ~1400 m)
  centered on the configured origin. Exercises the full
  compiler -> tile -> worldgen path without real GIS input.

Raw DEM downloads live in `raw/` (gitignored; ~1 GB).

To use a dataset in a dev run: the `mod` Gradle build copies `datasets/*` into
`run/geoworld/` and the default `config/geoworld.json` points `dataset_path`
at `geoworld/beatrice.geoworld`.
