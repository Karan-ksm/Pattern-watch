"""CLI entry point for pattern-watch.

    python main.py --mode terminal
    python main.py --mode web
    python main.py --mode web --airport "39.9088,-105.1172,5673,Rocky Mountain Metro (KBJC)"
"""

import argparse
import socket
import threading
import time
import traceback

import config
from borders import point_in_state
from display import create_web_app, render_terminal
from poller import AdsbLolPoller
from traffic import TrafficTracker


def parse_airport(text):
    """Parse an --airport override: "lat,lon,elevation_ft,name".

    The name is everything after the third comma, so it may itself
    contain commas.
    """
    parts = text.split(",", 3)
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected lat,lon,elevation_ft,name")
    try:
        lat, lon, elev = float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError:
        raise argparse.ArgumentTypeError("lat, lon and elevation_ft must be numbers")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise argparse.ArgumentTypeError("lat/lon out of range")
    name = parts[3].strip() or "custom airport"
    return config.Airport(name=name, lat=lat, lon=lon, elevation_ft=elev)


def run_terminal(airport):
    """Poll forever, repainting the terminal table each cycle."""
    poller = AdsbLolPoller(airport)
    tracker = TrafficTracker(airport)
    while True:
        states = poller.fetch_states()
        aircraft, events = tracker.update(states)
        render_terminal(airport, aircraft, events, poller.status)
        time.sleep(config.POLL_INTERVAL_S)


