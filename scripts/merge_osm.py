"""Merge Overpass JSON responses into one file, deduping by (type, id).

    python scripts/merge_osm.py merged.json part1.json part2.json ...

Used to stitch per-region fetches (e.g. the Beatrice corridor pull plus the
Phase 11 Lincoln pull) into a single input for the compiler sources.
"""

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print("usage: merge_osm.py <out.json> <in1.json> [in2.json ...]")
        return 1
    out, *inputs = argv[1:]
    elements: dict[tuple[str, int], dict] = {}
    for path in inputs:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for el in data.get("elements", []):
            elements[(el.get("type"), el.get("id"))] = el
        print(f"{path}: {len(data.get('elements', []))} elements")
    Path(out).write_text(
        json.dumps({"version": 0.6, "generator": "merge_osm.py",
                    "elements": list(elements.values())}),
        encoding="utf-8")
    print(f"wrote {out}: {len(elements)} elements")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
