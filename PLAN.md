# GeoWorld — Implementation Plan

A generic GeoWorld engine, with Beatrice → US-77 → Lincoln as the first dataset.

The most important architectural decision: **keep GIS ingestion completely outside Minecraft** and make the runtime mod consume a compact, precompiled world dataset. Minecraft/NeoForge should never know what a GeoTIFF, shapefile, EPSG code, or OSM PBF is.

## Overall System

```
                         OFFLINE BUILD PIPELINE
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  DEM/LiDAR       OSM       State GIS      Hand metadata     │
│     │             │            │                │           │
│     └─────────────┴────────────┴────────────────┘           │
│                           │                                 │
│                    GeoWorld Compiler                        │
│                           │                                 │
│                           ▼                                 │
│                  beatrice-lincoln.geoworld                  │
│                                                             │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
                       MINECRAFT MOD
┌─────────────────────────────────────────────────────────────┐
│  GeoWorldDataset                                            │
│       │                                                     │
│       ├── ElevationLayer                                    │
│       ├── InfluenceLayer                                    │
│       ├── WaterLayer                                        │
│       ├── RoadLayer                                         │
│       ├── LandUseLayer                                      │
│       ├── BuildingLayer                                     │
│       └── LandmarkLayer → .nbt structures                   │
│                                                             │
│                  GeoChunkGenerator                          │
│                        │                                    │
│             vanilla + geographic overrides                  │
└─────────────────────────────────────────────────────────────┘
```

That separation will save an enormous amount of pain.

NeoForge/Minecraft 1.21.1 gives us the necessary `ChunkGenerator` hooks: `fillFromNoise`, `buildSurface`, `getBaseHeight`, `getBaseColumn`, structure generation, biome decoration, etc. A custom generator also supplies a `MapCodec`, so this can remain a normal serializable Minecraft world generator rather than relying on invasive runtime hacks.

---

## Phase 0 — Build the skeleton and prove the worldgen hook

Don't touch GIS yet.

Create a NeoForge 1.21.1 mod with:

```
geoworld/
├── GeoWorldMod
├── worldgen/
│   ├── GeoChunkGenerator
│   └── GeoChunkGeneratorCodec
└── geo/
    └── GeoDataset
```

`GeoChunkGenerator` should conceptually wrap a normal Minecraft generator:

```java
public final class GeoChunkGenerator extends ChunkGenerator {
    private final NoiseBasedChunkGenerator delegate;
    private final GeoDataset dataset;
}
```

For everything initially:

```java
delegate.fillFromNoise(...);
delegate.buildSurface(...);
delegate.getBaseHeight(...);
...
```

Then perform one stupid proof-of-concept modification:

Inside a 500-block circle around `(0,0)`, force terrain toward Y=70.

You want to demonstrate:

```
                 modified
             ──────────────
           /                \
__________/                  \__________
            vanilla outside
```

**Definition of done:** You can create a world using your generator, vanilla generation works normally, and you can manipulate terrain in a spatial region without crashing chunk generation.

Do this before obtaining any GIS data.

---

## Phase 1 — Establish the global coordinate system

This is the foundation of everything else.

Use **one global real-world projection** for Beatrice, US-77, and Lincoln.

Do not do:

```
Beatrice coordinates → local system A
Lincoln coordinates  → local system B
```

Do:

```
                 projected geographic coordinates
                              │
                              ▼
                     GeoWorld coordinates
                              │
                   scale = 1 meter/block
                              │
                              ▼
                   Minecraft block coordinates
```

Your runtime API should ultimately be trivial:

```java
BlockPos2D geoToMinecraft(double eastMeters, double northMeters);
GeoPoint minecraftToGeo(int x, int z);
```

Configuration can determine where reality is inserted:

```json
{
    "origin": {
        "minecraft_x": 10000,
        "minecraft_z": -5000
    },

    "scale": {
        "horizontal_meters_per_block": 1.0,
        "vertical_meters_per_block": 1.0
    }
}
```

