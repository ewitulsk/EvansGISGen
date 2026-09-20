"""Author a demo landmark .nbt for the Beatrice dataset (Phase 9).

Writes a gzipped structure-template NBT (the format StructureTemplate.load
reads) — a small brick house: plank floor, brick walls with a glass band,
a door gap on the north face, flat dark-oak roof — plus the landmarks.json
entry that places it on a real residential lot east of downtown.

    python scripts/make_demo_landmark.py

The output lands inside datasets/beatrice.geoworld/ so the dataset stays
self-contained. Real landmarks (e.g. the childhood home) get authored
in-game with a structure block and dropped into landmarks/ the same way.
"""

import gzip
import json
import struct
import sys
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / "datasets" / "beatrice.geoworld"
DATA_VERSION = 3955  # Minecraft 1.21.1

# --- minimal NBT writer (big-endian, gzip) ----------------------------------

def _named(tid: int, name: str, payload: bytes) -> bytes:
    return bytes([tid]) + struct.pack(">H", len(name)) + name.encode() + payload

def t_int(name, v):      return _named(3, name, struct.pack(">i", v))
def t_str(name, v):      return _named(8, name, struct.pack(">H", len(v)) + v.encode())
def t_list(name, tid, items):
    return _named(9, name, bytes([tid]) + struct.pack(">i", len(items)) + b"".join(items))
def t_cmp(name, entries): return _named(10, name, b"".join(entries) + b"\x00")
def cmp_payload(entries): return b"".join(entries) + b"\x00"  # unnamed, for lists

def int_list(vals):  # unnamed IntTag list payload entries
    return [struct.pack(">i", v) for v in vals]

# --- the demo house ----------------------------------------------------------

AIR, FLOOR, WALL, GLASS, ROOF = range(5)
PALETTE = ["minecraft:air", "minecraft:oak_planks", "minecraft:bricks",
           "minecraft:glass", "minecraft:dark_oak_planks"]

SX, SY, SZ = 11, 5, 9

def block_at(x: int, y: int, z: int) -> int:
    wall = x in (0, SX - 1) or z in (0, SZ - 1)
    if y == 0:
        return FLOOR
    if 1 <= y <= 3 and wall:
        if z == 0 and x == 5 and y <= 2:
            return AIR                    # doorway on the north face
        if y == 2 and ((x % 3 == 1 and z in (0, SZ - 1))
                       or (z % 3 == 1 and x in (0, SX - 1))):
            return GLASS                  # window band
        return WALL
    if y == 4:
        return ROOF
    return AIR

def main() -> int:
    palette_tag = t_list("palette", 10,
                         [cmp_payload([t_str("Name", n)]) for n in PALETTE])
    blocks = []
    for y in range(SY):
        for z in range(SZ):
            for x in range(SX):
                blocks.append(cmp_payload([
                    t_list("pos", 3, int_list([x, y, z])),
                    t_int("state", block_at(x, y, z)),
                ]))
    root = t_cmp("", [
        t_list("size", 3, int_list([SX, SY, SZ])),
        t_list("blocks", 10, blocks),
        palette_tag,
        t_list("entities", 10, []),
        t_int("DataVersion", DATA_VERSION),
    ])
    nbt = gzip.compress(root)

    lm_dir = DATASET / "landmarks"
    lm_dir.mkdir(parents=True, exist_ok=True)
    (lm_dir / "demo_house.nbt").write_bytes(nbt)

    # Block position (200, 60) is a real residential footprint east of
    # downtown -> east=200, north=-60. Anchor [5,0,0] centers the house on
    # the lot with its door facing north; LEVEL_FOUNDATION flattens the lot
    # to the dataset surface and the bbox suppresses the procedural shell.
    landmarks = {"landmarks": [{
        "id": "demo_house",
        "template": "demo_house.nbt",
        "position": {"east": 200.0, "north": -60.0},
        "anchor": [5, 0, 0],
        "rotation": "NONE",
        "terrain": "LEVEL_FOUNDATION",
        "replaces_building": "ms:demo",
    }]}
    (DATASET / "landmarks.json").write_text(json.dumps(landmarks, indent=2))
    print(f"wrote {lm_dir / 'demo_house.nbt'} ({SX}x{SY}x{SZ}) + landmarks.json")
    return 0

if __name__ == "__main__":
    sys.exit(main())
