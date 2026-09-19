"""GeoWorld compiler: offline GIS -> .geoworld dataset pipeline.

The Minecraft mod never sees GIS formats. This package turns DEMs, vectors and
hand metadata into tiled binary datasets (see format/README.md) that the mod
consumes as plain array lookups.
"""

__version__ = "0.1.0"