The projection itself should happen **offline**.

Minecraft should receive:

```
x = 1834.24 meters east of origin
z = 722.17 meters north of origin
```

not:

```
40.2681° N, 96.7470° W
```

**Recommendation:** Start at 1 horizontal block = 1 meter. Keep horizontal and vertical scale independent:

- horizontal scale = 1.0 m/block
- vertical scale = configurable

That lets us later exaggerate Nebraska's subtle relief without distorting streets.

**Definition of done:** Given ten known real locations, your compiler places them at the expected relative Minecraft coordinates.

---

## Phase 2 — Build the GeoWorld compiler and dataset format

This should be a separate project/module from the NeoForge mod.

Repository structure:

```
geoworld/
├── mod/
│   └── Java / NeoForge
│
├── compiler/
│   └── GIS preprocessing tool
│
├── datasets/
│   └── beatrice-lincoln/
│
└── format/
    └── documentation/schema
```

Write the GIS compiler in **Python** initially, because `GDAL`, `rasterio`, `GeoPandas`, `pyproj`, and `shapely` make GIS ETL dramatically easier. It doesn't matter that the Minecraft project is Java — the compiler isn't shipped with the mod. Later, if desired, a Rust pipeline can replace it.

### The compiler outputs tiles

```
beatrice-lincoln.geoworld/

manifest.json

tiles/
    +0000_-0001.gwt
    +0000_+0000.gwt
    +0001_+0000.gwt
    ...

landmarks/
    index.bin
```

A GeoWorld tile might cover **256 × 256 blocks = 16 × 16 chunks**. Each tile contains only relevant layers.

Conceptually:

```
GeoTile
├── elevation
├── influence
├── surface type
├── water
├── roads
├── buildings
└── landmark references
```

### Don't use JSON for the heavy data

JSON is excellent for metadata, landmarks, configuration, and debugging — but terrible for millions of elevation cells.

Use a compact binary format for raster data:

```
HEADER
version
tileX
tileZ

ELEVATION
compressed int16[]

SURFACE
compressed byte[]

ROAD
compressed byte[]

WATER
compressed bitset
```

Compression like Zstd would make these extremely small.

**Definition of done:** Minecraft can request:

```java
GeoTile tile = dataset.tileAt(blockX, blockZ);
```

and retrieving a terrain height is basically:

```java
short elevation = tile.elevation(localX, localZ);
```

No GIS libraries involved.

---

## Phase 3 — Real Beatrice terrain

Now introduce an actual elevation dataset.

The pipeline becomes:

```
DEM / LiDAR
    ↓
reproject
    ↓
crop
    ↓
resample to 1m/block
    ↓
normalize elevation
    ↓
GeoWorld elevation tiles
```

The runtime exposes:

```java
double geographicHeight(int x, int z);
```

The crucial worldgen architecture should be:

```
fillFromNoise()

    vanilla generator
          ↓
    normal terrain
          ↓
 GeoWorld deformation
          ↓
     modified chunk
```

Then let Minecraft's later worldgen stages operate on the result.

### This detail is important

If GIS says:

```
vanilla height = 92
GIS height     = 76
```

we carve downward.

If:

```
vanilla height = 68
GIS height     = 81
```

we extend terrain upward.

But `getBaseHeight()` and `getBaseColumn()` **must also report your GeoWorld-adjusted terrain**. Minecraft uses these methods for various worldgen calculations, so only visually changing blocks in `fillFromNoise()` while telling the rest of the engine that the original height remains would cause inconsistencies. The 1.21.1 `ChunkGenerator` API exposes those methods alongside terrain construction specifically as part of the generator contract.

That should be built correctly early.

**First milestone:** Stand somewhere in Minecraft and recognize Beatrice's real macro topography. No roads. No houses. No river. Just terrain.

---

## Phase 4 — Solve the vanilla transition properly

