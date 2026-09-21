"""Module library + placements (Phase 21 / Contract 4).

A *module* is a small vanilla-structure NBT fragment stamped inside a
business footprint — a shelf aisle, a checkout lane, a cart corral.
The v1 library is generated programmatically here (the same blocks
Structure Lab authors would capture); later revisions can replace these
with lab-authored NBTs without touching the pipeline.

`module_placements` reads the interior zone raster and emits one
placement record per module: `{"module", "e", "n", "rot", "building"}`.
Rotations use the landmark vocabulary (NONE / CLOCKWISE_90 /
CLOCKWISE_180 / COUNTERCLOCKWISE_90); module-local +Z is the customer-
facing side (checkout lanes face the entrance), module-local +X is the
long axis.

Canonical orientation: +X = long axis, +Z = customer approach.
"""

from __future__ import annotations

from pathlib import Path

from . import nbtwrite
from .interiors import (ZONE_CART_STORAGE, ZONE_CHECKOUT, ZONE_GENERAL,
                        ZONE_GROCERY, ZONE_SELF_CHECKOUT, supports_layout)
from .interiors import _frame  # shared entrance-anchored frame


# ---------------------------------------------------------------------------
# v1 module builders — (x, y, z, block_name) lists, canonical orientation.


def _shelf_aisle() -> tuple[tuple[int, int, int], list]:
    """Double-sided shelf unit: 8 long, 3 high, 2 deep."""
    blocks = []
    for x in range(8):
        for z in range(2):
            blocks.append((x, 0, z, "white_concrete"))
            blocks.append((x, 1, z, "light_gray_concrete"))
            blocks.append((x, 2, z, "white_concrete"))
        # Stock color strip down the face — reads as product rows.
        blocks.append((x, 1, 0, "gray_concrete") if x % 2 == 0 else
                      (x, 2, 0, "gray_concrete"))
    return (8, 3, 2), blocks


def _checkout_lane() -> tuple[tuple[int, int, int], list]:
    """Checkout lane: 4-long belt counter + register post, front +Z."""
    blocks = []
    for x in range(4):
        blocks.append((x, 0, 0, "light_gray_concrete"))
        blocks.append((x, 1, 0, "gray_concrete"))   # belt
    blocks.append((3, 0, 1, "white_concrete"))
    blocks.append((3, 1, 1, "white_concrete"))
    blocks.append((3, 2, 1, "gray_concrete"))        # register
    blocks.append((3, 3, 1, "black_concrete"))       # display
    return (4, 4, 2), blocks


def _cart_corral() -> tuple[tuple[int, int, int], list]:
    """Cart corral: bar-railed bay 3 wide, 4 deep, open at -Z."""
    blocks = []
    for z in range(4):
        blocks.append((0, 0, z, "iron_bars"))
        blocks.append((2, 0, z, "iron_bars"))
        blocks.append((0, 1, z, "iron_bars"))
        blocks.append((2, 1, z, "iron_bars"))
    blocks.append((0, 0, 3, "gray_concrete"))
    blocks.append((1, 0, 3, "gray_concrete"))
    blocks.append((2, 0, 3, "gray_concrete"))
    return (3, 2, 4), blocks


def _vestibule() -> tuple[tuple[int, int, int], list]:
    """Vestibule arch: glazed front with a 2-wide opening, 6x4x6."""
    blocks = []
    for x in range(6):
        for y in range(4):
            for z in (0, 5):
                if z == 0 and y < 3 and x in (2, 3):
                    continue  # doorway
                blocks.append((x, y, z, "glass"))
    for x in range(6):
        blocks.append((x, 4, 0, "white_concrete"))
        blocks.append((x, 4, 5, "white_concrete"))
    for z in range(1, 5):
        blocks.append((0, 4, z, "white_concrete"))
        blocks.append((5, 4, z, "white_concrete"))
    return (6, 5, 6), blocks


MODULE_BUILDERS = {
    "shelf_aisle": _shelf_aisle,
    "checkout_lane": _checkout_lane,
    "cart_corral": _cart_corral,
    "vestibule": _vestibule,
}


