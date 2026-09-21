# GeoWorld dataset format (v1)

A `.geoworld` dataset is a directory produced offline by `compiler/`:

```
<name>.geoworld/
├── manifest.json
├── tiles/
│   ├── +0000_+0000.gwt
│   ├── +0000_-0001.gwt
│   └── ...
└── landmarks/            (Phase 9)
    └── index.bin
```

The mod never sees GIS formats (GeoTIFF, shapefiles, EPSG codes, OSM PBF).
Everything is precompiled into projected meter offsets and block-space rasters.

## manifest.json

```json
{
  "format_version": 1,
  "name": "synthetic",
  "tile_size": 256,
  "transform": {
    "origin": { "minecraft_x": 0, "minecraft_z": 0 },
    "scale": { "horizontal_meters_per_block": 1.0, "vertical_meters_per_block": 1.0 },
    "vertical_datum": { "elevation_meters": 381.0, "minecraft_y": 64 }
  },
  "projection": {
    "crs": "EPSG:32614",
    "geo_anchor_east_m": 691569.39,
    "geo_anchor_north_m": 4459949.59
  },
  "layers": ["elevation", "influence"],
  "tile_count": 121,
  "bounds": { "min_tile_x": -6, "max_tile_x": 5, "min_tile_z": -6, "max_tile_z": 5 }
}
```

- `transform` — the dataset-authoritative copy of the `geoworld.json`
  coordinate mapping (see `GeoTransform`). `projection` is documentation only;
  the runtime never reprojects.
- `geo_anchor_*` — the projected CRS point that is `GeoPoint(0, 0)`; all tile
  data is relative to it.

## Coordinate conventions

- A tile covers `tile_size x tile_size` block columns (default 256 = 16 chunks).
- Tile coordinates: `tileX = floorDiv(blockX, 256)`. File name:
  `%+05d_%+05d.gwt` on (tileX, tileZ), e.g. `+0000_-0001.gwt`.
- Within a tile, column index `i = localZ * tileSize + localX` where
  `localX = floorMod(blockX, 256)`.
- Geographic `+east` maps to `+x`; geographic `+north` maps to `-z`.

## .gwt binary layout

All multi-byte integers are **big-endian**.

```
u8[4]  magic = "GWTL"
u16    format version = 1
i32    tile_x
i32    tile_z
u16    layer mask (bit set = section present)
u16    reserved = 0

then one section per set mask bit, in ascending bit order:

  u8   compression codec: 0 = none, 1 = deflate (zlib)
  u32  uncompressed length
  u32  stored length
  u8[] payload (stored length bytes)
```

### Layers

| bit  | name      | payload                                   |
|------|-----------|-------------------------------------------|
| 0x01 | elevation | `i16[65536]` target block Y per column    |
| 0x02 | influence | `u8[65536]` 0..255 geographic influence   |
| 0x04 | surface   | `u8[65536]` surface class ids (Phase 7)   |
| 0x08 | road      | `u8[65536]` road class ids (Phase 6)      |
| 0x10 | water     | bitset, 8192 bytes; bit `i` = column `i`  |
| 0x20 | water_depth | `u8[65536]` water depth in blocks (0 = dry) |
| 0x40 | building  | `u8[65536]` building class ids (Phase 8)  |
| 0x80 | building_levels | `u8[65536]` floor count in footprints |
| 0x100 | roadz    | `i16[65536]` elevated deck top block Y (Phase 13) |
| 0x200 | roade    | `u8[65536]` deck cross-section class (Phase 13) |
| 0x400 | building_id | `u16[65536]` footprint instance ids (Phase 15) |
| 0x800 | building_roof | `i16[65536]` uniform roof top Y (Phase 15) |
| 0x1000 | business | `u8[65536]` known-business ids (Phase 19) |
| 0x2000 | interior | `u8[65536]` interior zone ids (Phase 20) |

Bitset packing: bit `i` is bit `i % 8` (LSB-first) of byte `i / 8`.

Notes:

- `elevation` stores final block Y — the compiler applies the vertical datum
  and scale, so the runtime is a pure lookup. Columns with no source data use
  the sentinel `-32768` (NODATA); readers must treat it as "vanilla" rather
  than a height.
- `influence` of 0 means "vanilla"; 255 means "fully geographic". The compiler
  precomputes the field so the runtime does no polygon distance math.
- `road` class ids (Phase 6): `0` none, `1` asphalt, `2` curb, `3` sidewalk,
  `4` shoulder, `5` track, `6` center-line marking. The compiler rasterizes
  OSM highway cross-sections by priority (major roads overwrite minor), so
  the runtime is a pure class→block lookup; `0` means "no road".