Do this before roads and buildings.

Create an **influence field**:

```
0.0 = completely vanilla
1.0 = completely geographic
```

So:

```java
double v = vanillaHeight(x, z);
double g = geoHeight(x, z);
double w = influence(x, z);

double height = lerp(v, g, w);
```

Use `smootherstep` rather than linear interpolation:

```java
double smootherstep(double t) {
    return t*t*t*(t*(t*6 - 15) + 10);
}
```

Your compiler should actually **precompute the influence field**. Then runtime generation doesn't calculate expensive polygon distances.

```
              Beatrice

          111111111111
       111111111111111111
     111111111111111111111
     999999999999999999999
      755555555555555557
        3222222222223
           000000

0 = vanilla
1 = GIS
```

This becomes one of the most useful concepts in the entire system.

### Architecture recommendation

Make influence a generic layer:

```java
interface ScalarField {
    float sample(int x, int z);
}
```

Then cities and highway corridors aren't special to terrain generation. They merely contribute to GeoWorld influence.

**Definition of done:** You can fly from Beatrice several kilometers outward and cannot identify the exact boundary where real terrain stops. That is a major project milestone.

---

## Phase 5 — Hydrology

Do water separately from elevation.

Don't assume `DEM below Y → water`. Use real GIS polygons/lines for:

- Big Blue River
- streams
- ponds
- lakes

Compile a `HydrologyLayer` which can provide:

- water surface elevation
- river footprint
- bank zone
- channel depth

Then generate a sensible cross-section:

```
                   floodplain
____________________
                    \
                     \
                      \______
                      ~~~~~~~
                      ~~~~~~~
                      _______
```

This is especially important for Beatrice because the river is geographically distinctive.

**Definition of done:** The Big Blue River is in the correct geographic location and looks like a Minecraft river rather than a DEM artifact.

---

## Phase 6 — Roads and Beatrice street network

Now process OSM/state road data.

The compiler should retain vectors initially:

```
Road {
    polyline
    class
    lanes
    width
    surface
    bridge
    tunnel
}
```

Then rasterize them offline into Minecraft-ready geometry. Don't make the mod perform geometry operations.

Runtime should basically see:

```
(x,z) → ROAD_NONE
(x,z) → ROAD_ASPHALT
(x,z) → ROAD_SIDEWALK
(x,z) → ROAD_CURB
```

Possibly with additional metadata.

Implement road construction in stages:

1. centerlines
2. correct width
3. intersections
4. sidewalks
5. markings
6. bridges
7. signs/traffic lights

For the first pass, asphalt ribbons are sufficient.

OpenStreetMap can provide much of the source geometry, but if you distribute OSM-derived datasets, remember that OSM data is ODbL-licensed and requires attribution.

**Definition of done:** You can navigate Beatrice using its real street network without needing a map. That is when the project will first feel genuinely impressive.

---

## Phase 7 — Land use and generic environment

Add:

- grass
- farmland
- forest
- commercial
- industrial
- residential
- parking
- railway
- parks

Create:

```java
enum SurfaceClass {
    NATURAL,
    GRASS,
    FOREST,
    FARMLAND,
    RESIDENTIAL,
    COMMERCIAL,
    INDUSTRIAL,
    PARKING,
    ...
}
```

And keep **classification separate from block selection**.

Don't compile `GRASS_BLOCK`, `OAK_LOG`, `STONE` into your GIS data. Compile semantic information:

```
FOREST
ROAD_PRIMARY
RESIDENTIAL_LAWN
```

Then the Minecraft mod's style/theme maps them to blocks. That's an important architectural separation.

Eventually you could have:

- `RealisticTheme`
- `VanillaTheme`
- `ModernCityTheme`
- `PostApocalypticTheme`

using exactly the same geographic dataset.

---

## Phase 8 — Building footprints and procedural buildings

Now import building polygons.

For each footprint:

