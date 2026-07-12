"""Regenerate state_borders.json from US Census cartographic boundaries.

Usage (from the project root):

    python tools/generate_borders.py

Downloads the Census Bureau's 20m-simplified state boundary GeoJSON
(public domain, via the eric.clst.org mirror), keeps the 50 states, and
writes state_borders.json mapping state name -> GeoJSON geometry
(Polygon or MultiPolygon). ~700 KB, checked into the repo so the app
never downloads anything at runtime.

Why: the traffic API can only be polled with a coarse shape, so the
statewide view fetches a box — but a box spills into neighbouring
states. These polygons let traffic.py keep only aircraft actually inside
the selected state (and let the map draw the true border).
"""

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from states import US_STATE_BOUNDS  # noqa: E402

GEOJSON_URL = (
    "https://eric.clst.org/assets/wiki/uploads/Stuff/gz_2010_us_040_00_20m.json"
)


def main():
    print(f"downloading {GEOJSON_URL} ...")
    req = urllib.request.Request(GEOJSON_URL, headers={"User-Agent": "pattern-watch"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        collection = json.loads(resp.read().decode("utf-8", errors="replace"))

    borders = {}
    for feature in collection["features"]:
        name = feature["properties"]["NAME"]
        if name in US_STATE_BOUNDS:  # skips DC and territories
            borders[name] = feature["geometry"]

    missing = set(US_STATE_BOUNDS) - set(borders)
    if missing:
        raise SystemExit(f"missing states in source data: {missing}")

    out = Path(__file__).resolve().parent.parent / "state_borders.json"
    out.write_text(json.dumps(borders, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(borders)} states)")


if __name__ == "__main__":
    main()
