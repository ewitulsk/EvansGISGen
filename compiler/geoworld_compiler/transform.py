"""Mirror of the mod's GeoTransform (Java) — keep semantics identical.

Projected geographic coordinates are meters east/north of a chosen geographic
anchor point (the dataset's "geo origin"), NOT raw CRS eastings/northings.
The compiler converts real-world sources into this space; the mod only ever
sees these meter offsets and maps them to block coordinates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Minecraft's +z axis points south, so geographic north maps to -z.


def _round_half_up(v: float) -> int:
    """Match Java Math.round (half-up) instead of Python's banker's rounding."""
    return math.floor(v + 0.5)


@dataclass(frozen=True)
class GeoTransform:
    """Anchors projected meter offsets to Minecraft block coordinates."""

    origin_x: int = 0          # minecraft block x of the geo origin
    origin_z: int = 0          # minecraft block z of the geo origin
    horizontal_meters_per_block: float = 1.0
    vertical_meters_per_block: float = 1.0
    datum_elevation_meters: float = 0.0  # real elevation mapped to datum_y
    datum_y: int = 64

    def block_x(self, east_meters: float) -> int:
        return self.origin_x + _round_half_up(east_meters / self.horizontal_meters_per_block)

    def block_z(self, north_meters: float) -> int:
        return self.origin_z - _round_half_up(north_meters / self.horizontal_meters_per_block)

    def block_y(self, elevation_meters: float) -> int:
        return self.datum_y + _round_half_up(
            (elevation_meters - self.datum_elevation_meters) / self.vertical_meters_per_block
        )

    def east_meters(self, x: int) -> float:
        return (x - self.origin_x) * self.horizontal_meters_per_block

    def north_meters(self, z: int) -> float:
        return (self.origin_z - z) * self.horizontal_meters_per_block

    def to_json(self) -> dict:
        return {
            "origin": {"minecraft_x": self.origin_x, "minecraft_z": self.origin_z},
            "scale": {
                "horizontal_meters_per_block": self.horizontal_meters_per_block,
                "vertical_meters_per_block": self.vertical_meters_per_block,
            },
            "vertical_datum": {
                "elevation_meters": self.datum_elevation_meters,
                "minecraft_y": self.datum_y,
            },
        }

    @staticmethod
    def from_json(data: dict) -> "GeoTransform":
        origin = data.get("origin", {})
        scale = data.get("scale", {})
        datum = data.get("vertical_datum", {})
        return GeoTransform(
            origin_x=int(origin.get("minecraft_x", 0)),
            origin_z=int(origin.get("minecraft_z", 0)),
            horizontal_meters_per_block=float(scale.get("horizontal_meters_per_block", 1.0)),
            vertical_meters_per_block=float(scale.get("vertical_meters_per_block", 1.0)),
            datum_elevation_meters=float(datum.get("elevation_meters", 0.0)),
            datum_y=int(datum.get("minecraft_y", 64)),
        )


class Projection:
    """WGS84 lat/lon -> dataset geo coordinates (meters east/north of anchor).

    Requires pyproj. The anchor is the projected point that becomes
    GeoPoint(0, 0) — e.g. downtown Beatrice for the Beatrice dataset.
    """

    def __init__(self, crs: str, anchor_lat: float, anchor_lon: float):
        import pyproj

        self.crs = crs
        self._to_crs = pyproj.Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        e, n = self._to_crs.transform(anchor_lon, anchor_lat)
        self.anchor_east = e
        self.anchor_north = n

    def to_geo(self, lat: float, lon: float) -> tuple[float, float]:
        """Returns (east_meters, north_meters) relative to the anchor."""
        e, n = self._to_crs.transform(lon, lat)
        return e - self.anchor_east, n - self.anchor_north

    def to_json(self) -> dict:
        return {
            "crs": self.crs,
            "geo_anchor_east_m": self.anchor_east,
            "geo_anchor_north_m": self.anchor_north,
        }
