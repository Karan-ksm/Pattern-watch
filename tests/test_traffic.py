"""Unit tests for traffic.py — geometry, state heuristics, and tracking.

No network anywhere: every test hand-builds the aircraft dicts that
poller.py would normally produce. This makes tuning the heuristics safe:
change a threshold in config.py, run pytest, see exactly which
classifications moved.
"""

import re

import config
from borders import border_geojson, point_in_state
from traffic import TrafficTracker, classify, compass_point, dist_and_bearing

# A test airport at a round 5,000 ft so AGL math is easy to eyeball.
AIRPORT = config.Airport(name="Test Field", lat=40.0, lon=-105.0, elevation_ft=5000)


def make_ac(**overrides):
    """A plausible default aircraft; each test tweaks only what it cares
    about. Defaults: 1,000 ft AGL, 100 kt, level, right over the field.
    """
    ac = {
        "icao24": "abc123",
        "callsign": "N123AB",
        "lat": 40.0,
        "lon": -105.0,
        "baro_alt_ft": 6000.0,
        "on_ground": False,
        "speed_kt": 100.0,
        "track_deg": 90.0,
        "vrate_fpm": 0.0,
    }
    ac.update(overrides)
    return ac


# --- geometry ---------------------------------------------------------------


def test_one_degree_of_latitude_is_sixty_nm():
    dist, bearing = dist_and_bearing(40.0, -105.0, 41.0, -105.0)
    assert abs(dist - 60.0) < 0.5
    assert bearing < 1.0 or bearing > 359.0  # due north


def test_bearing_due_east():
    _, bearing = dist_and_bearing(40.0, -105.0, 40.0, -104.0)
    assert 89.0 <= bearing <= 91.0


def test_compass_points():
    assert compass_point(0) == "N"
    assert compass_point(45) == "NE"
    assert compass_point(200) == "S"
    assert compass_point(350) == "N"


# --- state heuristics ---------------------------------------------------------


def test_on_ground_flag_wins():
    ac = make_ac(on_ground=True, speed_kt=200.0)  # speed shouldn't matter
    assert classify(ac, AIRPORT, dist_nm=0.5) == "on ground"


def test_low_and_slow_is_on_ground():
    # 100 ft AGL at 30 kt: baro says airborne-ish, but that's just noise.
    ac = make_ac(baro_alt_ft=5100.0, speed_kt=30.0)
    assert classify(ac, AIRPORT, dist_nm=0.2) == "on ground"


def test_climbing_low_and_close_is_departing():
    ac = make_ac(baro_alt_ft=5800.0, vrate_fpm=700.0)
    assert classify(ac, AIRPORT, dist_nm=1.5) == "departing"


def test_descending_low_and_close_is_inbound():
    ac = make_ac(baro_alt_ft=6200.0, vrate_fpm=-500.0)
    assert classify(ac, AIRPORT, dist_nm=4.0) == "inbound (descending toward field)"


def test_level_slow_and_close_is_pattern():
    ac = make_ac()  # defaults: 1,000 ft AGL, 100 kt, level
    assert classify(ac, AIRPORT, dist_nm=1.0) == "maneuvering/pattern"


def test_high_and_fast_is_overflight():
    ac = make_ac(baro_alt_ft=8200.0, speed_kt=180.0)
    assert (
        classify(ac, AIRPORT, dist_nm=6.0)
        == "overflight (high/fast, passing through)"
    )


def test_climbing_but_far_away_is_not_departing():
    # Distance gates matter: a climber 8 nm out is just passing through.
    ac = make_ac(vrate_fpm=800.0)
    assert (
        classify(ac, AIRPORT, dist_nm=8.0)
        == "overflight (high/fast, passing through)"
    )


def test_missing_vertical_rate_treated_as_level():
    ac = make_ac(vrate_fpm=None)
    assert classify(ac, AIRPORT, dist_nm=1.0) == "maneuvering/pattern"


def test_missing_altitude_does_not_crash():
    ac = make_ac(baro_alt_ft=None)
    assert (
        classify(ac, AIRPORT, dist_nm=2.0)
        == "overflight (high/fast, passing through)"
    )


