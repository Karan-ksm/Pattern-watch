"""OpenSky Network REST client.

Fetches aircraft state vectors inside a lat/lon bounding box around the
airport. Handles optional OAuth2 authentication, rate limiting (HTTP 429)
and the several ways the API can return nothing. The contract with the
rest of the program: a bad poll NEVER raises — it just returns None so
the caller can skip that cycle and try again next time.
"""

import math
import os
import time

import requests

import config

# Unit conversions. OpenSky reports metres and metres/second, but
# aviation thinks in feet, knots and feet-per-minute, so we convert once
# here at the boundary and the rest of the code never sees metric.
M_TO_FT = 3.28084
MS_TO_KT = 1.94384
MS_TO_FPM = 196.850


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


class OpenSkyPoller:
    """Polls /states/all for one airport's bounding box."""

    def __init__(self, airport, bounds=None):
        """Poll the airport's 10 nm box, or explicit `bounds`
        (lamin, lomin, lamax, lomax) — used for the statewide view.
        """
        self.airport = airport
        self.box = bounds if bounds is not None else bounding_box(airport, config.BOX_RADIUS_NM)
        self.client_id = os.environ.get("OPENSKY_CLIENT_ID")
        self.client_secret = os.environ.get("OPENSKY_CLIENT_SECRET")
        self._token = None
        self._token_expiry = 0.0
        self._auth_broken = False  # set after one failed auth; stops retry spam
        # Human-readable result of the most recent poll, for the displays.
        self.status = "not polled yet"

    # --- auth ---------------------------------------------------------------

    def _get_token(self):
        """Return a bearer token, or None to poll anonymously.

        Tokens are cached and refreshed a minute before they expire. If
        auth fails (bad credentials, auth server down) we warn once and
        permanently fall back to anonymous mode rather than crashing.
        """
        if not (self.client_id and self.client_secret) or self._auth_broken:
            return None
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        try:
            resp = requests.post(
                config.OPENSKY_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                timeout=10,
            )
            resp.raise_for_status()
            payload = resp.json()
            self._token = payload["access_token"]
            self._token_expiry = time.time() + payload.get("expires_in", 1800)
            return self._token
        except (requests.RequestException, KeyError, ValueError):
            print("[poller] OpenSky auth failed - continuing anonymously")
            self._auth_broken = True
            return None

    # --- polling ------------------------------------------------------------

    def fetch_states(self):
        """One poll. Returns a list of aircraft dicts, or None on failure.

        None means "we learned nothing this cycle" (network error, rate
        limit, bad payload); an empty list means "the sky really is empty".
        The distinction matters to the tracker: it should not age out
        aircraft just because a poll failed.
        """
        headers = {}
        token = self._get_token()
        if token:
            headers["Authorization"] = "Bearer " + token

        lamin, lomin, lamax, lomax = self.box
        params = {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax}

        try:
            resp = requests.get(
                config.OPENSKY_STATES_URL, params=params, headers=headers, timeout=10
            )
        except requests.RequestException as exc:
            self.status = f"poll failed: {exc.__class__.__name__}"
            return None

        if resp.status_code == 429:
            # OpenSky tells us how long to wait; we just skip cycles until then.
            retry = resp.headers.get("X-Rate-Limit-Retry-After-Seconds", "?")
            self.status = f"rate limited (retry after {retry}s)"
            return None
        if resp.status_code != 200:
            self.status = f"poll failed: HTTP {resp.status_code}"
            return None

        try:
            payload = resp.json()
        except ValueError:
            self.status = "poll failed: bad JSON"
            return None

        # OpenSky returns {"states": null} when the box is empty.
        raw_states = payload.get("states") or []
        aircraft = [ac for ac in (self._parse(s) for s in raw_states) if ac]
        self.status = "poll OK"
        return aircraft

    @staticmethod
    def _parse(vector):
        """Turn one positional state vector into a readable dict.

        OpenSky state vectors are plain arrays; the indices used below are
        from their API docs (0=icao24, 1=callsign, 5=lon, 6=lat, 7=baro
        altitude m, 8=on_ground, 9=velocity m/s, 10=true track deg,
        11=vertical rate m/s). Any field can be null. Returns None if the
        vector is unusable (no position).
        """

        def conv(value, factor):
            return None if value is None else value * factor

        try:
            lon, lat = vector[5], vector[6]
        except (IndexError, TypeError):
            return None
        if lat is None or lon is None:
            return None

        callsign = (vector[1] or "").strip()
        return {
            "icao24": vector[0],
            "callsign": callsign or vector[0],  # fall back to the hex address
            "lat": lat,
            "lon": lon,
            "baro_alt_ft": conv(vector[7], M_TO_FT),
            "on_ground": bool(vector[8]),
            "speed_kt": conv(vector[9], MS_TO_KT),
            "track_deg": vector[10],
            "vrate_fpm": conv(vector[11], MS_TO_FPM),
        }
