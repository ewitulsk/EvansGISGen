# Working agreements

## Dataset rebuilds: test small, build big once

A full `datasets/beatrice.geoworld` rebuild takes ~4–5 hours (DEM mosaic +
vector rasterization + ~17,650 tiles). Do **not** iterate against it.

For any change that affects the compiled dataset (compiler code, fetch
inputs, build flags), build a **small test region** first — pass a narrow
`--bounds` rect that still exercises the feature:

- General terrain/building work: a slice of Beatrice,
  e.g. `--bounds "-2000,-2000,4000,4000"`.
- Tall buildings / dense urban fabric: downtown Lincoln,
  e.g. `--bounds "1000,-61500,3500,-59500"`.
- Outbuildings/garages: a Lincoln residential block.

Verify with `pytest compiler/tests`, a tile probe
(`tileio.read_tile`), and the terrain survey pointed at the small
dataset. Only when **all** planned phases are implemented and verified
do a single full-dataset rebuild for the release commit.

A full rebuild is a release step, not a test step.