# --- tracker -------------------------------------------------------------------


def test_new_aircraft_emits_event():
    tracker = TrafficTracker(AIRPORT)
    aircraft, events = tracker.update([make_ac()])
    assert len(aircraft) == 1
    assert any("new aircraft in area: N123AB" in e for e in events)


def test_no_duplicate_new_event_across_polls():
    tracker = TrafficTracker(AIRPORT)
    tracker.update([make_ac()])
    _, events = tracker.update([make_ac()])
    assert sum("new aircraft" in e for e in events) == 1


def test_above_ceiling_is_filtered_out():
    tracker = TrafficTracker(AIRPORT)
    # 4,000 ft AGL over a 3,500 ft ceiling -> not our traffic.
    aircraft, _ = tracker.update([make_ac(baro_alt_ft=9000.0)])
    assert aircraft == []


def test_left_area_after_stale_polls():
    tracker = TrafficTracker(AIRPORT)
    tracker.update([make_ac()])
    for _ in range(config.STALE_POLLS):
        aircraft, events = tracker.update([])
    assert aircraft == []
    assert any("aircraft left area: N123AB" in e for e in events)


def test_overlap_detected_between_close_airports():
    # Watch Erie (KEIK) with the real preset list: Boulder Municipal is
    # ~8 nm away, so an aircraft over Erie sits in both watch areas.
    keik = config.PRESET_AIRPORTS[0]
    tracker = TrafficTracker(keik)
    ac = make_ac(lat=keik.lat, lon=keik.lon,
                 baro_alt_ft=keik.elevation_ft + 1000)
    aircraft, _ = tracker.update([ac])
    overlaps = aircraft[0]["overlaps"]
    assert any("KBDU" in name for name in overlaps)
    assert keik.name not in overlaps  # never lists the watched airport


def test_no_overlap_at_isolated_airport():
    # Centennial (KAPA) is ~20+ nm from every other preset, so an
    # aircraft overhead is in exactly one watch area.
    kapa = next(a for a in config.PRESET_AIRPORTS if "KAPA" in a.name)
    tracker = TrafficTracker(kapa)
    ac = make_ac(lat=kapa.lat, lon=kapa.lon,
                 baro_alt_ft=kapa.elevation_ft + 1000)
    aircraft, _ = tracker.update([ac])
    assert aircraft[0]["overlaps"] == []


def _statewide_sentinel(state_name):
    """The dropdown sentinel Airport for a state, e.g. 'Colorado'."""
    return next(a for a in config.STATEWIDE_OPTIONS if a.name.startswith(state_name))


def test_statewide_classifies_against_nearest_airport():
    # Statewide mode: an aircraft in the pattern at Erie should still get
    # Erie's heuristics, referenced to Erie ("near"), not the state centre.
    keik = config.PRESET_AIRPORTS[0]
    tracker = TrafficTracker(_statewide_sentinel("Colorado"), statewide=True)
    ac = make_ac(lat=keik.lat, lon=keik.lon,
                 baro_alt_ft=keik.elevation_ft + 1000, speed_kt=95.0)
    aircraft, _ = tracker.update([ac])
    assert aircraft[0]["near"] == keik.name
    assert aircraft[0]["state"] == "maneuvering/pattern"
    assert keik.name not in aircraft[0]["overlaps"]


def test_statewide_keeps_high_cruisers_as_en_route():
    # Mid-state at FL350: far from every preset airport and way above any
    # ceiling — statewide mode keeps it, calls it "en route", and doesn't
    # pretend a Front Range airport 100+ nm away is a useful reference.
    tracker = TrafficTracker(_statewide_sentinel("Colorado"), statewide=True)
    ac = make_ac(lat=38.5, lon=-106.5, baro_alt_ft=35000.0, speed_kt=450.0)
    aircraft, _ = tracker.update([ac])
    assert len(aircraft) == 1
    assert aircraft[0]["state"] == "en route"
    assert aircraft[0]["near"] is None
    assert aircraft[0]["dist_nm"] is None


