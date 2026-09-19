# datasets/

Compiled `.geoworld` datasets live here. They are build artifacts of the
compiler (`compiler/`), but small enough to commit.

- `synthetic.geoworld` — generated test dataset: rolling synthetic terrain +
  a circular influence field (~800 m full, fading to vanilla by ~1400 m)
  centered on the configured origin. Used to exercise the full
  compiler -> tile -> worldgen path before real GIS data lands in Phase 3.

To use it in a dev run: the `mod` Gradle build copies `datasets/*` into
`run/geoworld/` and the default `config/geoworld.json` points
`dataset_path` at `geoworld/synthetic.geoworld`.
