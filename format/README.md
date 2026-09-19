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
- `water` is the footprint mask; `water_depth` (Phase 5) is the water column
  depth in blocks. At wet columns `elevation` holds the **channel bed** Y
  (the compiler bakes the riverbed into the elevation layer), and the runtime
  fills `bed+1 .. bed+depth` with water — so the water surface lands at
  `bed + depth`, which is the DEM's water-surface elevation.
- Unknown mask bits should be skipped by readers after parsing their section
  header (forward compatibility). Sections always appear in ascending bit
  order.
- Deflate (zlib) is used instead of Zstd for now because both the Python
  stdlib and the JDK have it built in. The codec byte leaves room to add
  Zstd later without a format break.
