"""Interior zone raster (Phase 20): known-business floor-plan zoning.

The `interior` tile layer stores a zone id per column inside a business
footprint. Zones are solved from the instance's solved entrance edge and
dominant axis — not world orientation — so every Walmart reads the same
regardless of which way it faces: vestibule and checkout up front near
the entrance, departments in the middle, backroom at the rear.

Zone ids are persisted in datasets; the runtime mirrors them
(GeoChunkGenerator.zoneFloor) so floors are tinted per department.
"""

from __future__ import annotations

import math

import numpy as np

# Zone ids — the runtime mirrors this table; keep ids small and stable.
ZONE_NONE = 0
ZONE_VESTIBULE = 1
ZONE_CHECKOUT = 2
ZONE_SELF_CHECKOUT = 3
ZONE_GROCERY = 4
ZONE_GENERAL = 5
ZONE_CLOTHING = 6
ZONE_ELECTRONICS = 7
ZONE_PHARMACY = 8
ZONE_BACKROOM = 9
ZONE_CART_STORAGE = 10

ZONE_NAMES = {
    ZONE_VESTIBULE: "entrance_vestibule",
    ZONE_CHECKOUT: "checkout",
    ZONE_SELF_CHECKOUT: "self_checkout",
    ZONE_GROCERY: "grocery",
    ZONE_GENERAL: "general_merchandise",
    ZONE_CLOTHING: "clothing",
    ZONE_ELECTRONICS: "electronics",
    ZONE_PHARMACY: "pharmacy",
    ZONE_BACKROOM: "backroom_employee",
    ZONE_CART_STORAGE: "cart_storage",
}

# Layouts we can zone. `None` in the business registry means "no
# interior story" (U.S. Bank Tower is a landmark build, not a raster).
_LAYOUTS = ("supercenter_v1", "restaurant_v1")


def supports_layout(layout: str | None) -> bool:
    return layout in _LAYOUTS


def _frame(inst: dict):
    """Entrance-anchored frame: depth grows inward from the entrance
    edge midpoint, lateral runs along the front wall.

    Returns (enter_xy, unit_front, unit_lat, depth_max, lat_min, lat_max).
    """
    ex, ey = inst["entrance"]
    cx, cy = inst["centroid"]
    fx, fy = cx - ex, cy - ey
    d = math.hypot(fx, fy)
    if d <= 0.0:
        fx, fy = 0.0, 1.0
    else:
        fx, fy = fx / d, fy / d
    lx, ly = -fy, fx
    ring = inst["ring"]
    depths = [(px - ex) * fx + (py - ey) * fy for px, py in ring]
    lats = [(px - ex) * lx + (py - ey) * ly for px, py in ring]
    return ((ex, ey), (fx, fy), (lx, ly),
            max(depths), min(lats), max(lats))


def _coords(inst, es, ns):
    """Project geo points into the instance frame -> (depth, lateral)."""
    (ex, ey), (fx, fy), (lx, ly), depth_max, lat_min, lat_max = _frame(inst)
    depth = (es - ex) * fx + (ns - ey) * fy
    lat = (es - ex) * lx + (ns - ey) * ly
    return depth, lat, depth_max, lat_min, lat_max


def zone_field(inst: dict, es: np.ndarray, ns: np.ndarray) -> np.ndarray:
    """Zone ids for geo points inside a business footprint.

    `inst` is the instance record built by buildings.py (entrance,
    centroid, ring, layout). Returns u8 zones; callers mask to the
    footprint cells themselves.
    """
    layout = inst.get("layout")
    if layout == "supercenter_v1":
        return _zone_supercenter(inst, es, ns)
    if layout == "restaurant_v1":
        return _zone_restaurant(inst, es, ns)
    return np.zeros(es.shape, dtype=np.uint8)


def _zone_supercenter(inst, es, ns) -> np.ndarray:
    """Walmart Supercenter: front service band (vestibule / checkout /
    carts), grocery on one flank, hardlines on the other, apparel
    center-left, backroom at the rear."""
    depth, lat, depth_max, lat_min, lat_max = _coords(inst, es, ns)
    width = max(1.0, lat_max - lat_min)
    lat_c = lat - (lat_min + lat_max) / 2.0

    zone = np.full(es.shape, ZONE_GENERAL, dtype=np.uint8)

    # Departments first; the front service band and backroom override.
    zone[(lat_c >= width * 0.08)] = ZONE_GROCERY
    zone[(lat_c <= -width * 0.35)
         & (depth > 15.0) & (depth <= depth_max * 0.6)] = ZONE_ELECTRONICS
    zone[(lat_c < width * 0.08) & (lat_c > -width * 0.35)
         & (depth > 15.0) & (depth <= depth_max * 0.55)] = ZONE_CLOTHING
    zone[(lat_c >= width * 0.30)
         & (depth > 15.0) & (depth <= 34.0)] = ZONE_PHARMACY

    # Front service band, anchored at the entrance (lateral ~0 there).
    front = depth <= 15.0
    zone[front & (np.abs(lat) <= 5.0) & (depth <= 7.0)] = ZONE_VESTIBULE
    zone[front & (np.abs(lat) > 5.0) & (np.abs(lat) <= 10.0)
         & (depth <= 7.0)] = ZONE_CART_STORAGE
    zone[front & (depth > 7.0) & (lat_c <= width * 0.05)] = ZONE_CHECKOUT
    zone[front & (depth > 7.0) & (lat_c > width * 0.05)] = ZONE_SELF_CHECKOUT

    zone[depth >= depth_max - 10.0] = ZONE_BACKROOM
    return zone


def _zone_restaurant(inst, es, ns) -> np.ndarray:
    """Quick-service restaurant: vestibule at the door, counter band
    behind it, dining in the middle, kitchen/backroom at the rear."""
    depth, lat, depth_max, lat_min, lat_max = _coords(inst, es, ns)
    zone = np.full(es.shape, ZONE_GENERAL, dtype=np.uint8)
    zone[(depth <= 5.0) & (np.abs(lat) <= 4.0)] = ZONE_VESTIBULE
    zone[(depth > 5.0) & (depth <= 10.0)] = ZONE_CHECKOUT
    zone[depth >= depth_max - 6.0] = ZONE_BACKROOM
    return zone