def find_free_port(start, attempts=10):
    """Return the first free localhost port at or after `start`.

    macOS in particular likes to squat on low ports (AirPlay uses 5000),
    so instead of crashing with "address already in use" we probe by
    actually binding, which is the only reliable test.
    """
    for port in range(start, start + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                print(f"port {port} is busy, trying {port + 1}")
    raise SystemExit(f"no free port found in {start}-{start + attempts - 1}")


def find_home_state(airport):
    """Which state a custom --airport belongs to (for the picker)."""
    for state in config.US_STATE_BOUNDS:
        if point_in_state(airport.lat, airport.lon, state):
            return state
    return config.DEFAULT_STATE


def build_web_app(airport, start_polling=True):
    """Assemble web mode: per-view state, poll thread, and the Flask app.

    Used by run_web() for local dev and by wsgi.py under gunicorn. All
    state is in-memory and the poller is a single daemon thread, which is
    why the production server must run exactly ONE worker process.

    Every viewer keeps their own selection (the browser sends it as query
    params), and the server maintains one "view" — poller + tracker +
    latest snapshot — per distinct selection currently being watched.
    Views that nobody has looked at for IDLE_AFTER_S stop being polled
    and are dropped; at MAX_ACTIVE_VIEWS the stalest one is evicted.
    """
    # Airports offered per state; a custom --airport joins its own state.
    airports_by_state = {s: list(a) for s, a in config.AIRPORTS_BY_STATE.items()}
    home_state = config.DEFAULT_STATE
    if not any(airport in a for a in airports_by_state.values()):
        home_state = find_home_state(airport)
        airports_by_state[home_state].insert(0, airport)

    shared = {
        "lock": threading.Lock(),
        "airports_by_state": airports_by_state,  # never mutated after this
        "default": (home_state, airport),
        "views": {},  # (state, airport_name_or_None_for_statewide) -> view
        "wake": threading.Event(),  # set when a new view wants its first poll
    }

    def _make_view(state, view_airport):
        watch = airports_by_state[state]
        if view_airport in config.STATEWIDE_BOUNDS:
            poller = AdsbLolPoller(
                view_airport, bounds=config.STATEWIDE_BOUNDS[view_airport]
            )
            # watch_airports: that state's fields, so "nearest airport"
            # references and overlap tags work anywhere; region: filter
            # to the real state border.
            tracker = TrafficTracker(view_airport, watch_airports=watch,
                                     statewide=True, region=state)
        else:
            poller = AdsbLolPoller(view_airport)
            tracker = TrafficTracker(view_airport, watch_airports=watch)
        return {
            "state": state,
            "airport": view_airport,
            "poller": poller,
            "tracker": tracker,
            "aircraft": [],
            "events": [],
            "updated_at": None,
            "status": "waiting for first poll",
            "last_seen": time.time(),
        }

    def touch_view(state, name):
        """Get-or-create the view for (state, airport-name-or-None) and
        mark it watched now. Returns the view dict, or None if the
        selection doesn't exist. Exposed to display.py via `shared`.
        """
        airports = airports_by_state.get(state)
        if airports is None:
            return None
        if name is None:
            view_airport = config.STATEWIDE_SENTINELS[state]
        else:
            matches = [a for a in airports if a.name == name]
            if not matches:
                return None
            view_airport = matches[0]
        key = (state, name)
        with shared["lock"]:
            view = shared["views"].get(key)
            if view is None:
                views = shared["views"]
                if len(views) >= config.MAX_ACTIVE_VIEWS:
                    # Evict whichever view has gone unwatched longest.
                    stalest = min(views, key=lambda k: views[k]["last_seen"])
                    del views[stalest]
                view = _make_view(state, view_airport)
                views[key] = view
                shared["wake"].set()  # first poll now, not next cycle
            view["last_seen"] = time.time()
            return view

    shared["touch_view"] = touch_view

    def poll_loop():
        while True:
            # Never let one bad cycle kill the thread: the app would keep
            # serving pages while the maps silently froze on stale data.
            try:
                _poll_cycle()
            except Exception:
                traceback.print_exc()
                if shared["wake"].wait(timeout=config.POLL_INTERVAL_S):
                    shared["wake"].clear()

    def _poll_cycle():
        """Poll every watched view once, then sleep until the next cycle
        (waking early if a new view registers)."""
        now = time.time()
        with shared["lock"]:
            views = shared["views"]
            for key in [k for k, v in views.items()
                        if now - v["last_seen"] > config.IDLE_AFTER_S]:
                del views[key]  # nobody is watching this view anymore
            active = list(views.items())
        for key, view in active:
            t0 = time.time()
            states = view["poller"].fetch_states()
            aircraft, events = view["tracker"].update(states)
            # Heartbeat: one line per view per cycle so hosted logs show
            # what is being polled and how long the API takes to answer.
            print(
                f"[poller] {view['airport'].name}: {view['poller'].status}"
                f" ({len(aircraft)} aircraft, {time.time() - t0:.1f}s)",
                flush=True,
            )
            with shared["lock"]:
                # The view may have been evicted while we were polling.
                if shared["views"].get(key) is view:
                    view["aircraft"] = aircraft
                    view["events"] = events
                    view["updated_at"] = time.strftime("%H:%M:%S")
                    view["status"] = view["poller"].status
        if shared["wake"].wait(timeout=config.POLL_INTERVAL_S):
            shared["wake"].clear()

    app = create_web_app(shared)

    # Daemon thread: dies with the main process, no shutdown dance needed.
    # (start_polling=False exists for tests, which want the app without
    # any network activity.)
    #
    # Started lazily on the FIRST REQUEST rather than at import: some
    # gunicorn setups (Render's) import the app in the master process and
    # fork the worker afterwards, and threads do not survive a fork. A
    # thread started at import time polls happily in the master while the
    # worker serving requests holds a fork-frozen copy of `shared` — the
    # map never updates. The first request necessarily runs in the worker,
    # so starting there puts the poller in the serving process under any
    # server (gunicorn with or without preload, Flask dev server).
    if start_polling:
        start_lock = threading.Lock()

        @app.before_request
        def ensure_poller():
            if shared.get("poller_started"):
                return
            with start_lock:
                if shared.get("poller_started"):
                    return
                shared["poller_started"] = True
                print(
                    f"[poller] starting: poll every {config.POLL_INTERVAL_S}s,"
                    f" up to {config.MAX_ACTIVE_VIEWS} views, each idling"
                    f" out after {config.IDLE_AFTER_S}s unwatched",
                    flush=True,
                )
                threading.Thread(
                    target=poll_loop, name="poller", daemon=True
                ).start()

    return app


def run_web(airport):
    """Local dev: build the app and serve it with Flask's dev server."""
    app = build_web_app(airport)
    port = find_free_port(config.WEB_PORT)
    print(f"pattern-watch web mode: http://127.0.0.1:{port}")
    # Flask's dev server is fine here — single user, local demo. In
    # production, wsgi.py serves the same app through gunicorn instead.
    app.run(port=port, debug=False)


def main():
    parser = argparse.ArgumentParser(
        description="Live ADS-B traffic picture for an untowered airport."
    )
    parser.add_argument(
        "--mode",
        choices=["terminal", "web"],
        default="terminal",
        help="terminal table or Leaflet map served on localhost (default: terminal)",
    )
    parser.add_argument(
        "--airport",
        type=parse_airport,
        default=config.AIRPORT,
        metavar='"lat,lon,elevation_ft,name"',
        help=f"override the default airport ({config.AIRPORT.name})",
    )
    args = parser.parse_args()

    try:
        if args.mode == "terminal":
            run_terminal(args.airport)
        else:
            run_web(args.airport)
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