- `surface` class ids (Phase 7): `0` natural/unclassified, `1` grass, `2`
  farmland, `3` forest, `4` residential, `5` commercial, `6` industrial,
  `7` parking, `8` railway, `9` park. Semantic land use only — the runtime
  theme maps classes to blocks.
- `building` class ids: `0` none, `1` residential, `2` commercial,
  `3` industrial, `4` civic, `5` outbuilding, `6` generic (Phase 8), plus
  the Phase 16 use-specific classes — `7` supermarket, `8` restaurant,
  `9` fuel, `10` school, `11` church, `12` hospital, `13` hotel,
  `14` parking, `15` sports, `16` agricultural, `17` auto, `18` storage.
  `building_levels` holds the floor count inside footprint cells
  (`building:levels`/`height` tags, else class defaults). The runtime
  extrudes a shell: floor slab, perimeter walls with window banding, roof.
- `water` is the footprint mask; `water_depth` (Phase 5) is the water column
  depth in blocks. At wet columns `elevation` holds the **channel bed** Y
  (the compiler bakes the riverbed into the elevation layer), and the runtime
  fills `bed+1 .. bed+depth` with water — so the water surface lands at
  `bed + depth`, which is the DEM's water-surface elevation.
- `building_id`/`building_roof` (Phase 15): per-footprint instance data.
  `building_id` is a stable hash of the polygon centroid (1..65535; 0 = no
  building) — two abutting footprints carry different ids even when they
  share a class, so the runtime walls between them instead of merging row
  buildings into one blob. `building_roof` is the instance's single roof
  top block Y, solved from the highest DEM ground under the footprint plus
  `levels*3+1` (NODATA where the compiler had no DEM answer) — a building
  on a slope gets one connected flat roof instead of per-cell stepping.
  Both layers are optional: datasets without them keep the Phase 8
  per-cell/class-boundary fallback.
- `roadz`/`roade` (Phase 13): elevated road decks — bridges and layer>0
  overpasses. `roadz` holds the deck's top block Y per column (NODATA where
  no deck), `roade` the deck's cross-section class (same ids as `road`,
  except shoulders render as pavement). Deck heights are solved offline from
  OSM `bridge`/`layer` tags, under-feature clearance (lower roads, rail,
  water), and a slope-limited grade envelope — the runtime just places a
  two-block slab at `roadz` and leaves the space below open. Ground roads
  under a deck keep their `road` classification.
- `signs.json` (Phase 13): optional sidecar beside `manifest.json` —
  `{"signs": [{"e","n","type","lines","rot"}, ...]}` in geo meters.
  `type` is `street_name` (intersection blades), `stop`, `yield`, or
  `business_pylon` (Phase 19 — one freestanding pylon per known business,
  offset outward from its entrance). `rot` is the precomputed
  `ROTATION_16` facing. Sparse point data, so it lives outside the tile grid.
- `business` (Phase 19): known-business identity per column — `0` none,
  `1` walmart, `2` mcdonalds, `3` us_bank (registry in the dataset's
  `businesses.json` sidecar). Resolved from OSM `brand`/`name`/`operator`
  tags on the way or contained POI nodes, plus a manual overrides table.
  The runtime overrides the class palette with the business's facade
  palette and pylon sign.
- `interior` (Phase 20): zone raster inside footprints whose business
  registry entry carries a `layout`. Zone ids: `1` entrance_vestibule,
  `2` checkout, `3` self_checkout, `4` grocery, `5` general_merchandise,
  `6` clothing, `7` electronics, `8` pharmacy, `9` backroom_employee,
  `10` cart_storage.
- `businesses.json` (Phase 19): optional sidecar beside `manifest.json` —
  `{"businesses": {key: {"id","display","palette","layout"}},
  "instances": {stable_key: {"business","name","entrance","axis_deg"}}}`.
  `stable_key` is `osm:way/<id>` for OSM footprints or `ms:<hash>` for
  ML-only ones — never the collidable 16-bit `building_id`.
- `modules.json` + `modules/*.nbt` (Phase 21): optional sidecar pair —
  `{"modules": {name: "modules/<name>.nbt"}, "placements":
  [{"module","e","n","rot","building"}]}`. `e`/`n` is the module's
  footprint center in geo meters; `rot` uses the landmark rotation
  vocabulary (`NONE`, `CLOCKWISE_90`, `CLOCKWISE_180`,
  `COUNTERCLOCKWISE_90`) and pivots on the center. The runtime stamps
  each placement chunk-clamped on the instance's floor slab, skipping
  columns a landmark claims.
- Unknown mask bits should be skipped by readers after parsing their section
  header (forward compatibility). Sections always appear in ascending bit
  order.
- Deflate (zlib) is used instead of Zstd for now because both the Python
  stdlib and the JDK have it built in. The codec byte leaves room to add
  Zstd later without a format break.
