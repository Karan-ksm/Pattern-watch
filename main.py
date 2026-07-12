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
    """Assemble web mode: shared state, poll thread, and the Flask app.

    Used by run_web() for local dev and by wsgi.py under gunicorn. All
    state is in-memory and the poller is a single daemon thread, which is
    why the production server must run exactly ONE worker process.

    The web UI picks a state, then an airport in it (or the whole state).
    The Flask side writes the choice into shared["state"]/["airport"] and
    sets the "switch" event; the poll loop rebuilds its poller/tracker.
    """
    # Airports offered per state; a custom --airport joins its own state.
    airports_by_state = {s: list(a) for s, a in config.AIRPORTS_BY_STATE.items()}
    home_state = config.DEFAULT_STATE
    if not any(airport in a for a in airports_by_state.values()):
        home_state = find_home_state(airport)
        airports_by_state[home_state].insert(0, airport)

    shared = {
        "lock": threading.Lock(),
        "state": home_state,
        "airport": airport,
        "airports_by_state": airports_by_state,
        "switch": threading.Event(),
        "aircraft": [],
        "events": [],
        "updated_at": None,
        "status": "waiting for first poll",
        "last_seen": time.time(),  # when someone last fetched /api/traffic
    }

    def poll_loop():
        current = None
        poller = tracker = None
        while True:
            # Never let one bad cycle kill the thread: the app would keep
            # serving pages while the map silently froze on stale data.
            try:
                current, poller, tracker = _poll_once(current, poller, tracker)
            except Exception as exc:
                traceback.print_exc()
                with shared["lock"]:
                    shared["status"] = f"poller error: {type(exc).__name__}"
                if shared["switch"].wait(timeout=config.POLL_INTERVAL_S):
                    shared["switch"].clear()

    def _poll_once(current, poller, tracker):
        """One iteration of the poll loop; returns the (possibly rebuilt)
        (current, poller, tracker) for the next cycle."""
        # Idle pause: nobody has looked at the page recently, so
        # don't hammer the API for a picture nobody sees.
        # /api/traffic wakes us instantly via the switch event.
        with shared["lock"]:
            idle = time.time() - shared["last_seen"] > config.IDLE_AFTER_S
            if idle:
                shared["status"] = "idle - open the page to resume"
        if idle:
            if shared["switch"].wait(timeout=config.POLL_INTERVAL_S):
                shared["switch"].clear()
            return current, poller, tracker

        with shared["lock"]:
            wanted = shared["airport"]
            state = shared["state"]
            watch = shared["airports_by_state"][state]
        if wanted != current:
            current = wanted
            if current in config.STATEWIDE_BOUNDS:
                poller = AdsbLolPoller(
                    current, bounds=config.STATEWIDE_BOUNDS[current]
                )
                # watch_airports: that state's fields, so "nearest
                # airport" references and overlap tags work anywhere;
                # region: filter to the real state border.
                tracker = TrafficTracker(current, watch_airports=watch,
                                         statewide=True, region=state)
            else:
                poller = AdsbLolPoller(current)
                tracker = TrafficTracker(current, watch_airports=watch)
        t0 = time.time()
        states = poller.fetch_states()
        aircraft, events = tracker.update(states)
        # Heartbeat: one line per cycle so hosted logs show whether the
        # poller is alive and how long the API takes to answer.
        print(
            f"[poller] {current.name}: {poller.status}"
            f" ({len(aircraft)} aircraft, {time.time() - t0:.1f}s)",
            flush=True,
        )
        with shared["lock"]:
            # Drop results that raced with an airport switch.
            if shared["airport"] == current:
                shared["aircraft"] = aircraft
                shared["events"] = events
                shared["updated_at"] = time.strftime("%H:%M:%S")
                shared["status"] = poller.status
        # Sleep until the next poll, but wake early on a switch so
        # the new airport doesn't wait a full interval for data.
        if shared["switch"].wait(timeout=config.POLL_INTERVAL_S):
            shared["switch"].clear()
        return current, poller, tracker

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
                    f" idle after {config.IDLE_AFTER_S}s without visitors",
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
