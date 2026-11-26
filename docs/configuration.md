# Configuration

Zurich Air reads its settings from a [TOML](https://toml.io) file. Every key is optional:
an empty file, or none at all, gives the Zürich setup the project was designed for.
[`config.example.toml`](../config.example.toml) lists every key with its default and a
short comment; the test suite checks that it matches the built-in defaults in
`src/zurich_air/config.py`, so the two never drift apart.

- [Where the file lives](#where-the-file-lives)
- [Rules](#rules)
- [Reference](#reference): [`[location]`](#location) · [`[airport]`](#airport) ·
  [`[radar]`](#radar) · [`[display]`](#display) · [`[update]`](#update) ·
  [`[aircraft]`](#aircraft) · [`[ranking]`](#ranking) ·
  [`[ranking.weights]`](#rankingweights) · [`[provider]`](#provider)
- [The adsb.lol API key](#the-adsblol-api-key)
- [Recipes](#recipes)

## Where the file lives

**On the Raspberry Pi**, the installer creates `config.toml` in the install directory
(`~/zurich-air` unless you chose another with `--dir`), as a copy of
`config.example.toml`. It never overwrites an existing file, so your settings survive
updates. The service starts `zurich-air run --config ~/zurich-air/config.toml`; after
editing the file, apply it with a restart:

```sh
nano ~/zurich-air/config.toml
sudo systemctl restart zurich-air
journalctl -u zurich-air -n 20 --no-pager   # did it start cleanly?
```

A mistake in the file stops the program at start, in most cases with a message that names
the key, and systemd keeps retrying with growing pauses until the file is fixed.
`zurich-air doctor` checks the configuration along with the rest of the setup; see
[Troubleshooting](troubleshooting.md).

Because the installer copies every key into your file, later versions with new defaults
do not change keys that are already there. Compare your file with the current
`config.example.toml` after an update if you want the new values.

**On your computer**, the file is wherever you like; `config.toml` in the repository is
git-ignored. Pass it explicitly: without `--config`, the built-in defaults are used and
`config.toml` is not read.

```sh
cp config.example.toml config.toml
uv run zurich-air once --config config.toml
```

Relative paths in the file, such as `display.output_dir`, are relative to the working
directory: the repository when you run it by hand, the install directory under systemd.

## Rules

- **Unknown keys are rejected**, to catch typos: `unknown config key(s): radar.rnage_km`.
- **Types are strict.** Whole-number keys (shown without a decimal point below) do not
  accept `5.0`; decimal keys accept `60` as well as `60.0`. `true` and `false` are not
  numbers.
- **Values are checked** when the file is loaded; the ranges are listed in the tables.
- **No secrets**, so that the file can be shared in bug reports. The one secret Zurich Air
  knows, an adsb.lol API key, is read from the environment
  ([below](#the-adsblol-api-key)).

## Reference

### `[location]`

The radar centre and the place named in the header.

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | `"Zurich"` | Shown uppercased in the header: `ZURICH · LIVE AIR TRAFFIC`. The header does not shorten it, so keep it short. |
| `latitude` | `47.3769` | Radar centre, degrees north (−90 to 90). |
| `longitude` | `8.5417` | Radar centre, degrees east (−180 to 180). |
| `timezone` | `"Europe/Zurich"` | An IANA time zone name. Used for the clock in the header, the time in `NO DATA SINCE`, and the quiet hours. |

### `[airport]`

The airport drawn on the radar and used to recognise arrivals and departures.

| Key | Default | Meaning |
| --- | --- | --- |
| `code` | `"ZRH"` | Label drawn next to the runway glyph, and the column heading in `explain`. |
| `latitude` | `47.458056` | Airport reference point, degrees north. Distances for arrivals and departures are measured from here. |
| `longitude` | `8.548056` | Airport reference point, degrees east. |
| `elevation_ft` | `1416` | Field elevation in feet. A report slower than 5 kt and no more than 600 ft above it counts as on the ground; level flight up to 2000 ft above it can still count as an arrival. |
| `runways` | Zürich's three runways | Runway thresholds as `[lat1, lon1, lat2, lon2]`, one array per runway, drawn as a small glyph of the runway layout. Leave empty (`[]`) for a generic airport marker, a small ring at the reference point. The defaults come from [OurAirports](https://ourairports.com/) (public domain). |

### `[radar]`

| Key | Default | Meaning |
| --- | --- | --- |
| `range_km` | `60.0` | Radius of the outer ring in km. Aircraft farther from the centre are not shown; data is fetched for 10 km more. Must be positive. |
| `ring_step_km` | `20.0` | Spacing of the dotted inner range rings in km. Must be positive. |
| `trails` | `true` | Draw a dotted history trail behind each aircraft. |

### `[display]`

| Key | Default | Meaning |
| --- | --- | --- |
| `backend` | `"mock"` | `"mock"` writes the frame to `latest.png` in `output_dir`. `"waveshare"` will drive the e-paper panel; it is not implemented yet, and selecting it makes `once` and `run` stop with an error. |
| `width` | `400` | Frame width in pixels. The layout is designed for 400 × 300. |
| `height` | `300` | Frame height in pixels. |
| `rotation` | `0` | `0`, `90`, `180` or `270` degrees. To be applied by the hardware backend; the mock display ignores it. |
| `output_dir` | `"previews"` | Where the mock display and `preview` write PNG files. |
| `altitude_units` | `"aviation"` | `"aviation"`: flight levels from 7000 ft (the Swiss transition altitude), feet below (`FL350`, `4500 FT`). `"metric"`: metres, rounded to 10 m (`10 360 M`). |

### `[update]`

| Key | Default | Meaning |
| --- | --- | --- |
| `interval_s` | `60.0` | Seconds between updates (fetch, render, display). Updates land on whole multiples of it, just after the minute by default. At least 10. |
| `stale_after_s` | `300.0` | Once the last good fetch is this old, the display shows `NO DATA SINCE hh:mm` and a hollow dot, but keeps the aircraft. Must be positive. |
| `hide_after_s` | `1800.0` | Once it is this old, the radar is cleared and the list says `WAITING FOR DATA`. At least `stale_after_s`. |
| `quiet_hours` | `"23:30-06:00"` | A daily window of local time, `"HH:MM-HH:MM"`, which may cross midnight. `""` turns quiet hours off. Start and end must differ. |
| `quiet_interval_s` | `600.0` | Seconds between updates during quiet hours. The first update after the window still comes on time. At least 10. |

See [How it works](how-it-works.md#data-freshness) for the live, stale and waiting states.

### `[aircraft]`

| Key | Default | Meaning |
| --- | --- | --- |
| `featured` | `5` | Rows in the flight list, numbered on the radar: 0 to 6. |
| `max_altitude_ft` | `60000` | Aircraft above this are not shown. An unknown altitude passes. |
| `include_ground` | `false` | Also show (and allow featuring) aircraft on the ground: taxiing, parked. |
| `stale_after_s` | `60.0` | Aircraft whose last reported position is older than this are dropped. Must be positive. Not to be confused with `update.stale_after_s`, which is about the whole response. |

### `[ranking]`

Which aircraft get a row in the flight list. Arrivals and departures are inferred from
altitude, vertical rate and track; see
[How it works](how-it-works.md#choosing-the-five-aircraft).

| Key | Default | Meaning |
| --- | --- | --- |
| `phase_radius_km` | `40.0` | Arrivals and departures are recognised this close to the airport. Their bonus is full at the airport and half at this distance. Must be positive. |
| `arrival_max_altitude_ft` | `10000` | Arrivals: descending (or level and very low) at or below this altitude. Must be positive. |
| `departure_max_altitude_ft` | `15000` | Departures: climbing at or below this altitude. Must be positive. |
| `min_vertical_rate_fpm` | `300` | Slower climbs and descents count as level flight. Must be positive. |
| `max_track_offset_deg` | `75.0` | How far the track may stray from the direct line to (arrivals) or from (departures) the airport. Greater than 0, at most 90. |

### `[ranking.weights]`

Points each component adds to an aircraft's score. Each must lie between −100 and 100.
Emergencies (squawk 7500, 7600, 7700 or an emergency status) always come first, and
aircraft that opted out of tracking (LADD) or use a privacy address (PIA) are never
featured, whatever the weights.

| Key | Default | Meaning |
| --- | --- | --- |
| `arrival` | `40.0` | Inferred arrival; full at the airport, half at `phase_radius_km`. |
| `departure` | `40.0` | Inferred departure; scaled the same way. |
| `notable_type` | `30.0` | Very large or rare types: A380, 747, An-124, Beluga, C-17, A400M, MD-11 and others listed in `data/reference.py`. |
| `widebody` | `10.0` | Other widebodies. |
| `helicopter` | `15.0` | Helicopters. |
| `rescue` | `15.0` | Air rescue callsigns (Rega, `SAZ…`). |
| `military` | `25.0` | Flagged military in the data service's aircraft database. |
| `interesting` | `15.0` | Flagged interesting in that database. |
| `low_altitude` | `20.0` | Full at 0 ft, fading to nothing at 40 000 ft. |
| `near_centre` | `10.0` | Full at the radar centre, nothing at its edge. |
| `no_identity` | `-20.0` | Neither callsign nor type: a row that would say little. |
| `featured_before` | `15.0` | Stickiness: an aircraft already in the list keeps its place unless a newcomer beats it by this margin. Must not be negative. |

### `[provider]`

Where live traffic comes from. See [Data sources](data-sources.md) for the services and
their terms.

| Key | Default | Meaning |
| --- | --- | --- |
| `sources` | `["adsb_fi", "adsb_lol"]` | Tried in this order; a source that fails or is rate limited is skipped until it recovers. Choose from `"adsb_fi"` (opendata.adsb.fi) and `"adsb_lol"` (api.adsb.lol); at least one, none twice. |
| `timeout_s` | `10.0` | Seconds to wait for each request. Must be positive. |

## The adsb.lol API key

adsb.lol works without a key. If you have one, Zurich Air sends it with every adsb.lol
request (as the `x-api-key` header). It is read from the environment variable
`ZURICH_AIR_ADSB_LOL_KEY` and never from the config file, which is meant to be shareable.
Zurich Air still asks adsb.lol at most every three minutes, key or not.

**On the Raspberry Pi**, the service reads optional environment variables from
`~/zurich-air/.env` (`EnvironmentFile=` in the unit; a missing file is fine). The file is
git-ignored, and the installer never touches it:

```sh
nano ~/zurich-air/.env       # one line: ZURICH_AIR_ADSB_LOL_KEY=<your key>
chmod 600 ~/zurich-air/.env
sudo systemctl restart zurich-air
```

**On your computer**, only the service reads `.env`; when you run Zurich Air by hand, export
the variable into your shell first, for example from the same file:

```sh
set -a; . ./.env; set +a     # export every variable in .env
uv run zurich-air once --config config.toml
```

Never commit the key, put it on a command line that ends up in your shell history, or
paste it into an issue.

## Recipes

### Use it for another city or airport

Change `[location]` and `[airport]`, replacing each `<…>` placeholder with a real value:

```toml
[location]
name = "<City>"             # header: <CITY> · LIVE AIR TRAFFIC
latitude = <latitude>       # radar centre, decimal degrees
longitude = <longitude>
timezone = "<Area/City>"    # IANA name, e.g. the one timedatectl shows

[airport]
code = "<code>"             # label beside the airport
latitude = <latitude>       # airport reference point
longitude = <longitude>
elevation_ft = <feet>       # field elevation
runways = []                # [] draws a generic airport marker
```

- **Coordinates.** Take them from an authoritative source and double-check them: for the
  airport, the aerodrome data in the country's Aeronautical Information Publication (AIP);
  [OurAirports](https://ourairports.com/) (public domain), where the Zürich defaults come
  from, is a convenient cross-check. A wrong airport position quietly breaks the
  arrival and departure labels.
- **Runways are optional.** With `runways = []` the airport is a small ring. To draw the
  layout, add one `[lat1, lon1, lat2, lon2]` array per runway with the coordinates of its
  two thresholds. The glyph is only 9 px in radius: the directions and the shape of the
  layout show, metre-level accuracy does not.
- **The header** does not shorten `name`. A name of up to about 17 characters fits beside
  the clock; check a longer one in a preview.
- **The elevation** matters more than it seems: it decides which slow, low reports count as
  parked aircraft and which level flights still count as arriving.
- **Names in the flight list** come from tables in `src/zurich_air/data/reference.py`,
  which are written for traffic over Switzerland. Unknown airlines are left out of the
  description, and unknown aircraft types show their ICAO code; extend the tables if you
  like.
- **Previews.** The synthetic scenes of `zurich-air preview` are placed around Zürich and
  will not appear on a radar centred elsewhere. Check the result with live data instead:
  `uv run zurich-air once --config config.toml`, then open `previews/latest.png`.

### Show altitudes in metres

```toml
[display]
altitude_units = "metric"   # 10 360 M instead of FL340
```

### Rotate the picture

```toml
[display]
rotation = 180              # 0, 90, 180 or 270
```

Rotation is applied by the e-paper backend, which is not written yet; the mock display
and `preview` ignore it.

### Update all night

```toml
[update]
quiet_hours = ""            # no quiet hours: every interval_s, day and night
```

Or keep quiet hours but shorten them (`"01:00-05:00"`), or update more often during them
(`quiet_interval_s = 300`). Every update is a request to a data service.

### Fewer or more rows in the flight list

```toml
[aircraft]
featured = 3                # 0 to 6
```

The rows spread out over the height of the list, and six still fit. With `0` nothing is
featured or numbered, and the list area shows the same `NO AIRCRAFT` note as an empty sky.

### A larger or smaller radar

```toml
[radar]
range_km = 40               # radius of the outer ring
ring_step_km = 10           # dotted rings at 10, 20 and 30 km
```

The scope keeps its size on screen, so a smaller range spreads the traffic out. The
`near_centre` bonus is relative to the range, and arrivals and departures are recognised
within `ranking.phase_radius_km` of the airport regardless of it.

### Tune the ranking

Change the weights in `[ranking.weights]` and watch the effect with
`zurich-air explain`, which prints every candidate with its score and the reasons behind
it:

```sh
uv run zurich-air explain --config config.toml
```

For a fair comparison, rank the same traffic each time: save a few snapshots at a busy
moment and explain those instead (see [Development](development.md#save-and-replay-real-traffic)).
The earlier snapshots carry the flight list forward, as the live app does, so stickiness
shows up too:

```sh
uv run zurich-air explain --config config.toml --snapshot snapshots/t1.json snapshots/t2.json
```

Some starting points:

```toml
[ranking.weights]
helicopter = 40             # helicopters rank higher
low_altitude = 35           # prefer what you can see and hear from the ground
featured_before = 0         # no stickiness (rows still keep their order)
```

On the Pi, `~/zurich-air/.venv/bin/zurich-air explain --config ~/zurich-air/config.toml`
does the same with the installed version and your settings.
