"""Output layer: terminal table renderer and the Flask web app.

Neither mode owns the data — main.py runs the poll loop and hands the
tracker's output to whichever display was requested.
"""

import threading
import time

import requests
from flask import Flask, jsonify, render_template, request

import config
from borders import border_geojson

# ANSI: clear screen + move cursor home. Repainting the whole screen each
# poll is crude but keeps us dependency-free (no curses).
CLEAR_SCREEN = "\033[2J\033[H"

EVENTS_SHOWN_IN_TERMINAL = 5


def _fmt(value, template="{:,.0f}", missing="--"):
    """Format a possibly-missing number; ADS-B fields are often null."""
    return missing if value is None else template.format(value)


def render_terminal(airport, aircraft, events, status):
    """Repaint the terminal with the current traffic picture."""
    now = time.strftime("%H:%M:%S")
    lines = [
        f"pattern-watch — {airport.name}",
        f"{now} · watching a {config.BOX_RADIUS_NM} nm box below "
        f"{config.CEILING_AGL_FT:,} ft AGL · last poll: {status}",
        "",
    ]

    if aircraft:
        lines.append(
            f"{'CALLSIGN':<10} {'ALT MSL':>8} {'AGL':>7} {'GS kt':>6} "
            f"{'V/S fpm':>8} {'FROM FIELD':>12}  STATE"
        )
        lines.append("-" * 78)
        for ac in aircraft:
            from_field = f"{ac['dist_nm']:.1f} nm {ac['compass']}"
            lines.append(
                f"{ac['callsign']:<10} "
                f"{_fmt(ac['baro_alt_ft']):>8} "
                f"{_fmt(ac['agl_ft']):>7} "
                f"{_fmt(ac['speed_kt']):>6} "
                f"{_fmt(ac['vrate_fpm'], '{:+,.0f}'):>8} "
                f"{from_field:>12}  "
                f"{ac['state']}"
            )
    else:
        lines.append("(no traffic below the ceiling right now)")

    lines.append("")
    lines.append("Recent events:")
    recent = list(events)[-EVENTS_SHOWN_IN_TERMINAL:]
    if recent:
        lines.extend(f"  {event}" for event in recent)
    else:
        lines.append("  (none yet)")
    lines.append("")
    lines.append("Ctrl-C to quit")

    print(CLEAR_SCREEN + "\n".join(lines), flush=True)


def create_web_app(shared):
    """Build the Flask app for web mode.

    `shared` is a plain dict owned by main.py: the poll thread writes the
    traffic snapshot, and the /api/airport route below writes the airport
    choice. Every access is guarded by shared["lock"]. A queue or a real
    store would be overkill for one writer and one tiny snapshot.
    """
    app = Flask(__name__)

    def _selection_payload(airport, state):
        """The fields every selection-aware response carries."""
        return dict(
            airport=airport._asdict(),
            state=state,
            statewide=airport in config.STATEWIDE_BOUNDS,
            bounds=config.STATEWIDE_BOUNDS.get(airport),
        )

    @app.route("/")
    def index():
        with shared["lock"]:
            airport = shared["airport"]
            state = shared["state"]
            by_state = shared["airports_by_state"]
        statewide = airport in config.STATEWIDE_BOUNDS
        return render_template(
            "index.html",
            airport=airport._asdict(),
            state=state,
            statewide=statewide,
            selected_name=None if statewide else airport.name,
            bounds=config.STATEWIDE_BOUNDS.get(airport),
            airports_by_state={
                s: [a._asdict() for a in airports] for s, airports in by_state.items()
            },
            poll_interval_s=config.POLL_INTERVAL_S,
            box_radius_nm=config.BOX_RADIUS_NM,
            ceiling_agl_ft=config.CEILING_AGL_FT,
        )

    @app.route("/api/traffic")
    def api_traffic():
        with shared["lock"]:
            # Mark the demo as watched so the poll loop keeps running
            # (it pauses after IDLE_AFTER_S without visitors).
            was_idle = str(shared["status"]).startswith("idle")
            shared["last_seen"] = time.time()
            response = jsonify(
                aircraft=shared["aircraft"],
                events=shared["events"],
                updated_at=shared["updated_at"],
                status=shared["status"],
                **_selection_payload(shared["airport"], shared["state"]),
            )
        if was_idle:
            shared["switch"].set()  # wake the poll loop immediately
        return response

    @app.route("/api/airport", methods=["POST"])
    def set_airport():
        """Switch view: {"state": "Texas", "name": "<airport name>" | null}.
        A null name means the whole state (statewide).
        """
        data = request.get_json(silent=True) or {}
        state = data.get("state")
        name = data.get("name")
        with shared["lock"]:
            airports = shared["airports_by_state"].get(state)
        if airports is None:
            return jsonify(error="unknown state"), 400
        if name is None:
            airport = config.STATEWIDE_SENTINELS[state]
        else:
            matches = [a for a in airports if a.name == name]
            if not matches:
                return jsonify(error="unknown airport for that state"), 400
            airport = matches[0]
        with shared["lock"]:
            shared["state"] = state
            shared["airport"] = airport
            shared["aircraft"] = []  # the old view's traffic is stale now
            shared["events"] = []
            shared["status"] = "switching..."
        shared["switch"].set()  # wake the poll loop immediately
        return jsonify(**_selection_payload(airport, state))

    @app.route("/api/health")
    def api_health():
        """Self-diagnosis for the hosted instance: is the poll thread
        alive, what does it report, and can this server reach the
        traffic API at all? Runs a live outbound request so connectivity
        problems are visible from the outside without log access.
        """
        with shared["lock"]:
            status = shared["status"]
            updated_at = shared["updated_at"]
        test_url = config.ADSBLOL_POINT_URL.format(
            lat="40.0", lon="-105.0", radius_nm="5"
        )
        t0 = time.time()
        try:
            resp = requests.get(test_url, timeout=8)
            outbound = f"HTTP {resp.status_code} in {time.time() - t0:.1f}s"
        except Exception as exc:  # diagnostic endpoint: report, never raise
            outbound = f"{type(exc).__name__}: {exc}"[:300]
        return jsonify(
            poller_thread_alive=any(
                t.name == "poller" for t in threading.enumerate()
            ),
            status=status,
            updated_at=updated_at,
            poll_interval_s=config.POLL_INTERVAL_S,
            idle_after_s=config.IDLE_AFTER_S,
            outbound_test=outbound,
        )

    @app.route("/api/border/<state>")
    def api_border(state):
        """GeoJSON geometry of a state's real border, for the map."""
        geom = border_geojson(state)
        if geom is None:
            return jsonify(error="unknown state"), 404
        return jsonify(geom)

    return app
