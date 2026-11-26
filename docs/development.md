# Development

Everything except the e-paper panel runs on any Mac or Linux machine: the tests, the
renderer, live data and the mock display, which writes PNG files instead of driving
hardware. No Raspberry Pi is needed to work on Zurich Air.

- [Setup](#setup)
- [Project layout](#project-layout)
- [Commands](#commands)
- [Previews](#previews)
- [Save and replay real traffic](#save-and-replay-real-traffic)
- [Explain the ranking](#explain-the-ranking)
- [Tests, lint and format](#tests-lint-and-format)
- [Documentation images](#documentation-images)
- [Continuous integration](#continuous-integration)
- [Extending Zurich Air](#extending-zurich-air)

## Setup

Install [uv](https://docs.astral.sh/uv/); it fetches the right Python by itself.

```sh
git clone https://github.com/nandometzger/zurich-air.git
cd zurich-air
uv sync                                 # .venv with Python 3.13, pytest and ruff
uv run pytest -q                        # the test suite
uv run zurich-air preview --scale 3     # synthetic scenes → previews/
```

`.python-version` pins Python 3.13 for development; the package supports 3.11 and later,
and CI tests both ends. The only runtime dependency is Pillow; the e-paper panel will
need the hardware bindings the Pi installer adds (see [Raspberry Pi setup](raspberry-pi.md)).

## Project layout

```text
src/zurich_air/
├── __init__.py           version
├── __main__.py           python -m zurich_air
├── cli.py                the zurich-air command and its subcommands
├── app.py                the live loop: one tick per interval, error handling, watchdog
├── pipeline.py           build_scene: snapshot → Scene, shared by live app and previews
├── config.py             typed TOML configuration with defaults and validation
├── schedule.py           when to update: whole intervals, quiet hours, clock sanity
├── systemd.py            sd_notify messages and journal-friendly logging
├── data/
│   ├── aircraft.py       Aircraft and TrafficSnapshot, the provider-independent model
│   ├── provider.py       HTTP sources, fallback chain, backoff and rate limits
│   ├── readsb.py         defensive parser for readsb JSON
│   ├── reference.py      operator and type names, notable/widebody/helicopter lists
│   └── synthetic.py      deterministic invented scenes, for previews and tests only
├── processing/
│   ├── geo.py            distances, bearings, the radar projection
│   ├── filtering.py      which aircraft belong on the radar
│   ├── history.py        recent positions for the trails
│   ├── phase.py          arrival or departure, inferred conservatively
│   └── ranking.py        scores, reasons and the featured aircraft
├── rendering/
│   ├── renderer.py       the whole frame: header, radar, list, footer
│   ├── layout.py         screen geometry, spacing and type scale
│   ├── radar.py          scope, airport glyph, trails, markers, badge placement
│   ├── flight_list.py    the numbered rows
│   ├── symbols.py        aircraft sprites, badges, arrows, triangles
│   ├── fonts.py          B612 and pixel-exact text helpers
│   └── formatting.py     altitudes, descriptions, clock: data to short strings
├── display/
│   ├── __init__.py       create_display: picks the backend
│   ├── base.py           the Display interface (show, close)
│   └── mock.py           writes latest.png (and optionally every frame)
└── assets/fonts/         B612-Regular.ttf and its licence (OFL)

tests/                    the pytest suite; fixtures/ holds a readsb sample
scripts/install-pi.sh     installs or updates Zurich Air on a Raspberry Pi
scripts/render-docs-images.py  regenerates docs/images/
system/zurich-air.service systemd unit template, filled in by the installer
config.example.toml       every setting with its default
```

[How it works](how-it-works.md) explains how these pieces fit together.

## Commands

`preview`, `once`, `run` and `explain` accept `--log-level debug|info|warning|error`
(before or after the command) and `--config FILE`. Without `--config` the built-in
defaults are used; `config.toml` is not read automatically.

| Command | What it does |
| --- | --- |
| `zurich-air preview` | Renders synthetic scenes or saved responses to PNG. No hardware, no network. |
| `zurich-air once` | Fetches live traffic once and updates the display (`previews/latest.png` with the mock backend). |
| `zurich-air run` | Updates continuously until Ctrl-C, as the service does. |
| `zurich-air explain` | Prints every candidate aircraft with its score and the reasons behind it. |
| `zurich-air doctor` | Checks the setup; see [Troubleshooting](troubleshooting.md). |

`once` and `run` also take `--out DIR` (where the mock display writes) and
`--keep-frames` (keep every frame as `frame-0001.png`, `frame-0002.png`, … besides
`latest.png`). `once` exits with 0 when new data arrived and nothing failed, 1 otherwise;
`run` exits with 0 when stopped and 1 after three display failures in a row. An invalid
config, or a display backend that is not available, ends either with status 2.

## Previews

`preview` draws exactly what the panel would show, pixel for pixel, from data that never
changes, which makes it the tool for visual work.

```sh
uv run zurich-air preview                          # all synthetic scenes
uv run zurich-air preview --scene busy --scale 3   # one scene, also at 3×
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--scene NAME` | `all` | Synthetic scene: `typical`, `busy`, `quiet`, `empty`, `degraded`, `stale` or `all`. |
| `--snapshot FILE …` | | Saved readsb responses instead, oldest first: the last is rendered, the earlier ones only build up the trails. Cannot be combined with `--scene`. |
| `--source-label TEXT` | `SNAPSHOT` | Attribution in the footer of a `--snapshot` render, e.g. `ADSB.FI`. |
| `--config FILE` | built-in | Configuration to render with. |
| `--out DIR` | `display.output_dir` | Output directory (`previews/`, which git ignores). |
| `--scale N` | `1` | Also write an N× nearest-neighbour enlargement, `<name>@Nx.png`. |

The files are named after the scene (`previews/typical.png`) or the last snapshot file.
The synthetic scenes are an invented afternoon at Zürich: `typical` and `busy` traffic,
a `quiet` sky, an `empty` one, `degraded` data (missing fields, unknown codes, an
emergency, odd values, as real feeds produce them) and `stale` data, 11 minutes old. They
are placed around the default location, so they do not show up on a radar centred
elsewhere.

## Save and replay real traffic

For design work with real data, save responses once and render them as often as you like:

```sh
uv run zurich-air once --save-json snapshots/now.json
uv run zurich-air preview --snapshot snapshots/now.json --source-label ADSB.FI --scale 3
```

`--save-json` stores the raw response of the source that answered, and only when one did.
For trails, save a few a minute apart and pass them oldest first:

```sh
for i in 1 2 3 4 5; do uv run zurich-air once --save-json snapshots/t$i.json; sleep 60; done
uv run zurich-air preview --snapshot snapshots/t*.json --source-label ADSB.FI --scale 3
```

A snapshot is rendered as if live at the moment it was taken: its time comes from the
response, or from the file's modification time if the response has none. Snapshots
contain real traffic, so `snapshots/` is git-ignored: keep them out of the repository
(see [Data sources](data-sources.md#privacy)).

## Explain the ranking

`explain` shows why each aircraft is or is not in the flight list: one row per candidate
with its type, altitude, distance from the airport, inferred phase, score and every
reason that contributed points. A dashed line marks where the list ends.

```sh
uv run zurich-air explain                                      # one live fetch
uv run zurich-air explain --snapshot snapshots/t*.json         # offline, oldest first
uv run zurich-air explain --config config.toml --snapshot snapshots/t5.json
```

With several snapshots, the earlier ones only carry the flight list forward, so that the
`featured before` bonus shows up as it would in the live app. `explain` has no option for
the synthetic scenes; the example in [How it works](how-it-works.md#an-example) was
produced with the same function:

```sh
uv run python -c "
from zurich_air.cli import explain_ranking
from zurich_air.config import Config
from zurich_air.data import synthetic
print(explain_ranking([synthetic.scene('typical').snapshot], Config()))"
```

## Tests, lint and format

```sh
uv run pytest -q                   # all tests, a few seconds, no network
uv run ruff check                  # lint
uv run ruff format                 # format (CI runs ruff format --check)
```

- Tests never touch the network: the provider tests use a fake opener and clock, and
  parsing is tested against `tests/fixtures/readsb_small.json`.
- Rendering tests draw real frames and look at their pixels.
- `tests/test_config.py` checks that `config.example.toml` matches the defaults in
  `config.py`: change both together.
- `tests/test_deploy_files.py` checks the installer and the systemd unit (placeholders,
  watchdog, restart policy, hardening, no machine-specific paths or credentials). Install
  [ShellCheck](https://www.shellcheck.net) to have it lint the installer too; otherwise
  that test is skipped. One systemd test runs on Linux only.
- Ruff is configured in `pyproject.toml`: line length 100, Python 3.11 syntax, and the
  `E`, `F`, `W`, `I`, `B`, `UP`, `SIM`, `RUF`, `PTH` and `C4` rule sets.

## Documentation images

The images in `docs/images/` are rendered from synthetic data. After a visual change,
regenerate them and include them in the pull request:

```sh
uv run python scripts/render-docs-images.py
```

## Continuous integration

GitHub Actions (`.github/workflows/ci.yml`) runs on every push to `main` and on every pull
request, on Ubuntu with Python 3.11 and 3.13: `uv sync --locked`, `ruff check`,
`ruff format --check` and `pytest`. If you change dependencies, commit the updated
`uv.lock`; `--locked` fails otherwise.

## Extending Zurich Air

Some changes come up more often than others. Keep [the guidelines](../CONTRIBUTING.md#guidelines)
in mind: small, hardware-independent rendering, and no invented data.

- **Names in the flight list.** Operators (by ICAO airline prefix), aircraft type names,
  the notable, widebody and helicopter type lists and the air rescue callsign prefixes
  are tables in `data/reference.py`. Keep names short: the list column is about 130 px
  wide.
- **A ranking component.** Write a function `(Facts, Config) -> Reason | None` in
  `processing/ranking.py` and add it to `COMPONENTS`, add its weight to `RankingWeights`
  in `config.py` and to `config.example.toml` (the tests insist they match), document it
  in [Configuration](configuration.md#rankingweights), and test it.
- **A data source.** It has to speak readsb JSON. Add a `SourcePreset` to `PRESETS` in
  `data/provider.py` (URL template with `{lat}`, `{lon}` and `{radius_nm}`, minimum
  request interval, optional API key variable and header) and its name to `SOURCE_NAMES`
  in `config.py`, and describe its terms in [Data sources](data-sources.md).
- **A display backend.** Implement the `Display` interface in `display/base.py`:
  `show(image)` takes a full display-sized frame in mode `"1"`, and `close()` releases the
  hardware while the panel keeps the last frame. Select it in `create_display` in
  `display/__init__.py`. The e-paper backend for the Waveshare panel is Milestone 6 and
  waits for the hardware.
