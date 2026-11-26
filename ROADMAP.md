# Roadmap

Where Zurich Air stands and where it might go. Only **v1** is a commitment. **v2** has a
chosen scope and a hope rather than a date. Everything else is a list of ideas, each with
what it would take and what is still unknown; ideas change as the device meets real
walls, real Wi-Fi and real traffic. To discuss one, or to propose your own,
[open an issue](https://github.com/nandometzger/zurich-air/issues).

- [v1: Christmas 2026](#v1-christmas-2026)
- [v1.x: polish](#v1x-polish)
- [v2: weather, destinations and any airport](#v2-weather-destinations-and-any-airport)
- [Parked ideas](#parked-ideas)
- [Deliberately not planned](#deliberately-not-planned)

## v1: Christmas 2026

The first Zurich Air is a Christmas present, so v1 is due by Christmas 2026. Everything
that does not need the e-paper panel is done and runs on any computer and, in mock mode,
on the Pi.

**Done**

- [x] **Repository and tooling.** uv with a lockfile, ruff, pytest, CI on Python 3.11 and
  3.13, issue forms, a pull request template and Dependabot.
- [x] **Geometry.** Distances, bearings and the radar projection.
- [x] **Design.** A 1-bit renderer set in B612, designed against a 60 km radar and
  realistic traffic: numbered badges on leader lines, trails, the airport glyph, a flight
  list with `ARR`/`DEP` and descriptions shortened by meaning. A mock display and
  `zurich-air preview` with synthetic scenes; documentation images rendered from them.
- [x] **Live data.** A defensive readsb JSON parser; adsb.fi with adsb.lol as fallback,
  with per-source rate limits and backoff; one pipeline shared by previews and the live
  app.
- [x] **Ranking.** Explainable, configurable score components, stickiness, emergencies
  first, LADD and PIA aircraft never featured, and `zurich-air explain`.
- [x] **Raspberry Pi setup.** An install script, a systemd service and a step-by-step
  guide, including handing the device over to the Wi-Fi of its new home.
- [x] **Reliability.** Outage handling (`NO DATA SINCE`, then `WAITING FOR DATA`), quiet
  hours, a clock sanity check, an hourly summary in the log, the systemd watchdog, and a
  start that does not wait for time sync, so it comes up without a network.
- [x] **`zurich-air doctor`.** A setup self-check, run at the end of every install.
- [x] **Documentation.** Reading the display, hardware, Pi setup, configuration,
  troubleshooting, how it works, data sources and development.

**Open**

- [ ] **E-paper backend** (Milestone 6), written once the panel has arrived and been
  inspected:
  - identify the exact model and revision, and document the wiring;
  - a refresh policy: when to do a full refresh, and how long one takes on this panel;
  - `display.rotation`, which the config already accepts and only a hardware backend
    applies;
  - a neutral screen on shutdown, so that a stopped device never shows a frozen "live"
    picture.
- [ ] **Verification on the device.** Install from the guide on a real Pi and remove the
  *Untested* marks that hold. Then check that the watchdog really restarts a hung
  process, that the device boots with and without a network and shows the right state,
  and how long a frame takes to render on the Pi Zero 2 W (`zurich-air doctor` reports
  it).
- [ ] **The frame** (Milestone 8): choose it, mount the panel and the Pi, route the power
  cable.
- [ ] **Final documentation with photos** of the real device, and measured power draw in
  place of the published figures.

## v1.x: polish

Small improvements once v1 hangs on a wall. Ideas, not commitments.

- **Four levels of grey.** Some revisions of the Waveshare 4.2″ panel can show four grey
  levels; whether this one can is to be confirmed. Grey could quieten the range rings and
  trails so that aircraft stand out. *Needs:* panel support, a second render mode (the
  renderer works in 1-bit from the start, and 1-bit must stay the default), and a look at
  how grey levels interact with refresh time.
- **Partial refresh.** Update only what changed, faster and without the full-screen flash,
  with a periodic full refresh to clear ghosting. *Needs:* panel support, and measurements
  on the device of how many partial refreshes it takes before ghosting shows and how often
  a full one is needed.
- **SD-card longevity and automatic updates.** A read-only root file system (Raspberry Pi
  OS offers an overlay file system) so that power cuts cannot damage the card, and updates
  installed from tagged releases rather than from `main`. *Needs:* a place for the
  configuration and logs that survives the overlay or deliberately does not, an update
  path that works with it (switch the overlay off, update, run `zurich-air doctor`, switch
  it on again, roll back on failure), and release tags, which do not exist yet.
- **Aircraft silhouettes by type.** Today every featured aircraft is the same 15 px
  airliner, and helicopters have their own top view. Distinct shapes for a few types worth
  spotting (four engines for an A380 or 747, straight wings for turboprops) would show what
  is flying at a glance. *Needs:* shapes that stay legible in 1-bit at 15 px, mapped from
  ICAO type codes in `data/reference.py`.

## v2: weather, destinations and any airport

The second version tells a little more, without asking more of whoever glances at it: the
weather and the runways in use, and where a flight is going whenever that can be checked.
And it becomes easy to set up for another city. **Status:** parts 1 and 2 are implemented
and run in mock mode, pending verification on the device; part 3 follows.

### 1. Airport and weather line

**Implemented**, pending verification on the device. One line in the footer with the
runways in use and the weather at the airport: `LDG 14 · DEP 28 · 270° 8 KT G18 · 4 °C ·
QNH 1018`.

- [x] **Weather.** The METAR for `airport.icao` (LSZH) from
  [aviationweather.gov](https://aviationweather.gov), which is free and needs no key,
  fetched every 10 minutes: wind, temperature and QNH. An observation older than 90
  minutes is not shown, as honest about stale data as the radar.
- [x] **Runways in use**, inferred from the aircraft actually seen landing and taking off:
  aircraft lined up with a runway of `airport.icao` in the bundled OurAirports data, over
  the last 20 minutes. See [How it works](docs/how-it-works.md#runways-in-use).

### 2. Destinations, only when they check out

**Implemented**, pending verification on the device. ADS-B carries no flight plan, so
routes come from an offline table, the
[standing data](https://github.com/vradarserver/standing-data) of Virtual Radar Server
(CC0), downloaded one airline at a time, cached and checked for changes weekly. A route is
shown only when it agrees with what the aircraft is observed doing (see
[Data sources](docs/data-sources.md#routes-checked-never-guessed)):

- [x] an aircraft seen departing (`DEP`) must be on a route that starts at ZRH, one seen
  arriving (`ARR`) on a route that ends there;
- [x] an aircraft passing over must be heading for its destination, have made progress
  from its origin, which lies behind it, and fly no implausible detour;
- [x] a verified route stays with the aircraft until it has been off the radar for 5
  minutes or changes callsign.

Anything else shows no destination.

- **Measured** once, on live morning traffic around Zürich: the table had a route for 95%
  of the airline callsigns seen; 12 of 12 arrivals, 6 of 6 departures and 13 of 17
  overflights were verified, and wrong routes were rejected (see
  [How it works](docs/how-it-works.md#routes-only-when-they-check-out)).
- **The remaining risk:** the far end of a route lies well beyond the 60 km the display
  sees and cannot be checked. A flight that keeps its callsign but no longer goes where the
  table says would still pass.

### 3. Any airport, and easy setup

Making Zurich Air easy to build for any city.

- **Any airport from its ICAO code.** [OurAirports](https://ourairports.com/) data (public
  domain, where the Zürich defaults come from) is now bundled: some 3,300 larger airports
  with scheduled service and their runways, about 210 kB, read by `data/airports.py`.
  `airport.icao` already picks the runways for the runways in use, the METAR station and
  the airport the routes are checked against. The airport's position, elevation and runway
  layout could follow from it too, instead of being typed by hand. *Needs:* operator names
  for other regions in `data/reference.py`.
- **A one-line installer.** One command on a fresh Raspberry Pi OS instead of the three in
  the [Pi guide](docs/raspberry-pi.md#5-install-zurich-air); Wi-Fi and the user are still
  set with Raspberry Pi Imager. *Needs:* `install-pi.sh` to fetch the repository itself
  when it runs from a download, and a simple way to choose the city and the airport.
- **No prebuilt SD-card image.** Building and testing an image for every release is more
  upkeep than a hobby project should carry, and the installer gets to the same place.
- **A small settings page**, lowest priority: `http://zurich-air.local` for the location,
  units and quiet hours, plus the latest `zurich-air doctor` results and the current
  frame. *Needs:* no new dependency (Python's standard HTTP server), validation by the
  existing config loader before anything is saved, and some protection on a shared
  network, for example a code shown on the display.

## Parked ideas

Set aside because they would make Zurich Air more complicated than the object on the wall
it is meant to be.

- **A receiver of its own** (an RTL-SDR stick and an antenna, decoded by readsb): extra
  hardware, power and placement to get right, for data the networks already provide.
- **A spotter's logbook and "Yesterday at ZRH"**: a database, SD-card writes and a second
  page layout, for statistics that a display meant to be glanced at does not need.

## Deliberately not planned

Zurich Air is meant to be a calm object on the wall, not another screen that asks for
attention.

- **Phone notifications** or alerts of any kind.
- **Touch, or buttons**, beyond perhaps a single one.