def test_statewide_keeps_reference_inside_near_cutoff():
    # (40.0, -104.3) is ~35 nm east of every Front Range preset: outside
    # the 10 nm heuristics gate but inside STATEWIDE_NEAR_MAX_NM — so it's
    # "en route" yet still referenced to the nearest field for the
    # "X nm of KEIK" display.
    tracker = TrafficTracker(_statewide_sentinel("Colorado"), statewide=True)
    ac = make_ac(lat=40.0, lon=-104.3, baro_alt_ft=9000.0)
    aircraft, _ = tracker.update([ac])
    assert aircraft[0]["state"] == "en route"
    assert aircraft[0]["near"] is not None
    assert 10 < aircraft[0]["dist_nm"] <= config.STATEWIDE_NEAR_MAX_NM


def test_airports_data_sane():
    # Generated data: every state has airports, each with a parseable
    # ident and coordinates inside the state's REAL border polygon (the
    # rectangle wouldn't work: Adak sits in the Aleutians, far outside
    # Alaska's mainland-only bounding box).
    assert len(config.AIRPORTS_BY_STATE) == 50
    for state, airports in config.AIRPORTS_BY_STATE.items():
        assert airports, state
        for ap in airports:
            assert re.search(r"\([A-Z0-9]{3,4}\)", ap.name), ap.name
            assert point_in_state(ap.lat, ap.lon, state), f"{ap.name} not in {state}"
            assert -300 <= ap.elevation_ft <= 12000, ap.name


def test_borders_present_and_point_tests():
    for state in config.US_STATE_BOUNDS:
        assert border_geojson(state) is not None, state
    assert point_in_state(39.74, -104.99, "Colorado")       # Denver
    assert not point_in_state(41.88, -87.63, "Colorado")    # Chicago


def test_statewide_border_filter_drops_spillover():
    # Texas's poll box includes Santa Fe, NM — the border filter must
    # drop it while keeping genuinely-Texan traffic.
    texas = config.AIRPORTS_BY_STATE["Texas"]
    tracker = TrafficTracker(_statewide_sentinel("Texas"), watch_airports=texas,
                             statewide=True, region="Texas")
    santa_fe = make_ac(icao24="aaa111", lat=35.69, lon=-105.94, baro_alt_ft=12000.0)
    austin = make_ac(icao24="bbb222", lat=30.27, lon=-97.74, baro_alt_ft=5000.0)
    aircraft, _ = tracker.update([santa_fe, austin])
    assert [ac["icao24"] for ac in aircraft] == ["bbb222"]


def test_statewide_references_that_states_airports():
    # An aircraft in the pattern over a Texas field gets Texas references
    # when Texas airports are the watch list — per-state logic works
    # beyond Colorado.
    texas = config.AIRPORTS_BY_STATE["Texas"]
    field = texas[0]
    tracker = TrafficTracker(_statewide_sentinel("Texas"), watch_airports=texas,
                             statewide=True, region="Texas")
    ac = make_ac(lat=field.lat, lon=field.lon,
                 baro_alt_ft=field.elevation_ft + 1000, speed_kt=95.0)
    aircraft, _ = tracker.update([ac])
    assert aircraft[0]["near"] == field.name
    assert aircraft[0]["state"] == "maneuvering/pattern"


def test_state_bounds_table_sane():
    # 50 states, every box well-formed and inside plausible US ranges.
    assert len(config.US_STATE_BOUNDS) == 50
    for name, (abbr, lamin, lomin, lamax, lomax) in config.US_STATE_BOUNDS.items():
        assert len(abbr) == 2, name
        assert lamin < lamax, name
        assert lomin < lomax, name
        assert 18.0 <= lamin and lamax <= 72.0, name   # Hawaii..Alaska
        assert -180.0 <= lomin and lomax <= -66.0, name


def test_failed_poll_does_not_age_out_aircraft():
    tracker = TrafficTracker(AIRPORT)
    tracker.update([make_ac()])
    # Many failed polls in a row: no data is not the same as an empty sky.
    for _ in range(config.STALE_POLLS * 3):
        aircraft, events = tracker.update(None)
    assert len(aircraft) == 1
    assert not any("left area" in e for e in events)