- building ID
- polygon
- building class
- height/floors if available
- orientation
- metadata

Start extremely simple:

```
footprint
     ↓
foundation
     ↓
walls
     ↓
windows
     ↓
roof
```

Don't initially try to create good architecture. The first goal is simply for the urban mass and spacing to be recognizable.

Crucially:

```java
if (landmarks.claims(buildingId)) {
    skipProceduralBuilding();
}
```

That's how we prepare for hand-built buildings.

---

## Phase 9 — Curated `.nbt` landmarks

Now add the childhood home.

Minecraft 1.21.1's `StructureTemplate` system already supports template dimensions, rotation/transforms, block filtering, and placement into a world. But put a GeoWorld abstraction in front of it:

```java
record Landmark(
    String id,
    ResourceLocation template,
    double geoX,
    double geoZ,
    BlockPos anchor,
    Rotation rotation,
    TerrainPolicy terrainPolicy,
    Optional<String> replacesBuilding
) {}
```

Data:

```json
{
    "id": "childhood_home",
    "template": "geoworld:beatrice/childhood_home",
    "position": {
        "east": 1422.53,
        "north": 882.17
    },
    "anchor": [12, 5, 3],
    "rotation": "CLOCKWISE_90",
    "terrain": "LEVEL_FOUNDATION",
    "replaces_building": "osm:123456"
}
```

### Important architectural recommendation

Don't simply call `placeInWorld()` once when a random neighboring chunk generates.

Index landmark bounding boxes by chunk:

```
LandmarkSpatialIndex

chunk 103,42
    → childhood_home

chunk 104,42
    → childhood_home
    → school

chunk 104,43
    → school
```

When a chunk reaches the appropriate generation stage, render only the portion whose bounding box intersects that chunk. Minecraft's template machinery already understands transformed positions and placement settings, so we can leverage it rather than inventing NBT interpretation ourselves.

### Add terrain policies

```java
enum TerrainPolicy {
    NONE,
    LEVEL_FOUNDATION,
    CUT_AND_FILL,
    FOLLOW_TERRAIN
}
```

The childhood house can then contain a basement and a declared ground plane.

**Definition of done:** The manually built house appears:

- in the correct real-world position,
- facing the correct direction,
- aligned to the street,
- with its basement/foundation correct,
- without a procedural building underneath it.

---

## Phase 10 — The US-77 corridor

Only now expand outside Beatrice.

The beauty is that almost nothing new is required architecturally.

Define another influence source:

```
CityInfluence(Beatrice)
RoadCorridorInfluence(US77)
```

Combine:

```java
geoInfluence = max(
    beatriceInfluence,
    highway77Influence
);
```

Conceptually:

```
                         Lincoln

                     █████████████
                  ███████████████████
                         █████
                           █
                           █
                           █
                           █
                           █ US-77
                           █
                           █
                           █
                           █
                        █████
                   ██████████████
                     Beatrice
```

The US-77 corridor might have:

- 0–500m: full GIS
- 500–1500m: blending
- 1500m+: vanilla

You can tweak that considerably.

At this point add:

- road elevation profile
- bridges
- intersections
- nearby buildings
- farmland

**Definition of done:** Drive north from Beatrice on an accurately positioned US-77 and eventually enter vanilla Minecraft without a visible worldgen seam off either side.

---

## Phase 11 — Add Lincoln

At this point Lincoln isn't a new technical feature. It's just more data.

```
GeoRegion:
    beatrice

GeoCorridor:
    us77

GeoRegion:
    lincoln
```

The important realization is: **the runtime doesn't know what a city is**. It only knows:

- elevation field
- surface field
- road field
- water field
- structures
- influence field

That's excellent architecture. Beatrice, Lincoln, US-77, a future railroad, or a custom airport are all just sources of those fields.

---

## Phase 12 — The parcel studio

