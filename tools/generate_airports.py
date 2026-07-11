"""Regenerate airports_data.py from the OurAirports public-domain dataset.

Usage (from the project root):

    python tools/generate_airports.py

Downloads the full OurAirports airports.csv (~13 MB), keeps US general-
aviation fields — small/medium airports with a real ICAO-style ident
(K***, or PA**/PH** for Alaska/Hawaii) and known coordinates/elevation —
ranks them per state, and writes the top few per state to
airports_data.py. The picks are mechanical, not curated: airports with a
Wikipedia article rank first (a rough notability signal), then medium
fields before small ones, then alphabetical.

Run it once and commit the output; the app never downloads anything at
runtime.
"""

import csv
import io
import re
import sys
import time
import urllib.request
from pathlib import Path

# Import the state table from the project root (this script lives in tools/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from borders import point_in_state  # noqa: E402
from states import US_STATE_BOUNDS  # noqa: E402

CSV_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
PER_STATE = 25
IDENT_RE = re.compile(r"^(K[A-Z0-9]{3}|PA[A-Z0-9]{2}|PH[A-Z0-9]{2})$")

# Military fields have Wikipedia pages (so they rank well) but are the
# opposite of watchable GA airports — exclude by name.
MILITARY_RE = re.compile(
    r"air force|space force|\bafb\b|\barmy\b|\bnaval\b|navy|marine corps|"
    r"coast guard|national guard|air station|joint base|\bnas\b|\bmcas\b",
    re.IGNORECASE,
)

ABBR_TO_STATE = {abbr: name for name, (abbr, *_) in US_STATE_BOUNDS.items()}


def fetch_rows():
    print(f"downloading {CSV_URL} ...")
    with urllib.request.urlopen(CSV_URL, timeout=60) as resp:
        text = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def pick_airports(rows):
    by_state = {name: [] for name in US_STATE_BOUNDS}
    for row in rows:
        if row["iso_country"] != "US":
            continue
        if row["type"] not in ("small_airport", "medium_airport"):
            continue
        if not IDENT_RE.match(row["ident"]):
            continue
        if MILITARY_RE.search(row["name"]):
            continue
        state = ABBR_TO_STATE.get(row["iso_region"].removeprefix("US-"))
        if state is None:  # DC, territories
            continue
        try:
            lat = float(row["latitude_deg"])
            lon = float(row["longitude_deg"])
            elev = float(row["elevation_ft"])
        except (ValueError, KeyError):
            continue  # no usable position/elevation
        # Keep only airports inside the state's actual border polygon —
        # the same test traffic.py applies to aircraft. Drops far-flung
        # outliers like French Frigate Shoals (500 mi NW of Honolulu).
        if not point_in_state(lat, lon, state):
            continue
        by_state[state].append({
            "name": f'{row["name"]} ({row["ident"]})',
            "lat": round(lat, 4),
            "lon": round(lon, 4),
            "elev": round(elev),
            # Ranking signals: Wikipedia article ~ notability; medium
            # fields tend to be busier than small ones.
            "rank": (
                0 if row["wikipedia_link"].strip() else 1,
                0 if row["type"] == "medium_airport" else 1,
                row["name"],
            ),
        })
    for state, airports in by_state.items():
        airports.sort(key=lambda a: a["rank"])
        by_state[state] = airports[:PER_STATE]
    return by_state


def write_module(by_state, path):
    lines = [
        '"""GA airports per US state — GENERATED FILE, do not hand-edit.',
        "",
        "Regenerate with:  python tools/generate_airports.py",
        f"Source: OurAirports (public domain), fetched {time.strftime('%Y-%m-%d')}.",
        "Each entry: (name incl. ident, lat, lon, elevation_ft).",
        '"""',
        "",
        "AIRPORTS_BY_STATE = {",
    ]
    for state in sorted(by_state):
        lines.append(f'    "{state}": [')
        for a in by_state[state]:
            name = a["name"].replace('"', "'")
            lines.append(
                f'        ("{name}", {a["lat"]}, {a["lon"]}, {a["elev"]}),'
            )
        lines.append("    ],")
    lines.append("}")
    path.write_text("\n".join(lines) + "\n")
    total = sum(len(v) for v in by_state.values())
    print(f"wrote {path} ({total} airports across {len(by_state)} states)")


if __name__ == "__main__":
    rows = fetch_rows()
    by_state = pick_airports(rows)
    empty = [s for s, v in by_state.items() if not v]
    if empty:
        print(f"WARNING: no airports found for: {', '.join(empty)}")
    write_module(by_state, Path(__file__).resolve().parent.parent / "airports_data.py")