def write_module_nbts(out_dir: str | Path) -> list[str]:
    """Emit `modules/<name>.nbt` for every v1 module; returns names."""
    out = Path(out_dir) / "modules"
    for name, build in MODULE_BUILDERS.items():
        size, blocks = build()
        nbtwrite.write_structure(out / f"{name}.nbt", size, blocks)
    return list(MODULE_BUILDERS)


# ---------------------------------------------------------------------------
# Placement: read the zone raster in the instance's entrance frame.


def _rot_for_axis(dx: float, dz: float) -> str:
    """Rotation aligning module +X (long axis) with block dir (dx, dz)."""
    if abs(dx) >= abs(dz):
        return "NONE"
    return "CLOCKWISE_90"


def _rot_for_front(dx: float, dz: float) -> str:
    """Rotation pointing module +Z (customer side) at block dir (dx, dz)."""
    if abs(dx) >= abs(dz):
        return "COUNTERCLOCKWISE_90" if dx > 0 else "CLOCKWISE_90"
    return "NONE" if dz > 0 else "CLOCKWISE_180"


def _runs(mask, lo, hi, min_len):
    """Contiguous True runs of `mask(l)` over [lo, hi]; yields (a, b)."""
    out = []
    a = None
    l = lo
    while l <= hi:
        if mask(l):
            if a is None:
                a = l
        elif a is not None:
            if l - a >= min_len:
                out.append((a, l))
            a = None
        l += 1.0
    if a is not None and hi - a >= min_len:
        out.append((a, hi))
    return out


def module_placements(buildings) -> list[dict]:
    """Module placements for every layout-bearing business instance."""
    out = []
    for inst in buildings.instances:
        if not supports_layout(inst.get("layout")):
            continue
        (ex, ey), (fx, fy), (lx, ly), depth_max, lat_min, lat_max = \
            _frame(inst)

        def gp(d, l):
            return (ex + fx * d + lx * l, ey + fy * d + ly * l)

        def zone_at(d, l):
            e, n = gp(d, l)
            return buildings.interior_zone(e, n)

        # Block-space axes: geo (e, n) -> block (x = e, z = -n).
        aisle_rot = _rot_for_axis(lx, -ly)
        front_rot = _rot_for_front(-fx, fy)

        # Shelf aisles: depth rows every 6m through grocery/GM, one 8m
        # unit per ~10m of contiguous stock zone.
        for d in range(20, max(21, int(depth_max) - 13), 6):
            for a, b in _runs(
                    lambda l: zone_at(d, l) in (ZONE_GROCERY, ZONE_GENERAL),
                    lat_min + 1, lat_max - 1, 10.0):
                c = a + 4.0
                while c + 4.0 <= b - 1.0:
                    e, n = gp(d, c)
                    out.append({"module": "shelf_aisle", "e": e, "n": n,
                                "rot": aisle_rot, "building": inst["key"]})
                    c += 10.0

        # Checkout lanes: through the checkout/self-checkout band at ~11m
        # depth, one every 4m of lane.
        for zone_id in (ZONE_CHECKOUT, ZONE_SELF_CHECKOUT):
            for a, b in _runs(lambda l: zone_at(11.0, l) == zone_id,
                              lat_min + 1, lat_max - 1, 5.0):
                c = a + 2.0
                while c + 2.0 <= b:
                    e, n = gp(11.0, c)
                    out.append({"module": "checkout_lane", "e": e, "n": n,
                                "rot": front_rot, "building": inst["key"]})
                    c += 4.0

        # Cart corrals: one per cart-storage pocket.
        for a, b in _runs(lambda l: zone_at(4.0, l) == ZONE_CART_STORAGE,
                          lat_min + 1, lat_max - 1, 4.0):
            e, n = gp(4.0, (a + b) / 2.0)
            out.append({"module": "cart_corral", "e": e, "n": n,
                        "rot": front_rot, "building": inst["key"]})
    return out


def modules_doc(placements: list[dict]) -> dict:
    """The modules.json sidecar: template paths + placement records."""
    return {
        "modules": {name: f"modules/{name}.nbt" for name in MODULE_BUILDERS},
        "placements": [
            {**p, "e": round(p["e"], 2), "n": round(p["n"], 2)}
            for p in placements
        ],
    }