Turn Phase 9's landmark system into a curation loop: procedural buildings are the baseline, parcels define editable units, a dedicated dimension is the editor, and landmarks are the result. Players find their real house, build it properly, and it appears in the world — overwrite-safe.

### Parcels are a lookup index, not a render layer

Parcel polygons come from county GIS:

- **Lancaster County (Lincoln)** — public ArcGIS FeatureServer (`Assessor/TaxParcels`), geoJSON + Extract, verified: `PARCELID`, `SITEADDRESS` (`"2819 S 16TH ST, LINCOLN, NE, 68502"`), `USEDSCRP`, `NetArea`, owners. Paged bbox pulls cover the region.
- **Gage County (Beatrice)** — ArcGIS MapServer behind the gWorks portal (`mapserver01.gworks.com/.../Gage_County_NE_Assessor`), layer 88 `Parcels`. Queryable, real polygons — but `PID` only, **no address attributes**.

The compiler gains `--parcels` (GeoJSON) → a `parcels.json` sidecar in the dataset:

```json
{
    "parcels": [{"id": "0901100001000", "address": "2819 S 16TH ST, LINCOLN, NE, 68502", "rings": [...]}],
    "addresses": [{"text": "2819 S 16TH ST, LINCOLN, NE", "east": 2100.0, "north": 61200.0, "parcel": "0901100001000"}]
}
```

`addresses` is a **gazetteer** merged from parcel situs fields and OSM `addr:*` tags — so Gage gets address lookup even though its parcel layer lacks them. Runtime `ParcelIndex` does point-in-polygon and address match over a spatial-grid bucket index. The generator still never sees a shapefile. **Built**: `fetch-parcels` + `compile-parcels` → `parcels.json` (121K parcels: Lancaster `lan_*` with situs, Gage `gage_*` geometry-only; 104K gazetteer entries).

### Lookup: layered, degrades gracefully

`/geoworld studio <query>` resolution order:

1. **Rect** `"x0,z0 x1,z1"` (block coords) — every parcel intersecting it → a **multi-parcel lot** (a city block of the Haymarket at once)
2. **Coordinates** `"x,z"` (block coords) — the containing parcel
3. **Gazetteer address match** — offline, normalized (uppercase, canonical suffixes, collapse whitespace); instant
4. **Configured geocoder** → coords → point-in-polygon — optional (`geocoder` block in `geoworld.json`: `provider` census|nominatim|photon, `endpoint`, `timeout_ms`, `autocomplete`). Absent → clean error suggesting coordinates

Address ambiguity self-resolves: the index only contains dataset parcels, so a Beatrice address can't collide with a Lincoln one. Geocoder lat/lon results project through the manifest's `projection` block (`GeoProjection` — UTM forward).

### Autocomplete is a suggestion chain

Brigadier `SuggestionProvider` on the studio argument, merging:

