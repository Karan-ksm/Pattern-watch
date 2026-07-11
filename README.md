# pattern-watch

A live traffic picture for untowered airports — any GA field in any US
state — built on free ADS-B data from the
[OpenSky Network](https://opensky-network.org/).

**Live demo:** _coming soon_ <!-- replace with your Render URL after deploying -->
(free hosting sleeps when idle — the first visit takes ~30–60 s to wake)

## Why

About 90% of US airports have no control tower. There is no controller and
no radar screen — pilots announce their position and intentions over a
shared radio frequency and build a mental picture of who else is around.
That works, but it depends on everyone hearing (and making) the right
calls.

pattern-watch is a small demo of what a "traffic picture" for one of these
airports can look like: pick a state and one of its GA airports (or the
whole state at once), and it polls the OpenSky Network for aircraft there,
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
- **Real state borders** — OpenSky can only be polled with a lat/lon
  rectangle, so a statewide fetch would spill into neighbouring states.
  Traffic is filtered against the actual state border polygon (US Census
  data, point-in-polygon in `borders.py`) and the true border is drawn on
  the map. The poll itself is still the rectangle — filtering happens
  after download, so API cost is unchanged.
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
low-altitude traffic is invisible to OpenSky's receivers (see
Limitations). If you want the airport view to capture more passing
traffic, raise `CEILING_AGL_FT` in `config.py`.

## Project layout

| File | What it is |
| --- | --- |
| `main.py` | CLI entry; terminal loop and web-server startup |
| `config.py` | every tunable constant: airport lists, box size, poll rate, heuristic thresholds |
| `poller.py` | OpenSky REST client (OAuth2 or anonymous, 429/error handling, unit conversion) |
| `traffic.py` | distance/bearing, state-label heuristics, cross-poll tracking, border filter |
| `display.py` | terminal renderer + Flask app (`/`, `/api/traffic`, `/api/airport`, `/api/border/<state>`) |
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

## OpenSky credentials (optional but recommended)

Anonymous polling works out of the box but has a small daily budget
(~400 API credits). A free OpenSky account raises that to ~4,000/day:
create an **API client** in your account settings and export:

```bash
export OPENSKY_CLIENT_ID=your_client_id
export OPENSKY_CLIENT_SECRET=your_client_secret
```

> Note: the original OpenSky API used username/password Basic Auth, and
> some older tutorials still show that. OpenSky retired it in 2025 in
> favour of OAuth2 client credentials, which is what this project uses.
> If the variables are unset (or auth fails), pattern-watch quietly falls
> back to anonymous polling.

Budget math worth knowing: a 10 nm box costs ~1 credit per poll, a
state-sized box up to ~4 (bigger state, bigger cost — Texas is the max
tier). At the default 15-second interval that's ~240 credits/hour for one
airport — fine for demo sessions, but for all-day running raise
`POLL_INTERVAL_S` in `config.py` (30 s halves the burn). When the budget
runs out the status badge shows "rate limited" and polling resumes
automatically once it resets.

## Deploying (free)

The repo includes `render.yaml`, a blueprint for [Render](https://render.com)'s
free tier:

1. Sign in to Render with GitHub, click **New + → Blueprint**, and pick
   this repo — it reads `render.yaml` and creates the web service
   (gunicorn serving `wsgi.py`, one worker, because the poller and the
   traffic snapshot live in that process's memory).
2. In the service's **Environment** tab, add `OPENSKY_CLIENT_ID` and
   `OPENSKY_CLIENT_SECRET` (strongly recommended for hosting — anonymous
   credits run out fast). `POLL_INTERVAL_S` is preset to 30 s to stretch
   the budget.
3. Your app is live at `https://<service-name>.onrender.com` — put the
   link at the top of this README.

Hosted-demo behavior worth knowing:

- **The view is shared.** There is one poller and one picture per server,
  so every visitor sees — and can change — the same state/airport (the
  sidebar says so). Per-visitor views would need per-session pollers;
  out of scope for this demo.
- **Polling pauses when nobody is watching** (5 min without a visitor)
  and resumes on the next page load, so an unvisited demo spends no API
  credits.
- **Free instances sleep** after ~15 min idle; the first request
  afterwards takes ~30–60 s while Render wakes it.

## Data sources

- **Traffic**: [OpenSky Network](https://opensky-network.org/) REST API.
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
- **OpenSky's free-tier coverage is spotty at low altitude.** Reception
  depends on volunteer ground stations; aircraft in the pattern (low, and
  shielded by terrain) drop in and out. The tracker tolerates a few missed
  polls before declaring an aircraft gone, but gaps happen.
- **The daily API budget is real.** Sustained polling — especially
  statewide views — will eventually hit OpenSky's credit limit; the app
  shows "rate limited" and keeps retrying rather than crashing, but the
  picture goes stale until the budget resets.
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
- **Borders and boxes are approximations.** Border polygons are
  20m-simplified Census outlines; Alaska's *poll box* covers the mainland
  only because the Aleutians cross the 180° meridian (Aleutian airports
  like Adak are still individually watchable — each airport polls its own
  box).
- **15-second polling is slow** by air-traffic standards — a pattern
  aircraft moves ~0.4 nm between polls (the map animates between fixes,
  which looks continuous but is interpolation, not data).

## What I'd do next

- **Sensor fusion**: blend in a local ADS-B receiver (an RTL-SDR dongle
  running dump1090 costs ~$30) for second-by-second coverage right at the
  field, using OpenSky only for the wider area.
- **Historical pattern stats**: log traffic over weeks and answer
  questions like "which runway do people actually use when the wind is
  calm?" or "when is the pattern busiest?"
- **Converging-traffic alerts**: with two aircraft inbound, estimate
  closest point of approach and flag pairs likely to arrive at the same
  place at the same time — the beginnings of a safety net rather than
  just a picture.
