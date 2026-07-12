# pattern-watch

A live traffic picture for untowered airports — any GA field in any US
state — built on free ADS-B data from community aggregators
([adsb.lol](https://adsb.lol/), [adsb.fi](https://adsb.fi/),
[airplanes.live](https://airplanes.live/)).

**Live demo:** [pattern-watch.onrender.com](https://pattern-watch.onrender.com)
(free hosting sleeps when idle — the first visit takes ~30–60 s to wake)

## Why

About 90% of US airports have no control tower. There is no controller and
no radar screen — pilots announce their position and intentions over a
shared radio frequency and build a mental picture of who else is around.
That works, but it depends on everyone hearing (and making) the right
calls.

pattern-watch is a small demo of what a "traffic picture" for one of these
airports can look like: pick a state and one of its GA airports (or the
whole state at once), and it polls community ADS-B aggregators there,
works out each aircraft's distance and bearing from the field, and takes a
rough guess at what each one is doing — on the ground, departing, flying
the pattern, inbound, passing overhead, or just en route across the state.

## Quick start

Requires Python 3.9+.

```bash
cd pattern-watch
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Terminal mode: a table that repaints every 15 seconds (default: KEIK)
python main.py --mode terminal

# Web mode: a Leaflet map + sidebar, usually at http://127.0.0.1:5050
python main.py --mode web

# Watch a field that isn't in the built-in list (lat, lon, elev ft, name)
python main.py --mode web --airport "39.9088,-105.1172,5673,Rocky Mountain Metro (KBJC)"

# Run the tests (28, no network needed)
pytest
```

Web mode prints the exact URL on startup: it wants port 5050 and probes
upward (5051, 5052, …) if that's busy. (It deliberately avoids the classic
5000 — macOS AirPlay Receiver squats on it.)

## The web UI

Styled after VFR sectional charts (cream paper, navy ink, magenta airport
symbology — untowered fields really are magenta on sectionals):

- **Two-step picker** — choose a **State** (all 50), then a **View**: one
  of that state's GA airports (~25 per state, 1,152 total, from the
  public-domain OurAirports dataset; Colorado's list is hand-curated
  first) or "Entire state — all traffic". A custom `--airport` joins its
  own state automatically.
- **Real state borders** — the traffic API can only be polled with a
  coarse shape (a circle around the state's bounding box), so a statewide
  fetch would spill into neighbouring states.
  Traffic is filtered against the actual state border polygon (US Census
  data, point-in-polygon in `borders.py`) and the true border is drawn on
  the map. The poll itself is still the coarse shape — filtering
  happens after download.
- **Range rings** at 2/5/10 nm around a watched airport, with a
  translucent red fill that fades as you zoom in (a fixed fill would tint
  the whole screen once you're inside a ring).
- **Aircraft** drawn as icons rotated to their track, color-coded by
  state label, with a permanent callsign · altitude tag, a position
  trail, and smooth movement between polls. Click one for full details.
  (Statewide mode drops the tags and trails — hundreds of labelled planes
  at state zoom is unreadable.)
- **Overlap tag** — nearby airports' watch areas overlap; an aircraft
  inside two or three at once gets an extra "overlap ×2/×3" chip, with
  the airport names in its click popup.
- **Sidebar** — live traffic cards (hover one and its plane glows on the
  map, and vice versa; click to fly to it), an event feed ("new aircraft
  in area" / "aircraft left area"), a poll-health badge (green live,
  amber rate-limited, red error) and a countdown to the next update.
- **KEY** button (bottom left) toggles the color legend; **DARK MAP**
  (bottom right) switches the tiles to a dark, Google-Maps-style look
  (done client-side by CSS-inverting the light tiles — see the comment
  in `templates/index.html`).

## Why does a small airport look empty?

Expect it: when I compared views live, Colorado statewide showed 78
aircraft — but only 10 of them were both below the altitude ceiling and
within 10 nm of a watched field. The rest were en-route traffic at cruise
altitudes, which the airport view deliberately filters out because it's
irrelevant to anyone in the pattern. Meanwhile the airport view of a busy
field (Centennial) showed 4 aircraft, including one 115 ft off the runway.

So an empty airport view usually means the field is genuinely quiet —
small GA airports have zero movements for hours at a time — or that
low-altitude traffic is invisible to the ADS-B receivers (see
Limitations). If you want the airport view to capture more passing
traffic, raise `CEILING_AGL_FT` in `config.py`.

## Project layout

| File | What it is |
| --- | --- |
| `main.py` | CLI entry; terminal loop and web-server startup |
| `config.py` | every tunable constant: airport lists, box size, poll rate, heuristic thresholds |
| `poller.py` | ADS-B client with provider failover (point+radius query, box filter, rate-limit backoff) |
| `traffic.py` | distance/bearing, state-label heuristics, cross-poll tracking, border filter |
| `display.py` | terminal renderer + Flask app (`/`, `/api/traffic`, `/api/airport`, `/api/border/<state>`, `/api/health`) |
| `borders.py` | point-in-polygon tests against real state outlines |
| `states.py` | bounding boxes + abbreviations for the 50 states |
| `airports_data.py` | **generated** — ~25 GA airports per state (OurAirports) |
| `state_borders.json` | **generated** — state border polygons (US Census, 20m simplified) |
| `tools/generate_airports.py` | regenerates `airports_data.py` (downloads the dataset) |
| `tools/generate_borders.py` | regenerates `state_borders.json` |
| `templates/index.html` | the whole web UI — one file, CDN Leaflet, no build step |
| `wsgi.py` | production entry point (gunicorn, single worker) |
| `render.yaml` | one-click deploy blueprint for Render's free tier |
| `tests/test_traffic.py` | 28 pytest cases: heuristics, tracker, borders, data sanity |
| `conftest.py` | empty on purpose — makes pytest put the project root on `sys.path` |

## How the state labels work

`traffic.py` classifies each aircraft with an ordered set of threshold
rules (first match wins), using altitude above the field (AGL), vertical
rate, ground speed, and distance from the field:

1. **on ground** — the transponder's on-ground flag is set, or the
   aircraft is within 150 ft of field elevation moving under 40 kt.
2. **departing** — climbing ≥ 400 fpm, below 2,000 ft AGL, within 5 nm.
3. **inbound (descending toward field)** — descending ≥ 300 fpm, below
   2,500 ft AGL, within 7 nm.
4. **maneuvering/pattern** — roughly level between 400–2,000 ft AGL,
   under 130 kt, within 4 nm.
5. **overflight (high/fast, passing through)** — everything else.

In the statewide view, aircraft outside the state's border polygon are
dropped first; each remaining aircraft is judged against its **nearest**
watched airport: within 10 nm of one it gets that field's heuristics (and
the sidebar says which field the distance refers to); farther out it's
labelled **en route**, and beyond ~50 nm from every watched airport no
distance is shown at all. Statewide applies no altitude ceiling — the
point is to see everything.

These rules are deliberately simple and deliberately tunable — every
number is a constant in `config.py`, and the tests pin the expected label
for a set of hand-built cases so you can tune with a safety net.

## Data feed: community aggregators (no account needed)

Traffic comes from community-run, open-source ADS-B aggregators — no API
key, and — unlike the OpenSky Network this project originally used —
they answer requests from cloud-hosted IPs, which is what makes the free
Render deployment work. The poller tries [adsb.lol](https://adsb.lol/),
[adsb.fi](https://adsb.fi/) and [airplanes.live](https://airplanes.live/)
in order (identical readsb JSON), with a per-provider backoff: if one
rate-limits us, the next takes the poll and the map never goes stale.
The app is polite regardless: requests are spaced ≥2 s apart process-wide
no matter how many views are open, `POLL_INTERVAL_S` sets the per-view
cadence, and polling stops entirely when nobody is watching.

> History: this project originally polled the OpenSky Network. That
> works fine locally, but OpenSky's servers silently drop connections
> from datacenter IP ranges, so the hosted demo could never reach it —
> the switch to community aggregators is what fixed hosting.

## Deploying (free)

The repo includes `render.yaml`, a blueprint for [Render](https://render.com)'s
free tier:

1. Sign in to Render with GitHub, click **New + → Blueprint**, and pick
   this repo — it reads `render.yaml` and creates the web service
   (gunicorn serving `wsgi.py`, one worker, because the poller and the
   traffic snapshot live in that process's memory).
2. No API credentials are needed — the ADS-B providers are free and
   auth-less. `POLL_INTERVAL_S` (5 s in the blueprint) sets the update
   cadence; the poller self-throttles across views regardless.
3. Your app is live at `https://<service-name>.onrender.com` — put the
   link at the top of this README.

A deployment gotcha this project already handles, documented because it
cost a full evening to find: Render's gunicorn imports the app in the
master process and forks the worker afterwards, and **threads do not
survive a fork**. A poll thread started at import time runs (and logs!)
in the master while the worker serves a frozen copy of the traffic
snapshot — the map never updates, with nothing suspicious in the logs.
The poller therefore starts on the first request (see `build_web_app` in
`main.py`), which always runs in the worker. `GET /api/health` shows the
poll thread's liveness plus a live outbound-connectivity test, so a
hosted instance can be diagnosed with one curl.

Hosted-demo behavior worth knowing:

- **Every visitor gets their own view.** Each browser keeps its own
  state/airport selection, and the server polls one view per distinct
  selection being watched (up to `MAX_ACTIVE_VIEWS`, default 12, evicting
  the stalest at the cap) — two people can watch different states
  without affecting each other.
- **Views nobody is watching stop being polled** (5 min after their last
  request) and restart on demand, so an unvisited demo makes no API
  calls at all.
- **Free instances sleep** after ~15 min idle; the first request
  afterwards takes ~30–60 s while Render wakes it.

## Data sources

- **Traffic**: [adsb.lol](https://adsb.lol/), [adsb.fi](https://adsb.fi/)
  and [airplanes.live](https://airplanes.live/) REST APIs (readsb JSON,
  tried in that order).
- **Airports**: [OurAirports](https://ourairports.com/data/) (public
  domain). Regenerate with `python tools/generate_airports.py`.
- **State borders**: US Census Bureau cartographic boundary files
  (20m-simplified, public domain). Regenerate with
  `python tools/generate_borders.py`.

## Limitations (read this before trusting the picture)

- **ADS-B only shows aircraft that broadcast it.** ADS-B Out is not
  required in most of the airspace around untowered fields, and plenty of
  the traffic there — older GA aircraft, gliders, ultralights, NORDO
  aircraft — is simply invisible to this tool. An empty screen does *not*
  mean an empty pattern.
- **Community coverage is spotty at low altitude.** Reception depends
  on volunteer ground stations; aircraft in the pattern (low, and
  shielded by terrain) drop in and out. The tracker tolerates a few missed
  polls before declaring an aircraft gone, but gaps happen.
- **The state labels are rough heuristics, not certified logic.** A
  helicopter hovering, a glider circling, or a fast twin on a wide final
  will all be mislabelled sometimes. Real systems fuse radar, multi-sensor
  ADS-B, and flight-plan data with validated logic; this is a toy by
  comparison.
- **The airport picks are mechanical.** Generated from OurAirports by a
  simple ranking (has a Wikipedia article, medium before small, military
  fields excluded by name) — a state's busiest GA field might be missing
  and a sleepy one included. Add favourites to `PRESET_AIRPORTS` in
  `config.py` or pass `--airport`.
- **Very large states lose their far corners.** The traffic API is
  queried as a circle (max 250 nm radius) around the state's bounding
  box; for states bigger than that (Texas, Alaska, California, Montana)
  the corners beyond the cap aren't fetched. Individual airports are
  unaffected — every airport polls its own small box.
- **Borders and boxes are approximations.** Border polygons are
  20m-simplified Census outlines; Alaska's *poll box* covers the mainland
  only because the Aleutians cross the 180° meridian (Aleutian airports
  like Adak are still individually watchable — each airport polls its own
  box).
- **Seconds-scale polling is slow** by air-traffic standards — even at
  the hosted 5 s cadence a pattern aircraft moves ~0.14 nm between
  fixes, and with many views open the shared ≥2 s request spacing
  stretches each view's effective refresh (the map animates between
  fixes, which looks continuous but is interpolation, not data).

## What I'd do next

- **Sensor fusion**: blend in a local ADS-B receiver (an RTL-SDR dongle
  running dump1090 costs ~$30) for second-by-second coverage right at the
  field, using adsb.lol only for the wider area.
- **Historical pattern stats**: log traffic over weeks and answer
  questions like "which runway do people actually use when the wind is
  calm?" or "when is the pattern busiest?"
- **Converging-traffic alerts**: with two aircraft inbound, estimate
  closest point of approach and flag pairs likely to arrive at the same
  place at the same time — the beginnings of a safety net rather than
  just a picture.
