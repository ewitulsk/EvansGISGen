"""Parcels: fetch county GIS parcel polygons, compile parcels.json, export
lot outlines as vanilla .nbt (Phase 12).

parcels.json is a dataset sidecar — a lookup index, not a render layer:

    {
        "parcels":   [{"id", "address", "bbox": [e0,n0,e1,n1],
                       "rings": [[[e,n], ...], ...]}],
        "addresses": [{"text", "east", "north", "parcel"}]
    }

`rings` are parcel polygon rings in dataset geo meters (first = exterior,
rest = holes). `addresses` is a gazetteer: normalized situs addresses plus
OSM addr:* points — each resolves to a geo point and (when known) its
parcel id, so address lookup never needs a live geocoder for covered
ground.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

from .transform import Projection

# --- ArcGIS fetch ------------------------------------------------------------

# Browser UA — some county GIS hosts (gWorks/WAF) 403 non-browser clients.
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"}


def fetch_parcels(service_url: str, layer: int,
                  min_lon: float, min_lat: float,
                  max_lon: float, max_lat: float, out: str | Path,
                  *, page_size: int = 2000) -> Path:
    """Paged bbox query against an ArcGIS FeatureServer/MapServer layer;
    writes a merged GeoJSON FeatureCollection.

    Works for Lancaster's public FeatureServer and the gWorks-hosted Gage
    County MapServer alike — both speak the same /query API.
    """
    url = service_url.rstrip("/") + f"/{layer}/query"
    features: list[dict] = []
    offset = 0
    while True:
        qs = urllib.parse.urlencode({
            "where": "1=1",
            "geometry": f"{min_lon},{min_lat},{max_lon},{max_lat}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "true",
            "outSR": "4326",
            "f": "geojson",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        })
        req = urllib.request.Request(f"{url}?{qs}", headers=_UA)
        with urllib.request.urlopen(req, timeout=120) as r:
            page = json.load(r)
        feats = page.get("features", [])
        features.extend(feats)
        print(f"  parcels page: {len(feats)} (total {len(features)})")
        # exceededTransferLimit is an ArcGIS quirk flag; a short page is the
        # reliable end signal on servers that omit it.
        if len(feats) < page_size and not page.get("exceededTransferLimit"):
            break
        offset += len(feats)
        if not feats:
            break

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))
    print(f"wrote {len(features)} parcels: {out}")
    return out


# --- address normalization ---------------------------------------------------

_SUFFIX = {
    "STREET": "ST", "AVENUE": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR",
    "ROAD": "RD", "LANE": "LN", "COURT": "CT", "PLACE": "PL",
    "TERRACE": "TER", "CIRCLE": "CIR", "HIGHWAY": "HWY", "PARKWAY": "PKWY",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
}


def normalize_address(text: str | None) -> str | None:
    """Canonical comparison form: uppercase, punctuation-stripped,
    whitespace-collapsed, suffix synonyms folded. Returns None when the
    input carries no usable street part."""
    if not text:
        return None
    # Situs addresses carry ", CITY, ST, ZIP" tails — keep the street part.
    s = text.split(",")[0].upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    toks = [_SUFFIX.get(t, t) for t in s.split()]
    return " ".join(toks) or None


# --- parcels.json compile -----------------------------------------------------

def _rings_geo(geom: dict, proj: Projection) -> list[list[list[float]]]:
    """GeoJSON polygon/multipolygon -> rings in dataset geo meters."""
    t = geom.get("type")
    polys = (geom.get("coordinates") or []) if t == "MultiPolygon" \
        else [geom.get("coordinates") or []]
    rings: list[list[list[float]]] = []
    for poly in polys:
        for ring in poly:
            pts = []
            for lon, lat, *_ in ring:
                e, n = proj.to_geo(lat, lon)
                pts.append([round(e, 2), round(n, 2)])
            if len(pts) >= 3:
                rings.append(pts)
    return rings


def _centroid(ring: list[list[float]]) -> tuple[float, float]:
    return (sum(p[0] for p in ring) / len(ring),
            sum(p[1] for p in ring) / len(ring))


def compile_parcels(geojson_paths: list[str | Path], projection: Projection,
                    out: str | Path, *,
                    id_field: str = "PARCELID",
                    address_field: str = "SITEADDRESS",
                    id_prefix: str = "",
                    append: bool = False) -> Path:
    """Merge fetched parcel GeoJSONs into a dataset parcels.json: rings
    projected to geo meters, deduped by parcel id, plus a situs gazetteer.

    `id_prefix` namespaces ids per county (e.g. "gage_") so different
    services' schemes never collide; `append` merges into an existing
    parcels.json for multi-county datasets.
    """
    out = Path(out)
    parcels: dict[str, dict] = {}
    addresses: list[dict] = []
    if append and out.exists():
        prev = json.loads(out.read_text())
        parcels = {p["id"]: p for p in prev.get("parcels", [])}
        addresses = prev.get("addresses", [])
    for path in geojson_paths:
        data = json.loads(Path(path).read_text())
        for f in data.get("features", []):
            props = f.get("properties") or {}
            pid = str(props.get(id_field) or "").strip()
            geom = f.get("geometry")
            if not pid or not geom:
                continue
            pid = id_prefix + pid
            rings = _rings_geo(geom, projection)
            if not rings:
                continue
            e0 = min(p[0] for r in rings for p in r)
            n0 = min(p[1] for r in rings for p in r)
            e1 = max(p[0] for r in rings for p in r)
            n1 = max(p[1] for r in rings for p in r)
            addr = props.get(address_field) if address_field else None
            parcels[pid] = {
                "id": pid,
                "address": addr or None,
                "bbox": [round(e0, 2), round(n0, 2), round(e1, 2), round(n1, 2)],
                "rings": rings,
            }
            norm = normalize_address(addr)
            if norm:
                ce, cn = _centroid(rings[0])
                addresses.append({"text": norm, "east": round(ce, 1),
                                  "north": round(cn, 1), "parcel": pid})

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"parcels": list(parcels.values()), "addresses": addresses}))
    print(f"wrote {len(parcels)} parcels, {len(addresses)} addresses: {out}")
    return out


def add_osm_addresses(osm_json_paths: list[str | Path],
                      projection: Projection, parcels_path: str | Path) -> int:
    """Fold OSM addr:housenumber/addr:street elements into the gazetteer —
    the address source for parcels whose county layer carries no situs.
    Returns the number of gazetteer entries added."""
    data = json.loads(Path(parcels_path).read_text())
    added = 0
    seen = {a["text"] for a in data["addresses"]}
    for path in osm_json_paths:
        for el in json.loads(Path(path).read_text()).get("elements", []):
            tags = el.get("tags") or {}
            num, street = tags.get("addr:housenumber"), tags.get("addr:street")
            if not num or not street:
                continue
            if el.get("type") == "node":
                lat, lon = el.get("lat"), el.get("lon")
            else:
                c = el.get("center") or {}
                lat, lon = c.get("lat"), c.get("lon")
            if lat is None or lon is None:
                continue
            text = normalize_address(f"{num} {street}")
            if not text or text in seen:
                continue
            e, n = projection.to_geo(lat, lon)
            data["addresses"].append({"text": text, "east": round(e, 1),
                                      "north": round(n, 1), "parcel": None})
            seen.add(text)
            added += 1
    Path(parcels_path).write_text(json.dumps(data))
    return added


# --- point-in-polygon / parcel lookup (compiler side, mirrored at runtime) ----

def _point_in_rings(e: float, n: float, rings: list[list[list[float]]]) -> bool:
    inside = False
    for i, ring in enumerate(rings):
        hit = False
        for a, b in zip(ring, ring[1:] + ring[:1]):
            if (a[1] > n) != (b[1] > n):
                x = a[0] + (n - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
                if e < x:
                    hit = not hit
        inside = hit if i == 0 else inside and not hit
    return inside


def parcel_at(parcels_path: str | Path, east: float, north: float) -> dict | None:
    data = json.loads(Path(parcels_path).read_text())
    for p in data["parcels"]:
        b = p["bbox"]
        if b[0] <= east <= b[2] and b[1] <= north <= b[3] \
                and _point_in_rings(east, north, p["rings"]):
            return p
    return None


def resolve_parcels_query(parcels_path: str | Path, query: str) -> dict | None:
    """'e,n' coordinates or an address string -> the matching parcel."""
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*", query)
    if m:
        return parcel_at(parcels_path, float(m.group(1)), float(m.group(2)))
    data = json.loads(Path(parcels_path).read_text())
    want = normalize_address(query)
    if not want:
        return None
    for a in data["addresses"]:
        if a["text"] == want and a.get("parcel"):
            pid = a["parcel"]
            return next((p for p in data["parcels"] if p["id"] == pid), None)
    # prefix fallback
    for a in data["addresses"]:
        if a["text"].startswith(want) and a.get("parcel"):
            pid = a["parcel"]
            return next((p for p in data["parcels"] if p["id"] == pid), None)
    return None


def parcels_in_rect(parcels_path: str | Path,
                    e0: float, n0: float, e1: float, n1: float) -> list[dict]:
    """Every parcel whose bbox intersects the geo-meter rect — a city block
    or streetscape selection for multi-parcel export/studio edits."""
    data = json.loads(Path(parcels_path).read_text())
    return [p for p in data["parcels"]
            if p["bbox"][0] <= e1 and p["bbox"][2] >= e0
            and p["bbox"][1] <= n1 and p["bbox"][3] >= n0]


# --- outline .nbt export ------------------------------------------------------

def _ring_cells(rings: list[list[list[float]]],
                e0: float, n0: float) -> set[tuple[int, int]]:
    """Rasterize polygon *edges* to 1 m cells local to (e0, n0)."""
    cells: set[tuple[int, int]] = set()
    for ring in rings:
        for a, b in zip(ring, ring[1:] + ring[:1]):
            steps = max(1, int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))))
            for i in range(steps + 1):
                t = i / steps
                cells.add((round(a[0] + (b[0] - a[0]) * t - e0),
                           round(n0 - (a[1] + (b[1] - a[1]) * t))))
    return cells


def export_parcel(parcels_path: str | Path, query: str, out: str | Path,
                  *, buildings_geojson: str | Path | None = None,
                  projection: Projection | None = None) -> Path:
    """Write a vanilla-structure .nbt of the lot outline: parcel boundaries
    as red concrete, building footprint edges as yellow concrete — the seed
    a Structure Lab / studio build starts from.

    `query` is 'e,n', an address, or a rect 'e0,n0,e1,n1' selecting every
    parcel whose bbox intersects it (multi-parcel / city-block export).
    """
    from .nbtwrite import write_structure

    rect = re.fullmatch(
        r"\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*",
        query)
    if rect:
        r0, s0, r1, s1 = (float(v) for v in rect.groups())
        parcels = parcels_in_rect(parcels_path, min(r0, r1), min(s0, s1),
                                  max(r0, r1), max(s0, s1))
    else:
        p = resolve_parcels_query(parcels_path, query)
        parcels = [p] if p else []
    if not parcels:
        raise ValueError(f"no parcel matches {query!r}")

    e0 = min(p["bbox"][0] for p in parcels)
    n0 = min(p["bbox"][1] for p in parcels)
    e1 = max(p["bbox"][2] for p in parcels)
    n1 = max(p["bbox"][3] for p in parcels)
    # Template origin = selection bbox NW corner -> (min east, max north) so
    # template x grows east and z grows south, matching world axes.
    size_x = max(1, int(round(e1 - e0)) + 1)
    size_z = max(1, int(round(n1 - n0)) + 1)
    blocks: list[tuple[int, int, int, str]] = []
    for p in parcels:
        for x, z in _ring_cells(p["rings"], e0, n1):
            if 0 <= x < size_x and 0 <= z < size_z:
                blocks.append((x, 0, z, "minecraft:red_concrete"))

    if buildings_geojson and projection is not None:
        data = json.loads(Path(buildings_geojson).read_text())
        for f in data.get("features", []):
            rings = _rings_geo(f.get("geometry") or {}, projection)
            for x, z in _ring_cells(rings, e0, n1):
                if 0 <= x < size_x and 0 <= z < size_z:
                    blocks.append((x, 1, z, "minecraft:yellow_concrete"))

    out = Path(out)
    write_structure(out, (size_x, 2, size_z), blocks)
    print(f"wrote lot outline: {out} ({size_x}x{size_z}, "
          f"{len(parcels)} parcel(s))")
    return out
