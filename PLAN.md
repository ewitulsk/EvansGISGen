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
