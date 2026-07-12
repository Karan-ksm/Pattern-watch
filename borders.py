"""Real state border polygons + point-in-state tests.

The traffic API can only be queried with a coarse shape, so the
statewide view polls a bounding box — which spills over into neighbouring
states. This module loads the actual state outlines (generated into
state_borders.json by tools/generate_borders.py from public-domain Census
data) so the tracker can throw away aircraft that are inside the box but
outside the state.
"""

import json
from pathlib import Path

_BORDERS_FILE = Path(__file__).resolve().parent / "state_borders.json"

# name -> GeoJSON geometry dict ({"type": "Polygon"|"MultiPolygon", ...}).
# ~700 KB loaded once at import; lookups after that are pure math.
with open(_BORDERS_FILE) as f:
    _BORDERS = json.load(f)


def border_geojson(state):
    """The GeoJSON geometry for a state (for the map), or None."""
    return _BORDERS.get(state)


def _in_ring(lat, lon, ring):
    """Textbook ray-casting (even-odd) point-in-polygon test.

    Casts a horizontal ray from the point and counts how many polygon
    edges it crosses: odd = inside. GeoJSON rings are [lon, lat] pairs.
    """
    inside = False
    for i in range(len(ring)):
        lon1, lat1 = ring[i][0], ring[i][1]
        lon2, lat2 = ring[i - 1][0], ring[i - 1][1]  # wraps to close the ring
        if (lat1 > lat) != (lat2 > lat):
            # Longitude where the edge crosses our latitude.
            cross = (lon2 - lon1) * (lat - lat1) / (lat2 - lat1) + lon1
            if lon < cross:
                inside = not inside
    return inside


def point_in_state(lat, lon, state):
    """True if (lat, lon) lies inside the state's actual border.

    Unknown state -> True (fail open: better to show a plane twice than
    hide it). Holes in polygons are counted by the same even-odd rule.
    O(total vertices) per call — the 20m-simplified outlines are small,
    so a few hundred aircraft per poll is trivial.
    """
    geom = _BORDERS.get(state)
    if geom is None:
        return True

    if geom["type"] == "Polygon":
        polygons = [geom["coordinates"]]
    else:  # MultiPolygon
        polygons = geom["coordinates"]

    for polygon in polygons:
        # Ring 0 is the outer boundary; any further rings are holes.
        # Even-odd: inside an odd number of rings = inside the state.
        hits = sum(1 for ring in polygon if _in_ring(lat, lon, ring))
        if hits % 2 == 1:
            return True
    return False
