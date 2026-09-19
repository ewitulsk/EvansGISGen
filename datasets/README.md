# datasets/

Compiled `.geoworld` datasets live here. They are build artifacts of the
compiler (`compiler/`), but small enough to commit.

- `beatrice.geoworld` — real terrain for downtown Beatrice, NE, compiled from
  USGS 3DEP 1 m DEM tiles (`NE_SouthernNE_2018_D19`, fetched into `raw/` via
  `geoworld_compiler fetch`). Covers ~8 km x 8 km around the anchor
  (40.2681 N, -96.7470 W); influence ramps to vanilla over the outer 500 m.
- `synthetic.geoworld` — generated test dataset: rolling synthetic terrain +
  a circular influence field (~800 m full, fading to vanilla by ~1400 m)
  centered on the configured origin. Exercises the full
  compiler -> tile -> worldgen path without real GIS input.

Raw DEM downloads live in `raw/` (gitignored; ~1 GB).

To use a dataset in a dev run: the `mod` Gradle build copies `datasets/*` into
`run/geoworld/` and the default `config/geoworld.json` points `dataset_path`
at `geoworld/beatrice.geoworld`.
