"""Fetch DEM rasters from the USGS National Map (TNM) public API.

Queries staged products by WGS84 bbox and downloads matching GeoTIFFs.
No authentication required; files land in datasets/raw/ (gitignored).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

TNM_API = "https://tnmaccess.nationalmap.gov/api/v1/products"
DEFAULT_DATASET = "Digital Elevation Model (DEM) 1 meter"
DEFAULT_FORMAT = "GeoTIFF"


def query_products(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
                   dataset: str = DEFAULT_DATASET, prod_format: str = DEFAULT_FORMAT,
                   max_results: int = 50) -> list[dict]:
    url = (f"{TNM_API}?bbox={min_lon},{min_lat},{max_lon},{max_lat}"
           f"&datasets={urllib.request.quote(dataset)}"
           f"&prodFormats={urllib.request.quote(prod_format)}"
           f"&max={max_results}&outputFormat=JSON")
    with urllib.request.urlopen(url, timeout=60) as resp:
        return json.load(resp).get("items", [])


def fetch_dem(min_lon: float, min_lat: float, max_lon: float, max_lat: float,
              out_dir: str | Path, dataset: str = DEFAULT_DATASET) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = query_products(min_lon, min_lat, max_lon, max_lat, dataset)
    if not items:
        raise RuntimeError(f"no TNM products found for bbox "
                           f"{min_lon},{min_lat},{max_lon},{max_lat} dataset {dataset!r}")
    written = []
    for item in items:
        url = item.get("downloadURL")
        if not url:
            continue
        dest = out / Path(url.split("?")[0]).name
        if dest.exists():
            print(f"exists, skipping: {dest.name}")
            written.append(dest)
            continue
        print(f"downloading {item.get('title', dest.name)} "
                  f"({int(item.get('sizeInBytes', 0)) / 1048576:.0f} MB)")
        urllib.request.urlretrieve(url, dest)
        written.append(dest)
    return written
