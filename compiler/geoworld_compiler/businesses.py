"""Known-business registry (Phase 19): identity -> business key.

A "known business" is a footprint we can name — Walmart, McDonald's,
U.S. Bank — rather than a generic class. Identity comes from OSM
identity tags (`brand`, `brand:wikidata`, `name`, `operator`, `ref`)
on the building way or on POI nodes inside it, plus a manual
`OVERRIDES` table for stores OSM doesn't tag.

The registry entry carries everything the runtime needs to theme the
building: the emitted u8 layer id, a display name, a facade palette
(block names resolved by the mod against the vanilla registry), and an
optional interior `layout` key — footprints whose business has one get
the `interior` zone raster (Contract 3) and a module set (Contract 4).
"""

from __future__ import annotations

import math

# business key -> registry record. `id` is the value written into the
# `business` tile layer (0 = none); keep ids small and stable — they
# are persisted in datasets.
BUSINESSES: dict[str, dict] = {
    "walmart": {
        "id": 1,
        "display": "Walmart Supercenter",
        "palette": {
            "wall": "light_gray_concrete",
            "accent": "blue_concrete",
            "accent_y": "parapet",
            "floor": "smooth_stone_slab",
            "roof": "gray_concrete",
            "window": "glass",
        },
        "layout": "supercenter_v1",
        "match": {
            "brand": {"walmart", "wal-mart", "walmart supercenter",
                      "walmart neighborhood market"},
            "brand:wikidata": {"Q483551"},
            "name": {"walmart", "walmart supercenter",
                     "walmart neighborhood market", "wal-mart"},
            "operator": {"walmart", "walmart inc."},
        },
    },
    "mcdonalds": {
        "id": 2,
        "display": "McDonald's",
        "palette": {
            "wall": "white_concrete",
            "accent": "yellow_concrete",
            "accent_y": "parapet",
            "floor": "smooth_stone_slab",
            "roof": "red_concrete",
            "window": "glass",
        },
        "layout": "restaurant_v1",
        "match": {
            "brand": {"mcdonald's", "mcdonalds", "mc donald's"},
            "brand:wikidata": {"Q38076"},
            "name": {"mcdonald's", "mcdonalds"},
            "operator": {"mcdonald's", "mcdonalds"},
        },
    },
    "us_bank": {
        "id": 3,
        "display": "U.S. Bank",
        "palette": {
            "wall": "white_concrete",
            "accent": "red_concrete",
            "accent_y": "parapet",
            "floor": "polished_andesite",
            "roof": "light_gray_concrete",
            "window": "glass",
        },
        "layout": None,
        "match": {
            "brand": {"u.s. bank", "us bank", "u.s. bancorp"},
            "brand:wikidata": {"Q7390848"},
            "name": {"u.s. bank", "us bank", "u.s. bank branch",
                     "u.s. bank tower"},
            "operator": {"u.s. bank", "us bank", "u.s. bancorp"},
        },
    },
}

# Manual overrides for stores whose OSM way carries no identity tags.
# Each entry: footprint centroid within `radius` meters of (lat, lon)
# resolves to `business`. Anchored to Nominatim/OSM positions of the
# real stores (2026-09); the radius covers the footprint + parking-tag
# slop without bleeding into neighbors.
OVERRIDES: list[dict] = [
    {"lat": 40.3036881, "lon": -96.7442309, "radius": 150.0,
     "business": "walmart", "note": "Beatrice NE Supercenter (way 593154777)"},
    {"lat": 39.8432950, "lon": -96.6051493, "radius": 150.0,
     "business": "walmart", "note": "Marysville KS Supercenter (way 95105555)"},
    {"lat": 40.8489672, "lon": -96.6017866, "radius": 150.0,
     "business": "walmart", "note": "Lincoln N 85th Supercenter (way 195040388)"},
    {"lat": 40.8582710, "lon": -96.6777482, "radius": 150.0,
     "business": "walmart", "note": "Lincoln N 27th Supercenter (way 197626744)"},
    {"lat": 40.7294709, "lon": -96.6850782, "radius": 150.0,
     "business": "walmart", "note": "Lincoln Jamie Ln Supercenter (way 343553998)"},
    {"lat": 40.8136085, "lon": -96.7030413, "radius": 60.0,
     "business": "us_bank", "note": "U.S. Bank Tower, 233 S 13th, Lincoln"},
]

# Identity tags worth carrying off OSM elements into a footprint's
# identity record.
IDENTITY_TAGS = ("brand", "brand:wikidata", "name", "official_name",
                 "operator", "ref")


def _norm(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


# Match sets are compared normalized — lowercase them once at load.
for _rec in BUSINESSES.values():
    for _tag in list(_rec["match"]):
        _rec["match"][_tag] = {_norm(v) for v in _rec["match"][_tag]}


def match_business(tags: dict) -> str | None:
    """Business key for an OSM tag set, or None.

    `brand`/`brand:wikidata` are the canonical signals; `name` and
    `operator` cover stores tagged loosely. Exact match after
    normalization — no substring rules, so "Walmart Ave" can't hit.
    """
    if not tags:
        return None
    for key, rec in BUSINESSES.items():
        for tag, values in rec["match"].items():
            v = tags.get(tag)
            if v is not None and _norm(v) in values:
                return key
    return None


def override_business(east: float, north: float, projection) -> str | None:
    """Business key from the manual overrides table, or None.

    `projection` converts the override's WGS84 anchor into dataset geo
    meters; the footprint centroid must land inside its radius.
    """
    best = None
    best_d = math.inf
    for ov in OVERRIDES:
        oe, on = projection.to_geo(ov["lat"], ov["lon"])
        d = math.hypot(east - oe, north - on)
        if d <= ov["radius"] and d < best_d:
            best = ov["business"]
            best_d = d
    return best


def resolve_business(tags: dict, centroid_e: float, centroid_n: float,
                     projection) -> str | None:
    """Business key for a footprint: tags first, then the overrides table."""
    key = match_business(tags)
    if key is not None:
        return key
    return override_business(centroid_e, centroid_n, projection)


def registry_doc() -> dict:
    """The `businesses` section of the dataset's businesses.json."""
    return {key: {"id": rec["id"], "display": rec["display"],
                  "palette": rec["palette"], "layout": rec["layout"]}
            for key, rec in BUSINESSES.items()}