- **Local gazetteer** — sorted array + binary-search prefix match, then all-tokens-substring match; ~15 suggestions, parcel id in the tooltip. Synchronous, microseconds.
- **Remote autocomplete** — only if `geocoder.autocomplete` configured (Photon `/api`, Pelias `/v1/autocomplete`; Nominatim's `/search` works but ranks partials poorly; Census has none). Async with a hard ~400 ms timeout — a keystroke never waits on HTTP; on timeout/error the local matches ship alone.

### The studio dimension

`geoworld:studio` — void-flat, fixed noon. Each selection gets a **deterministic lot slot** (512 m grid, allocation persisted in world `SavedData` `geoworld_studio` along with per-player sessions and return positions).

A lot is a `ParcelSelection` — one parcel or many. Single parcels keep the `parcel_<id>` landmark key; a region becomes `lot_<hash>` of the sorted parcel-id set, so re-selecting the same block overwrites the same landmark. Selections are capped (~640 m span incl. context).

`/geoworld studio <query>` (op-level 2) stages the lot:

- **Real DEM elevation** for the selection plus a 32 m context ring — players build against actual grade and see neighboring lots
- **Outlines**: selected parcels red, neighbors light gray, footprint edges yellow
- **Existing state**: if a landmark is registered for the selection, its `.nbt` pastes into the lot for continued editing (overwrite path); otherwise procedural shells paste inside the selection as scaffolding (`bare` flag for a clean lot)
- Teleport player in; return position saved in `SavedData`

`/geoworld studio save` — `StructureTemplate.fillFromWorld` captures the selection bbox **including terrain** (basements, retaining walls, terraformed slope all persist) → writes `.nbt` to the overlay → upserts a landmark entry keyed by selection id (`"parcels": [...]` recorded for provenance). Same selection → replace. It also stamps the template into the already-generated overworld immediately and reloads the live index. One new `TerrainPolicy` variant:

```java
REPLACE_LOT   // template volume replaces the real lot volume, including
              // below the anchor plane — not just blocks on a flattened pad
```

`/geoworld studio exit` — teleport back.

### User content never lives in the compiled dataset

Saved builds go to an **overlay** — `geoworld_landmarks/` in the world save (`landmarks.json` + `landmarks/*.nbt`), merged over the dataset's compiled `landmarks/` at `LandmarkIndex.load` — overlay wins on id collision. Dataset rebuilds can't wipe player work; the compiled dataset stays pure.

### Offline export for Structure Lab

`geoworld_compiler export-parcel --parcels <parcels.json> --query <e,n|address|e0,n0,e1,n1>` → vanilla-format `.nbt` with parcel rings (red) + footprint edges (yellow). A rect query exports every intersecting parcel — a whole city block for the Haymarket. Feeds `submit_structure` in MinecraftStructureInjector directly — the curation loop gets AI-assisted builds for free.

### Ordering

1. `fetch-parcels` (Lancaster FeatureServer + Gage MapServer) → `parcels.json` + gazetteer + `ParcelIndex`
2. `export-parcel` compiler command (immediate Structure Lab value)
3. Studio dimension + stage/enter/save/exit + `REPLACE_LOT` + overlay merge
4. Geocoder config + suggestion chain

**Definition of done:** `/geoworld studio "2819 S 16th St"` autocompletes, teleports to a real-terrain lot with the neighbor context drawn; the player edits the pasted shell; `save` writes a landmark that appears in the world on the next chunk gen and survives a dataset rebuild; re-entering the same parcel restores the saved build.

---

## Phase 13 — Vertical roads and street signs

Roads today are painted onto terrain: the DEM carries at-grade pavement, so a motorway viaduct or a loop ramp renders as a stripe of gray concrete *on the ground* — the Memorial Stadium interchange (I-180/US-34) currently shows its ramps projected flat. Phase 13 gives roads a third dimension and puts signs where signs go.

### Elevation model

OSM does not give absolute deck heights — it gives `layer` ordering and `bridge`/`tunnel` flags. The compiler resolves heights:

- Every way keeps `layer` (default 0), `bridge`, `tunnel`, `name`, `ref`. `*_link` classes are added (ramps were previously dropped entirely).
- For each `bridge=*` way, per-vertex required height = `max(DEM(v), under_surface + clearance)` where under-features are detected by segment proximity: lower-level road ways (+5 m), rail lines from the landuse fetch (+4.5 m), water via the hydro depth raster (+3 m over the water surface). Interior vertices floor at the way's max detected requirement (viaducts are level); endpoints that touch an at-grade way stay pinned to the DEM so decks land on their approaches.
- A slope-limited envelope (max grade ~8%, forward/backward relaxation) ramps the profile between pins — a +5 m crossing spreads its rise ~60 m each way.
- Embankments need no work: they are terrain, and the DEM already carries them.

### Tile format

Two new optional layers (backward-compatible mask bits `0x100`/`0x200`):

- `roadz` i16 — deck top block-Y where an elevated deck covers the column, `NODATA` elsewhere. Only emitted where the resolved deck is ≥ ~1.5 m above the DEM so embanked ramps still render as ordinary terrain.
- `roade` u8 — the deck's cross-section class (shoulders reclass to asphalt; no gravel on a bridge).

The existing `road` layer still carries the *at-grade* surface — a road passing under a bridge paints both layers.

### Runtime

`paveRoads` gains the deck path: deck cells place a two-block slab (structural under-block + class surface at `roadz`), guardrail walls along deck edges, and pillar supports down to terrain on a deterministic grid where the gap is ≥3 m. Air under the deck is left open — underpasses and river water stay real. The old wet-culvert deck hack remains as the fallback for wet road cells with no `roadz`.

### Signs

`fetch_roads` additionally pulls `node["highway"~"stop|give_way|traffic_signals"]`. The compiler emits a `signs.json` sidecar:

- **Street-name signs** at intersections of named ways (shared-vertex detection; link/motorway classes excluded), offset to a corner past both road widths, one sign listing both street names, blade facing the cross-street's bearing.
- **Stop/yield signs** at their OSM node, offset to the right of travel, facing oncoming traffic.

Runtime `SignIndex` (same lazy sidecar pattern as `ParcelIndex`/`LandmarkIndex`) places a post + standing sign during decoration and sets the `SignBlockEntity` text — real names on real corners.

**Definition of done:** the I-180/US-34 viaduct east of Memorial Stadium renders as an elevated deck with supports and rails, its loop ramps climb and span, the Big Blue bridges float over open water, a downtown corner carries a readable street-name sign, and the scripted survey asserts deck height/clearance at known overpass coordinates.

---

## Runtime Architecture

The runtime architecture to aim for:

```
GeoChunkGenerator
│
├── VanillaGeneratorAdapter
│
├── GeoWorld
│   │
│   ├── GeoDataset
│   │
│   ├── GeoTileCache
│   │
│   └── GeoSpatialIndex
│   │
│   ├── ElevationLayer
│   ├── InfluenceLayer
│   ├── HydrologyLayer
│   ├── SurfaceLayer
│   ├── RoadLayer
│   ├── BuildingLayer
│   └── LandmarkLayer
│
├── generation/
│   ├── TerrainDeformer
│   ├── SurfaceGenerator
│   ├── HydrologyGenerator
│   ├── RoadGenerator
│   ├── ProceduralBuildingGenerator
│   └── LandmarkGenerator
│
└── VanillaCompatibility
```

Do **not** create things like:

```
BeatriceGenerator
LincolnGenerator
Highway77Generator
```

Those concepts belong to the dataset, not the Java implementation.

### Make generation stages explicitly ordered

Formalize this rather than letting different classes mutate chunks whenever they happen to run:

```
VANILLA DENSITY
      ↓
TERRAIN DEFORMATION
      ↓
VANILLA / GEO SURFACE
      ↓
HYDROLOGY
      ↓
ROADS
      ↓
LAND USE / VEGETATION
      ↓
PROCEDURAL BUILDINGS
      ↓
CURATED LANDMARKS
      ↓
DECORATION
```

Some of these will map onto different actual Minecraft generation phases rather than literally executing consecutively in one function, but our domain model should still specify precedence. For example:

```
landmark > procedural building > road > land use
```

unless explicitly overridden. That prevents nasty questions later like: *Why did a tree spawn inside my childhood bedroom?*

NeoForge also exposes biome modifiers for controlling features, carvers, spawns and other biome-generation settings, which may be preferable to fighting those systems inside the chunk generator itself.

### Make everything deterministic

This is extremely important for asynchronous chunk generation. Nothing should depend on:

- which neighboring chunk generated first
- wall-clock time
- global mutable RNG
- whether a player has visited somewhere

Given `world seed + dataset + chunk X/Z`, generation must always produce the same result.

Random procedural house details should use something like:

```java
long seed = hash(worldSeed, buildingId);
```

rather than a shared `Random`. That lets chunks generate concurrently without discrepancies.

### Cache immutable tiles, not generated chunks

```java
class GeoTileCache {
    LoadingCache<TilePos, GeoTile> tiles;
}
```

Maybe keep the most recently used 100–500 tiles resident. Since each 256×256 tile covers 16 chunks, players moving through the city will have excellent locality.

The expensive GIS work has already happened. Runtime operations should mostly be:

- array lookup
- bit lookup
- simple interpolation
- block placement

So worldgen should remain quite fast.

### Build debugging tools early

This is one of the strongest recommendations. Add commands like:

```
/geoworld info

/geoworld layers elevation
/geoworld layers influence
/geoworld layers roads
/geoworld layers buildings

/geoworld goto childhood_home

/geoworld geo
```

And ideally a debug visualization mode:

```
RED     = road
BLUE    = water
GREEN   = GIS influence
YELLOW  = building
PURPLE  = curated landmark
```

GIS alignment problems of 3–10 meters will otherwise be maddening to diagnose.

Also generate PNG previews from the compiler:

```
build/debug/
    elevation.png
    roads.png
    influence.png
    buildings.png
```

You'll catch most data errors without even launching Minecraft.

---

## Deliberately Deferred

Don't initially build:

- realistic procedural architecture
- traffic lights
- signs
- road markings
- individual tree reconstruction
- interiors
- utility poles
- parcel-level property logic
- traffic simulation
- photogrammetry
- automatic landmark reconstruction

Those are attractive distractions.

The first serious vertical slice should be:

```
                Vanilla Minecraft
                       │
                       │ seamless transition
                       ▼

          ┌────────────────────────┐
          │                        │
          │   REAL BEATRICE        │
          │                        │
          │  accurate terrain      │
          │  Big Blue River        │
          │  actual streets        │
          │  basic buildings       │
          │  childhood home NBT    │
          │                        │
          └───────────┬────────────┘
                      │
                      │ US-77
                      │
                      │
                  fade to vanilla
```

Only after that works:

```
US-77 all the way to Lincoln
             ↓
Lincoln GIS region
```

---

## Milestones

| Milestone | Result |
|-----------|--------|
| 0. Generator skeleton | Vanilla generator wrapped successfully |
| 1. Coordinate system | Real points map precisely into Minecraft |
| 2. GeoWorld format | Compiler → tiled runtime dataset |
| 3. Elevation | Real Beatrice terrain appears |
| 4. Blending | Seamless vanilla ↔ GIS transition |
| 5. Water | Big Blue River |
| 6. Roads | Real Beatrice street grid |
| 7. Land use | Farmland, grass, parking, forest, etc. |
| 8. Buildings | Real footprints + primitive structures |
| 9. Landmarks | Custom `.nbt` house works |
| 10. US-77 | Narrow real-world corridor north |
| 11. Lincoln | Second city using exactly the same engine |
| 12. Polish | Bridges, road markings, better architecture, vegetation |

### Architecture gates

Three milestones are architecture gates rather than ordinary features:

- **Milestone 3:** Can we correctly deform Minecraft terrain from an external heightfield?
- **Milestone 4:** Can we blend it with vanilla without breaking caves, surfaces, features, structures, or height queries?
- **Milestone 9:** Can fixed multi-chunk landmarks generate deterministically regardless of chunk-generation order?

If we solve those three cleanly, essentially everything else is data processing and increasingly sophisticated block placement.

---

## First Executable Goal

Make Beatrice terrain + blending the first real vertical slice. Don't start by building a grand GIS abstraction containing roads, houses, Lincoln, landmarks and US-77. Build enough architecture to support them later, but make the first executable goal very small:

> Take an elevation raster of Beatrice, compile it into GeoWorld tiles, start Minecraft, teleport to `(0,0)`, and see real terrain seamlessly surrounded by vanilla Minecraft.

Once that works, we've validated the central technical bet behind the entire project.
