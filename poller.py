"""adsb.lol REST client.

Fetches aircraft inside a lat/lon bounding box around the airport, using
the free community adsb.lol aggregator (readsb JSON, no auth). We moved
off OpenSky because its servers silently drop connections from cloud
datacenter IPs, which broke the hosted deployment. The contract with the
rest of the program: a bad poll NEVER raises — it just returns None so
the caller can skip that cycle and try again next time.
"""

import math
import time

import requests

import config

# adsb.lol can only be queried as a point + radius circle, so we ask for
# the circle that circumscribes our box and filter the result back down
# to the box. The API caps the radius; state-sized boxes bigger than
# that lose their far corners (the border filter hides the spill-over
# anyway, so in practice only the largest states are affected).
MAX_RADIUS_NM = 250

# Self-throttling, shared by ALL pollers in the process (module-level:
# per-viewer views mean several pollers, but adsb.lol rate-limits the
# IP, so the budget is global). Requests are spaced at least
# MIN_REQUEST_GAP_S apart, and a 429/420 pauses everything until the
# server's Retry-After (default/cap below) has passed. Hosted instances
# share egress IPs with other tenants, so limits can arrive regardless.
MIN_REQUEST_GAP_S = 2.0
BACKOFF_DEFAULT_S = 60
BACKOFF_MAX_S = 300
_next_request_at = 0.0
_backoff_until = 0.0


def bounding_box(airport, radius_nm):
    """Square box around the airport as (lamin, lomin, lamax, lomax).

    One nautical mile is 1/60 of a degree of latitude by definition.
    Degrees of longitude shrink as you move away from the equator, so the
    east-west span is stretched by 1/cos(lat) to stay ~radius_nm wide.
    """
    lat_span = radius_nm / 60.0
    lon_span = radius_nm / (60.0 * math.cos(math.radians(airport.lat)))
    return (
        airport.lat - lat_span,
        airport.lon - lon_span,
        airport.lat + lat_span,
        airport.lon + lon_span,
    )


class AdsbLolPoller:
    """Polls adsb.lol for one airport's bounding box."""

    def __init__(self, airport, bounds=None):
        """Poll the airport's 10 nm box, or explicit `bounds`
        (lamin, lomin, lamax, lomax) — used for the statewide view.
        """
        self.airport = airport
        self.box = bounds if bounds is not None else bounding_box(airport, config.BOX_RADIUS_NM)
        lamin, lomin, lamax, lomax = self.box
        self.center_lat = (lamin + lamax) / 2
        self.center_lon = (lomin + lomax) / 2
        half_ns_nm = (lamax - lamin) / 2 * 60.0
        half_ew_nm = (lomax - lomin) / 2 * 60.0 * math.cos(math.radians(self.center_lat))
        radius = math.hypot(half_ns_nm, half_ew_nm)
        if radius > MAX_RADIUS_NM:
            print(
                f"[poller] {airport.name}: box needs a {radius:.0f} nm query"
                f" radius, capped at {MAX_RADIUS_NM} nm - far corners of the"
                f" box are not covered",
                flush=True,
            )
        self.radius_nm = min(radius, MAX_RADIUS_NM)
        # Human-readable result of the most recent poll, for the displays.
        self.status = "not polled yet"

    # --- polling ------------------------------------------------------------

    def fetch_states(self):
        """One poll. Returns a list of aircraft dicts, or None on failure.

        None means "we learned nothing this cycle" (network error, rate
        limit, bad payload); an empty list means "the sky really is empty".
        The distinction matters to the tracker: it should not age out
        aircraft just because a poll failed.
        """
        global _next_request_at, _backoff_until

        now = time.time()
        if now < _backoff_until:
            self.status = f"rate limited (retrying in {int(_backoff_until - now)}s)"
            return None
        # Space requests out; all pollers run on the one poll thread, so
        # a plain sleep here throttles the whole process.
        if _next_request_at > now:
            time.sleep(_next_request_at - now)
        _next_request_at = time.time() + MIN_REQUEST_GAP_S

        # Radius rounds UP: truncating 14.14 to 14 would leave the box
        # corners just outside the queried circle.
        url = config.ADSBLOL_POINT_URL.format(
            lat=f"{self.center_lat:.4f}",
            lon=f"{self.center_lon:.4f}",
            radius_nm=math.ceil(self.radius_nm),
        )
        try:
            resp = requests.get(url, timeout=10)
        except requests.RequestException as exc:
            self.status = f"poll failed: {exc.__class__.__name__}"
            return None

        # 429 is the standard too-many-requests answer; 420 is a
        # rate-limit variant some deployments use. Honour Retry-After
        # when the server sends one, within sane bounds.
        if resp.status_code in (429, 420):
            retry = resp.headers.get("Retry-After", "")
            delay = int(retry) if retry.isdigit() else BACKOFF_DEFAULT_S
            _backoff_until = time.time() + min(delay, BACKOFF_MAX_S)
            self.status = "rate limited"
            return None
        if resp.status_code != 200:
            self.status = f"poll failed: HTTP {resp.status_code}"
            return None

        try:
            payload = resp.json()
        except ValueError:
            self.status = "poll failed: bad JSON"
            return None

        raw_states = payload.get("ac") or []
        aircraft = [ac for ac in (self._parse(a) for a in raw_states) if ac]
        self.status = "poll OK"
        return aircraft

    def _parse(self, ac):
        """Turn one readsb-style aircraft dict into our readable dict.

        adsb.lol already reports aviation units (feet, knots, ft/min).
        `alt_baro` is the number of feet OR the literal string "ground".
        Any field can be missing. Returns None if the aircraft is
        unusable (no position or no hex address — the tracker keys on
        the hex, so keyless aircraft would collide) or falls outside our
        box — the query is a circle around the box, so it spills over.
        """
        lat, lon = ac.get("lat"), ac.get("lon")
        if lat is None or lon is None:
            return None
        hexid = (ac.get("hex") or "").strip()
        if not hexid:
            return None
        lamin, lomin, lamax, lomax = self.box
        if not (lamin <= lat <= lamax and lomin <= lon <= lomax):
            return None

        alt = ac.get("alt_baro")
        on_ground = alt == "ground"
        if not isinstance(alt, (int, float)):
            alt = None

        callsign = (ac.get("flight") or "").strip()
        vrate = ac.get("baro_rate")
        if vrate is None:
            vrate = ac.get("geom_rate")
        return {
            "icao24": hexid,
            "callsign": callsign or hexid,  # fall back to the hex address
            "lat": lat,
            "lon": lon,
            "baro_alt_ft": alt,
            "on_ground": on_ground,
            "speed_kt": ac.get("gs"),
            "track_deg": ac.get("track"),
            "vrate_fpm": vrate,
        }
