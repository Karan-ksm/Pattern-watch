"""Filtering and interpretation of raw aircraft states.

This is the "brains" of pattern-watch and also its roughest part: the
state labels are simple threshold heuristics — educated guesses based on
altitude, vertical rate, ground speed and distance from the field. They
are NOT certified logic and they will sometimes be wrong (a helicopter
hovering, a glider circling in lift, a fast twin flying a wide pattern).
Every threshold lives in config.py so tuning is a one-file job.
"""

import collections
import math
import time

import config
from borders import point_in_state

EARTH_RADIUS_NM = 3440.065

# 8-point compass, each point covering a 45 degree slice.
COMPASS_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def dist_and_bearing(lat1, lon1, lat2, lon2):
    """Great-circle distance (nm) and initial bearing (degrees true)
    from point 1 to point 2.

    Standard haversine formula — overkill-accurate for a 10 nm box, but
    it is the textbook way to do this and costs nothing.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    dist_nm = 2 * EARTH_RADIUS_NM * math.asin(math.sqrt(a))

    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return dist_nm, bearing


def compass_point(bearing):
    """Bearing in degrees -> nearest 8-point compass direction."""
    return COMPASS_POINTS[int((bearing + 22.5) // 45) % 8]


def classify(ac, airport, dist_nm):
    """Guess what an aircraft is doing near the airport.

    An ORDERED decision tree — the first matching rule wins, so more
    specific situations (on the ground) are checked before vaguer ones
    (overflight, which is really just "none of the above"). All numbers
    come from config.py.
    """
    # Missing data degrades gracefully: no vertical rate reading is
    # treated as level flight, no speed as stationary.
    speed = ac.get("speed_kt") or 0.0
    vrate = ac.get("vrate_fpm") or 0.0

    alt = ac.get("baro_alt_ft")
    agl = None if alt is None else alt - airport.elevation_ft

    # 1. On ground: either the transponder says so outright, or the
    #    aircraft is essentially at field elevation moving at taxi speed.
    #    (Barometric altitude is noisy, hence the 150 ft allowance.)
    if ac.get("on_ground"):
        return "on ground"
    if agl is not None and agl < config.GROUND_MAX_AGL_FT and speed < config.GROUND_MAX_SPEED_KT:
        return "on ground"

    # No altitude report at all -> we can't run any of the altitude-based
    # rules. Rather than guess, park it in the catch-all bucket; the
    # display still shows its position and speed.
    if agl is None:
        return "overflight (high/fast, passing through)"

    # 2. Departing: climbing steadily while still low and close to the
    #    field. The distance gate keeps us from labelling every climbing
    #    aircraft that merely clips the box as "departing".
    if (
        vrate >= config.CLIMB_FPM
        and agl < config.DEPART_MAX_AGL_FT
        and dist_nm <= config.DEPART_MAX_DIST_NM
    ):
        return "departing"

    # 3. Inbound: descending steadily, already fairly low, close enough
    #    that this field is a plausible destination. Note: it could also
    #    be descending toward a neighbouring field — we can't tell.
    if (
        vrate <= config.DESCENT_FPM
        and agl < config.INBOUND_MAX_AGL_FT
        and dist_nm <= config.INBOUND_MAX_DIST_NM
    ):
        return "inbound (descending toward field)"

    # 4. Pattern / maneuvering: roughly level (didn't match 2 or 3) at a
    #    typical pattern altitude, slow, hugging the field. Pattern
    #    altitude is usually ~1000 ft AGL; the wide 400-2000 band absorbs
    #    baro error and non-standard patterns.
    if (
        config.PATTERN_MIN_AGL_FT <= agl <= config.PATTERN_MAX_AGL_FT
        and speed <= config.PATTERN_MAX_SPEED_KT
        and dist_nm <= config.PATTERN_MAX_DIST_NM
    ):
        return "maneuvering/pattern"

    # 5. Anything left is just passing through: high, fast, far from the
    #    field, or some combination — not interacting with the airport.
    return "overflight (high/fast, passing through)"


class TrafficTracker:
    """Tracks aircraft across polls by icao24 so labels stay stable and
    we can emit "new aircraft" / "aircraft left" events.
    """

    def __init__(self, airport, watch_airports=None, statewide=False, region=None):
        self.airport = airport
        # All airports whose watch radii we compare against for the
        # "overlap" tag (aircraft inside 2+ watch areas at once).
        self.watch_airports = (
            watch_airports if watch_airports is not None else config.PRESET_AIRPORTS
        )
        # Statewide mode: no altitude ceiling, and each aircraft is
        # described relative to its NEAREST watch airport instead of one
        # fixed field ("en route" when it isn't near any of them).
        self.statewide = statewide
        # State name for the border filter. The API is polled with a
        # rectangle, so without this, statewide traffic "spills over"
        # from neighbouring states.
        self.region = region
        # icao24 -> {"ac": enriched aircraft dict, "missed": consecutive polls absent}
        self.tracks = {}
        self.events = collections.deque(maxlen=config.EVENT_LOG_SIZE)

    def update(self, raw_states):
        """Digest one poll and return (aircraft_list, events_list).

        raw_states is the poller's output: a list of aircraft dicts, or
        None if the poll failed. On a failed poll we keep the previous
        picture and do NOT age anyone out — no data is not the same as
        an empty sky.
        """
        if raw_states is None:
            return self._current(), list(self.events)

        seen = set()
        for raw in raw_states:
            ac = self._enrich(raw)
            if ac is None:  # above the ceiling — not our traffic
                continue
            icao = ac["icao24"]
            seen.add(icao)
            if icao not in self.tracks:
                # Statewide aircraft far from every watched airport have
                # no distance to report.
                where = (
                    f" ({ac['dist_nm']:.1f} nm {ac['compass']})"
                    if ac["dist_nm"] is not None
                    else ""
                )
                self._log(f"new aircraft in area: {ac['callsign']}{where}")
            self.tracks[icao] = {"ac": ac, "missed": 0}

        # Age out aircraft that didn't show up this poll. The STALE_POLLS
        # grace period stops flaky low-altitude ADS-B coverage from
        # producing constant arrive/leave flapping.
        for icao in list(self.tracks):
            if icao in seen:
                continue
            track = self.tracks[icao]
            track["missed"] += 1
            if track["missed"] >= config.STALE_POLLS:
                self._log(f"aircraft left area: {track['ac']['callsign']}")
                del self.tracks[icao]

        return self._current(), list(self.events)

    def _enrich(self, raw):
        """Attach distance, bearing, AGL and a state label to one aircraft.
        Returns None if the aircraft is above the altitude ceiling
        (single-airport mode only — statewide keeps everything).
        """
        alt = raw.get("baro_alt_ft")

        # Statewide border filter: the poll rectangle spills into
        # neighbouring states — drop anything outside the actual border.
        if self.statewide and self.region is not None:
            if not point_in_state(raw["lat"], raw["lon"], self.region):
                return None

        # Pick the reference airport: the watched field, or in statewide
        # mode whichever watch airport this aircraft is closest to. If
        # even the closest one is far away (viewing a state with no
        # configured airports in it), there is no meaningful reference —
        # the aircraft is just "en route" with no distance shown.
        ref = self.airport
        if self.statewide and self.watch_airports:
            ref = min(
                self.watch_airports,
                key=lambda ap: dist_and_bearing(ap.lat, ap.lon, raw["lat"], raw["lon"])[0],
            )
            ref_dist, _ = dist_and_bearing(ref.lat, ref.lon, raw["lat"], raw["lon"])
            if ref_dist > config.STATEWIDE_NEAR_MAX_NM:
                ac = dict(raw)
                ac["dist_nm"] = None
                ac["bearing_deg"] = None
                ac["compass"] = None
                ac["near"] = None
                ac["agl_ft"] = None
                ac["state"] = "en route"
                ac["overlaps"] = []
                return ac

        # Ceiling filter, relative to field elevation. Aircraft with no
        # altitude report are kept — better to show them than hide them.
        if (
            not self.statewide
            and alt is not None
            and alt > ref.elevation_ft + config.CEILING_AGL_FT
        ):
            return None

        dist_nm, bearing = dist_and_bearing(ref.lat, ref.lon, raw["lat"], raw["lon"])
        ac = dict(raw)
        ac["dist_nm"] = dist_nm
        ac["bearing_deg"] = bearing
        ac["compass"] = compass_point(bearing)
        ac["near"] = ref.name
        ac["agl_ft"] = None if alt is None else alt - ref.elevation_ft
        if self.statewide and dist_nm > config.BOX_RADIUS_NM:
            # Not close to any watched field: the airport heuristics don't
            # apply, it's just cruising through the state.
            ac["state"] = "en route"
        else:
            ac["state"] = classify(raw, ref, dist_nm)

        # Which OTHER airports' watch areas is this aircraft also inside?
        # (The polled box is a square, but "within BOX_RADIUS_NM" as a
        # circle is the intuitive meaning and close enough for a tag.)
        ac["overlaps"] = []
        for other in self.watch_airports:
            if other == ref:  # never list the reference airport itself
                continue
            d, _ = dist_and_bearing(other.lat, other.lon, raw["lat"], raw["lon"])
            if d <= config.BOX_RADIUS_NM:
                ac["overlaps"].append(other.name)
        return ac

    def _current(self):
        """Current aircraft, nearest to the field first. Statewide
        aircraft with no reference airport (dist_nm is None) sort last.
        """
        return sorted(
            (t["ac"] for t in self.tracks.values()),
            key=lambda ac: ac["dist_nm"] if ac["dist_nm"] is not None else float("inf"),
        )

    def _log(self, message):
        self.events.append(f"{time.strftime('%H:%M:%S')}  {message}")
