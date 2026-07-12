"""Configuration for pattern-watch.

Everything you might reasonably want to tweak lives in this file: the
airport being watched, the size of the watch box, the poll rate, and all
the thresholds used by the state heuristics in traffic.py.
"""

import os
from typing import NamedTuple

from airports_data import AIRPORTS_BY_STATE as _GENERATED_AIRPORTS
from states import US_STATE_BOUNDS


class Airport(NamedTuple):
    """A point on the map we care about, plus its field elevation."""

    name: str
    lat: float
    lon: float
    elevation_ft: float


# Default airport: Erie Municipal (KEIK), an untowered field just north
# of Denver, CO. Override on the command line with --airport.
AIRPORT = Airport(
    name="Erie Municipal (KEIK)",
    lat=40.0102,
    lon=-105.0481,
    elevation_ft=5130,
)

# Hand-curated Colorado airports (the original demo set). These override
# the generated list for Colorado below — curation beats mechanics.
PRESET_AIRPORTS = [
    AIRPORT,
    Airport(name="Vance Brand / Longmont (KLMO)", lat=40.1637, lon=-105.1633, elevation_ft=5055),
    Airport(name="Boulder Municipal (KBDU)", lat=40.0394, lon=-105.2258, elevation_ft=5288),
    Airport(name="Rocky Mountain Metro (KBJC)", lat=39.9088, lon=-105.1172, elevation_ft=5673),
    Airport(name="Centennial (KAPA)", lat=39.5701, lon=-104.8493, elevation_ft=5885),
    Airport(name="Northern Colorado Rgnl (KFNL)", lat=40.4518, lon=-105.0113, elevation_ft=5016),
]

# GA airports for every state: generated from the public-domain OurAirports
# dataset (see tools/generate_airports.py to regenerate). Colorado gets the
# curated list first, then generated fields that aren't already in it
# (matched by their "(IDENT)"), so curation orders the list without
# shrinking it.
AIRPORTS_BY_STATE = {
    state: [Airport(name=n, lat=lat, lon=lon, elevation_ft=elev)
            for n, lat, lon, elev in entries]
    for state, entries in _GENERATED_AIRPORTS.items()
}


def _ident(airport):
    """The "(KEIK)"-style code at the end of an airport name."""
    return airport.name.rsplit("(", 1)[-1].rstrip(")")


_curated_idents = {_ident(a) for a in PRESET_AIRPORTS}
AIRPORTS_BY_STATE["Colorado"] = (PRESET_AIRPORTS + [
    a for a in AIRPORTS_BY_STATE["Colorado"] if _ident(a) not in _curated_idents
])[:25]

DEFAULT_STATE = "Colorado"

# Statewide view: every US state gets a sentinel "airport" whose lat/lon
# is just the centre of its bounding box (used for map centring), polled
# with the state's bounds from states.py instead of a 10 nm box.
# Heads-up: a state-sized box means a much bigger query and payload per
# call than a small one; if the API ever rate-limits us (429) the poller
# skips those cycles gracefully.
STATEWIDE_SENTINELS = {
    name: Airport(
        name=f"{name} ({abbr}) — statewide",
        lat=(lamin + lamax) / 2,
        lon=(lomin + lomax) / 2,
        elevation_ft=0,
    )
    for name, (abbr, lamin, lomin, lamax, lomax) in US_STATE_BOUNDS.items()
}
STATEWIDE_BOUNDS = {
    STATEWIDE_SENTINELS[name]: (lamin, lomin, lamax, lomax)
    for name, (abbr, lamin, lomin, lamax, lomax) in US_STATE_BOUNDS.items()
}
STATEWIDE_OPTIONS = list(STATEWIDE_BOUNDS)  # alphabetical

# In statewide mode, an aircraft farther than this from every watched
# airport is not associated with any field (labelled "en route" with no
# distance shown) — otherwise viewing e.g. Texas with Colorado presets
# would show nonsense like "580 nm NE of KFNL".
STATEWIDE_NEAR_MAX_NM = 50

# --- Watch area ------------------------------------------------------------

# Half-width of the square bounding box around the airport, in nautical
# miles. ~10 nm covers the traffic pattern plus anyone about to enter it.
BOX_RADIUS_NM = 10

# Ignore aircraft more than this many feet ABOVE THE FIELD. This is
# deliberately relative to field elevation (i.e. AGL) rather than a fixed
# MSL number, so it still makes sense at a high-elevation airport like
# KEIK (~5,130 ft MSL) without retuning.
CEILING_AGL_FT = 3500

# --- Polling ----------------------------------------------------------------

# Overridable via environment so a hosted demo can poll more slowly
# (being polite to the free API) without code edits.
POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", 15))

# Web mode: stop polling a view (a state/airport selection) when nobody
# has watched it for this long. Matters most for a deployed demo — no
# reason to keep fetching a picture nobody is looking at.
IDLE_AFTER_S = 300

# Web mode: every viewer gets their own view, so this caps how many
# distinct selections we poll concurrently. At the cap, the view that
# has gone unwatched longest is evicted for the newcomer.
MAX_ACTIVE_VIEWS = 12

# An aircraft missing from this many consecutive polls is considered to
# have left the area. ADS-B low-altitude coverage flickers, so we do
# not drop anyone on a single missed poll.
STALE_POLLS = 3

# --- State heuristic thresholds (used by traffic.classify) ------------------
# All rough approximations; see traffic.py for how each one is applied.

GROUND_MAX_AGL_FT = 150     # at/below this AND slow -> probably on the ground
GROUND_MAX_SPEED_KT = 40    # taxi-ish speed

CLIMB_FPM = 400             # climbing at least this fast -> maybe departing
DEPART_MAX_AGL_FT = 2000    # ...while still low
DEPART_MAX_DIST_NM = 5      # ...and still close to the field

DESCENT_FPM = -300          # descending at least this fast -> maybe inbound
INBOUND_MAX_AGL_FT = 2500   # ...while already fairly low
INBOUND_MAX_DIST_NM = 7     # ...and close enough that the field is plausible

PATTERN_MIN_AGL_FT = 400    # typical pattern altitude band (roughly level)
PATTERN_MAX_AGL_FT = 2000
PATTERN_MAX_SPEED_KT = 130  # pattern speeds are slow; airliners never fit
PATTERN_MAX_DIST_NM = 4     # the pattern hugs the field

# --- ADS-B data providers ----------------------------------------------------

# Free community ADS-B aggregators (readsb JSON, no auth), tried in
# order. All serve the same data; when one rate-limits this IP — hosted
# instances share egress IPs, so limits arrive through no fault of ours
# — the next provider takes over and the map never goes stale. (We
# moved off OpenSky entirely: it silently drops connections from cloud
# datacenter IPs.) Query shape: aircraft within {radius_nm} nautical
# miles of a point.
ADSB_PROVIDERS = [
    ("adsb.lol", "https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}"),
    ("adsb.fi",
     "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{radius_nm}"),
    ("airplanes.live", "https://api.airplanes.live/v2/point/{lat}/{lon}/{radius_nm}"),
]

# --- Display -----------------------------------------------------------------

EVENT_LOG_SIZE = 30  # how many recent arrive/leave events to keep
# Local dev port (not 5000: macOS AirPlay squats on it). In production
# gunicorn binds the port itself, so this is dev-only.
WEB_PORT = int(os.environ.get("WEB_PORT", 5050))
