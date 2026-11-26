# Roadmap

Where Zurich Air stands and where it might go. Only **v1** is a commitment. Everything
after it is a list of ideas, each with what it would take and what is still unknown;
ideas change as the device meets real walls, real Wi-Fi and real traffic. To discuss one,
or to propose your own, [open an issue](https://github.com/nandometzger/zurich-air/issues).

- [v1: Christmas 2026](#v1-christmas-2026)
- [v1.x: polish](#v1x-polish)
- [v2: "Spotter's edition"](#v2-spotters-edition)
- [v2: for everyone](#v2-for-everyone)
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

## v2: "Spotter's edition"

The recommended direction for a second version: the display learns from what it sees over
Zürich, day after day. Ideas, in a rough order.

### 1. A receiver of its own

An RTL-SDR USB stick and a 1090 MHz antenna, decoded on the Pi by
[readsb](https://github.com/wiedehopf/readsb). The display would no longer depend on an
internet service for its data, and it could feed the same community networks it reads
from today.

- **Software.** readsb writes the JSON format the parser already reads (it accepts the
  `aircraft` key of a local readsb as well as the `ac` key of the APIs), so this is mostly
  a new local source next to the HTTP ones in `data/provider.py`, filtered to the radar
  range on the Pi.
- **Hardware.** A one-time cost of roughly CHF 40–60, no subscription. The Pi Zero 2 W has
  one micro-USB data port, so the stick needs an OTG adapter, and the power supply has to
  cover it too.
- **Open questions.** Whether a Pi Zero 2 W can decode and render comfortably at the same
  time (to be measured). How much an indoor antenna behind a window receives, compared
  with the networks' coverage; the network could stay as a fallback. Types,
  registrations and the privacy flags come from the networks' aircraft database; locally,
  readsb would need a database file for them, or rows would show less.

### 2. A spotter's logbook and "Yesterday at ZRH"

Remember what flew past, in a small SQLite database (Python's standard `sqlite3`, no new
dependency), and during quiet hours show a summary page instead of a radar at night: the
observed movements, the busiest hour, the rarest visitors and the types seen for the
first time.

- *Needs:* a schema for sightings, writes batched to spare the SD card, a second page
  layout, and wording that makes clear these are observed and inferred movements, not
  official airport figures.
- *Privacy:* LADD and PIA aircraft are counted but never logged by name, as on the
  display.

### 3. Airport and weather line

One line with the weather at Zürich Airport and the runways in use.

- **Weather.** The METAR for LSZH from [aviationweather.gov](https://aviationweather.gov),
  which is free and needs no key, polled far less often than the traffic. *Needs:* room
  in the layout, a choice of the few fields worth showing (wind, visibility, temperature,
  QNH), and the same honesty about stale data as the radar.
- **Runways in use**, inferred from the aircraft actually seen landing and taking off:
  match the tracks of `ARR` and `DEP` aircraft close to the airport with the runway
  directions in `airport.runways`, and show a runway only when several movements agree.

### 4. Destinations, but only when they agree

ADS-B carries no flight plan, which is why Zurich Air shows no routes today (see
[Data sources](docs/data-sources.md#why-there-are-no-routes-or-destinations)). A
destination could be shown when a free route source and the observed flight agree: a
route ending at ZRH for an aircraft seen arriving, or starting there for one seen
departing. Otherwise nothing is shown. *Needs:* a free source whose terms allow this use
and whose data is good enough, which has yet to be found, and room in the flight list.

## v2: for everyone

Making Zurich Air easier to build for any city.

- **Any airport from its ICAO code.** Fill the airport's position, elevation and runway
  thresholds from [OurAirports](https://ourairports.com/) data (public domain, where the
  Zürich defaults come from) instead of typing coordinates by hand. *Needs:* a new key,
  because `airport.code` is only the label on the radar; a decision between bundling a
  trimmed dataset and a one-time download; and operator names for other regions in
  `data/reference.py`.
- **A prebuilt SD-card image** with Zurich Air installed, built in CI for each release.
  *Needs:* an image build pipeline, release tags, and a way to set the Wi-Fi and user on
  first boot (whether Raspberry Pi Imager's customisation works with a custom image is to
  be checked).
- **A small settings page** at `http://zurich-air.local` for the location, units and
  quiet hours, plus the latest `zurich-air doctor` results and the current frame.
  *Needs:* no new dependency (Python's standard HTTP server), validation by the existing
  config loader before anything is saved, and some protection on a shared network, for
  example a code shown on the display.

## Deliberately not planned

Zurich Air is meant to be a calm object on the wall, not another screen that asks for
attention.

- **Phone notifications** or alerts of any kind.
- **Touch, or buttons**, beyond perhaps a single one.
