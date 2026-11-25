# Zurich Air

A minimalist, framed e-paper display showing live air traffic around Zürich — built with a
Raspberry Pi Zero 2 W and a 4.2″ Waveshare e-paper panel.

<p align="center">
  <img src="docs/images/preview@2x.png" width="800" alt="Radar of synthetic traffic around Zürich with a numbered list of five featured flights">
</p>

<sub>Rendered from synthetic data (400 × 300 px, 1-bit, shown at 2×).</sub>

The radar shows 60 km around the city, north up, with range rings every 20 km and the
runway layout of Zürich Airport. The most interesting aircraft are numbered: each badge
is tied to its aircraft by a short leader line, as on an air traffic controller's screen,
and the list beside the radar names them: callsign, altitude with a climb or descent mark,
operator and type, and `ARR` or `DEP` for arrivals and departures (otherwise an arrow
along the track). Dotted trails fade out behind each aircraft.

> **Status:** early development. Live data, the renderer and the mock display (PNG files)
> run on any machine; hardware integration follows once the panel arrives.

## Development setup

Requires [uv](https://docs.astral.sh/uv/) (it installs the right Python automatically).

```sh
git clone https://github.com/nandometzger/zurich-air.git
cd zurich-air
uv sync            # creates .venv with Python 3.13 and all dev tools
uv run pytest      # run the test suite
uv run ruff check  # lint
uv run zurich-air preview --scale 3   # render synthetic scenes to previews/
```

## Usage

```sh
cp config.example.toml config.toml   # optional: every setting has a Zürich default
uv run zurich-air once --config config.toml   # fetch live traffic once → previews/latest.png
uv run zurich-air run --config config.toml    # update every minute until Ctrl-C
```

With the default `mock` display backend every frame is written to `previews/` (`latest.png`
is always the newest); the e-paper backend is not implemented yet. Add `--log-level debug`
before the command for more detail.

For design work with real data, save a response and render it offline as often as you like:

```sh
uv run zurich-air once --save-json snapshots/now.json
uv run zurich-air preview --snapshot snapshots/now.json --scale 3
```

`preview --snapshot` accepts several files (oldest first) to build up the radar trails.
The footer credits `SNAPSHOT` unless you name the source, e.g. `--source-label ADSB.FI`.

### Which aircraft are listed

The flight list favours arrivals and departures near the airport, unusual aircraft
(A380, 747, Beluga, military, Rega helicopters, …) and traffic low enough to see from the
city; emergencies always come first. Aircraft already in the list keep their place unless
a newcomer is clearly more interesting, so the e-paper stays calm. Aircraft whose owners
opted out of public tracking (LADD) or that fly with a privacy address (PIA) are never
listed; they remain anonymous markers on the radar. The weights live in the `[ranking]`
section of the config, and `explain` shows every score and the reasons behind it:

```sh
uv run zurich-air explain                                # one live fetch
uv run zurich-air explain --snapshot snapshots/now.json  # offline
```

## Data sources

Zurich Air shows live data from community ADS-B networks, fetched over HTTPS without an
account. Sources are tried in the order given by `provider.sources`:

- **[adsb.fi](https://adsb.fi)** (default) — the open data API at `opendata.adsb.fi`.
  Free for personal, non-commercial use and limited to about one request per second.
  Attribution is required: the display credits ADSB.FI in its footer, and the data comes
  from [adsb.fi](https://adsb.fi).
- **[adsb.lol](https://adsb.lol)** (fallback) — `api.adsb.lol`. The data is licensed under
  the [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
  Without a key the quota is 500 requests per day per IP, so Zurich Air asks it at most
  every three minutes. An optional API key can be set in the environment variable
  `ZURICH_AIR_ADSB_LOL_KEY` (never in the config file).

Zurich Air polls once a minute with a generic user agent and backs off after errors and
rate limits. If you build your own, you are responsible for following each provider's
current terms of use — check them before you deploy, especially for anything beyond
personal use.

## License

MIT — see [LICENSE](LICENSE). The bundled [B612](https://github.com/polarsys/b612) typeface
is licensed under the SIL Open Font License 1.1.
