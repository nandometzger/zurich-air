# Data sources

Zurich Air does not receive radio signals itself. It asks community ADS-B networks, which
collect the reports of receivers run by volunteers, for the aircraft around the radar
centre. Both supported services are free, need no account, and answer over HTTPS in
the JSON format of the readsb decoder, so one parser (`data/readsb.py`) reads both.

Three more sources fill in what ADS-B does not carry: the airport's weather from
aviationweather.gov, a table of airline routes from the standing data of Virtual Radar
Server, and airport and runway data from OurAirports, bundled with the code.

- [adsb.fi (default)](#adsbfi-default)
- [adsb.lol (fallback)](#adsblol-fallback)
- [Choosing the sources](#choosing-the-sources)
- [Fallback, backoff and rate limits](#fallback-backoff-and-rate-limits)
- [aviationweather.gov: the weather](#aviationweathergov-the-weather)
- [VRS standing data: routes](#vrs-standing-data-routes)
- [OurAirports: airports and runways](#ourairports-airports-and-runways)
- [What Zurich Air sends](#what-zurich-air-sends)
- [What it uses from the answer](#what-it-uses-from-the-answer)
- [Routes: checked, never guessed](#routes-checked-never-guessed)
- [Privacy](#privacy)
- [Your responsibility](#your-responsibility)

## adsb.fi (default)

[adsb.fi](https://adsb.fi) runs an open data API at `opendata.adsb.fi`. Zurich Air uses
its query for aircraft within a distance of a point:

```text
https://opendata.adsb.fi/api/v3/lat/{lat}/lon/{lon}/dist/{radius_nm}
```

- **Terms.** Free for personal, non-commercial use. Attribution is required: cite adsb.fi
  and link to its home page. Zurich Air credits `ADSB.FI` in the footer of the display
  whenever the data on screen comes from it, and the README and this page link to
  [adsb.fi](https://adsb.fi).
- **Rate limit.** About one request per second. Zurich Air asks once a minute (every ten
  minutes in quiet hours) and never more often than every 5 seconds.

## adsb.lol (fallback)

[adsb.lol](https://adsb.lol) offers a similar API at `api.adsb.lol`:

```text
https://api.adsb.lol/v2/point/{lat}/{lon}/{radius_nm}
```

- **Licence.** The data is licensed under the
  [Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/). The
  footer credits `ADSB.LOL` while its data is shown.
- **Rate limit.** Without a key the quota is 500 requests per day per IP address, so
  Zurich Air asks adsb.lol at most every 180 seconds: no more than 480 requests a day,
  even if it were the only source.
- **Optional API key.** If you have one, put it in the environment variable
  `ZURICH_AIR_ADSB_LOL_KEY` (never in the config file); Zurich Air then sends it as the
  `x-api-key` header. [Configuration](configuration.md#the-adsblol-api-key) shows how to
  set it for the service. The 180-second minimum interval applies with a key too.

## Choosing the sources

`provider.sources` lists the services to ask, in order; the default is
`["adsb_fi", "adsb_lol"]`. To use only one of them:

```toml
[provider]
sources = ["adsb_fi"]
```

`provider.timeout_s` (10 s) limits how long each request may take.

## Fallback, backoff and rate limits

On every update, the sources are tried in order and the first one that answers wins. A
source is skipped while it is resting, either because its minimum interval since the last
request has not passed (5 s for adsb.fi, 180 s for adsb.lol) or because it failed
recently:

| What happened | When the source is asked again |
| --- | --- |
| A good answer | After its minimum interval; earlier errors are forgotten. |
| HTTP 429, rate limited | After the time in the service's `Retry-After` header (seconds or a date), or after 10 minutes without one. |
| Any other failure: timeout, network error, other HTTP status, malformed JSON, a response over 8 MB | After 30 s, then 60 s, 120 s and 300 s for each further failure in a row; longer if a `Retry-After` header asks for it. |

No source rests for more than 24 hours. Each failure is logged as a warning, such as
`adsb.fi: HTTP 429 rate limited; next try in 600 s` or
`adsb.fi: timed out; next try in 30 s`, and the hourly summary counts failed requests per
source.

If no source delivers, the update reports `no data` with the reason for each source, and
the last good data stays on screen: marked stale after 5 minutes and cleared after 30
(see [Data freshness](how-it-works.md#data-freshness)). With the default chain, a single
failed request to adsb.fi is usually covered by adsb.lol in the same minute. During a
longer adsb.fi outage, adsb.lol fills in whenever its 180-second interval allows, every
three or four minutes: often enough that the display is not marked stale.

## aviationweather.gov: the weather

The airport's weather comes from its METAR, the routine weather report of an airport, which
the [Aviation Weather Center](https://aviationweather.gov) of the US National Weather
Service (NWS) publishes through a data API that needs no account:

```text
https://aviationweather.gov/api/data/metar?ids={station}&format=json
```

- **Station.** `weather.station`, or `airport.icao` when that is empty: `LSZH` by default.
- **How often.** At most every `weather.interval_s` (600 s), failed attempts included: six
  requests an hour. Zürich issues a METAR at 20 and 50 minutes past the hour. An
  observation older than `weather.max_age_s` (90 minutes) is not shown.
  `zurich-air doctor` (without `--offline`) and a live `zurich-air explain` make one
  request each.
- **Rate limit.** The service allows 100 requests a minute.
- **Failures.** A timeout, network error, HTTP error, malformed JSON, a response over
  1 MB or no usable observation for the station is one warning in the log, such as
  `metar LSZH: HTTP 503; next try in 600 s`; the next request comes after
  `weather.interval_s`. The last good observation stays in use until it is too old.
- **Terms.** Data from the NWS is in the public domain. Zurich Air credits
  aviationweather.gov in the README; using its data implies no endorsement by the NWS, and
  nothing in Zurich Air should suggest one. A METAR is issued by the weather service of the
  station's country and only passed on by aviationweather.gov. Whether the Swiss
  originator of LSZH's METAR sets terms of its own for passing it on has not been
  verified, so treat the weather as being for a personal display, which is what Zurich Air
  is.

From each observation it uses:

| Field | Used as |
| --- | --- |
| `icaoId` | Station; observations for another station are ignored. |
| `obsTime` (or `reportTime`, which is rounded) | Observation time, for the age limit. |
| `wdir` | Wind direction in degrees true, or `"VRB"` for variable. |
| `wspd`, `wgst` | Wind speed and gusts in knots; gusts are shown only above the speed. |
| `temp` | Temperature in °C. |
| `altim` | QNH in hPa. |
| `rawOb` | The METAR as issued: shown by `zurich-air explain` and in the debug log. |

The dew point (`dewp`) is read but not shown. As with the traffic data, any field may be
missing or malformed; such a value becomes unknown and is left out.

## VRS standing data: routes

Routes come from the [standing data](https://github.com/vradarserver/standing-data) of
Virtual Radar Server, a crowd-sourced table maintained by vradarserver and its
contributors and released under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
It maps a normalised airline callsign such as `SWR8` to the ICAO codes of the airports the
flight serves, in the order flown (`LSZH-KJFK`), and is updated daily. A route from it is
a schedule, not an observation; see
[Routes: checked, never guessed](#routes-checked-never-guessed).

The table keeps one CSV file per airline code, and Zurich Air downloads only the files of
the airlines it actually sees, from GitHub:

```text
https://raw.githubusercontent.com/vradarserver/standing-data/main/routes/schema-01/S/SWR-all.csv
```

An airline with more than 10,000 routes is split into one file per first digit of the
flight number (`R/RYR-7.csv`); when an airline has no single file, that file is asked for
next. Downloading all 1,600 files (19 MB) every week would be wasteful on a home
connection, and holding all 630,000 routes in memory on a Pi Zero impossible.

- **Size.** One file is 1 kB to 250 kB, requested gzip-compressed, which brings it to
  about a quarter on the wire. Responses over 2 MB are refused.
- **Cache.** Each file is reduced to `callsign,airports` lines and kept in
  `routes.cache_dir` (`cache/routes`, relative to the working directory), with an
  `index.json` of when each was fetched and its ETag. Files are replaced in one step.
- **Weekly refresh.** After 7 days a file is asked for again with its ETag
  (`If-None-Match`); an unchanged file costs a `304 Not Modified` without a body. While a
  refresh fails, the old file stays in use for up to 30 days. A `404` (no such airline) is
  remembered for 7 days too, so unknown codes are not asked for again.
- **Fetch budget.** At most two requests per update (two per 50 s), each with a 5 s
  timeout. A lookup whose file is not cached yet finds no route, and succeeds on a later
  update once the budget allows the download. After a fresh install, the routes of a busy
  sky therefore appear over the first few minutes.
- **Backoff.** After a failure, no request at all for 30 s, then 60 s, 120 s and 300 s
  after further failures in a row; after HTTP 429 the time in `Retry-After`, or 10 minutes
  without one; never more than 24 hours. Each failure is one warning, such as
  `routes: timed out; next try in 30 s`.
- **Terms.** CC0 asks for nothing; Zurich Air credits the standing data in the README all
  the same.

Only airline callsigns (a three-letter code and a flight number) are looked up;
registrations used as callsigns never cause a request, and nor do aircraft whose owners
opted out of tracking (LADD) or that use a privacy address (PIA).

## OurAirports: airports and runways

Airports and their runways come from [OurAirports](https://ourairports.com/data/), whose
data is in the public domain (the `ourairports-data` repository uses the Unlicense). They
are bundled with the code in `src/zurich_air/assets/airports/`, about 210 kB:

- `airports.csv.gz`: the 3,260 airports with scheduled service that OurAirports classifies
  as large or medium, plus LSZH, with ICAO and IATA codes, name, position and elevation;
- `runways.csv.gz`: their 4,154 open runways with coordinates for both ends, without
  helicopter or water lanes;
- `SOURCE.txt`: the date, the source URLs and the SHA-256 of the files they were built
  from.

Zurich Air uses them for the runways of `airport.icao` (from which the runways in use are
inferred), the positions of the airports on a route (for the overflight checks), and the
IATA codes on the display (`JFK` rather than `KJFK`). `zurich-air doctor` checks that
`airport.icao` is among them. Nothing is fetched while it runs:
`scripts/update-airports.py` regenerates the files from OurAirports (see
[Development](development.md#bundled-airport-data)).
The default runway layout of the radar glyph, `airport.runways`, also comes from
OurAirports.

## What Zurich Air sends

**To the traffic services:** one HTTPS GET request per update, plus one to the next source
if the first fails. With the default settings it looks like this:

```text
GET https://opendata.adsb.fi/api/v3/lat/47.3769/lon/8.5417/dist/38
User-Agent: zurich-air/0.1.0
Accept: application/json
```

- **The radar centre**, `location.latitude` and `location.longitude`, to four decimal
  places.
- **The radius** in whole nautical miles: `radar.range_km` plus a 10 km margin (so that
  aircraft entering the scope already have a trail), rounded up and kept within the APIs'
  limit of 1 to 250 nm. For the default 60 km that is 70 km, or 38 nm.
- **A generic user agent**, `zurich-air/<version>`: no URL, name or e-mail address.
- **The adsb.lol key** in an `x-api-key` header, only to adsb.lol and only if you set one.

**To aviationweather.gov:** the station code, at most every ten minutes, with the same
user agent:

```text
GET https://aviationweather.gov/api/data/metar?ids=LSZH&format=json
User-Agent: zurich-air/0.1.0
Accept: application/json
```

**To GitHub** (`raw.githubusercontent.com`): the name of an airline's route file, with the
same user agent, `Accept-Encoding: gzip`, and the file's ETag when it is checked for
changes:

```text
GET https://raw.githubusercontent.com/vradarserver/standing-data/main/routes/schema-01/S/SWR-all.csv
User-Agent: zurich-air/0.1.0
Accept-Encoding: gzip
```

That is the three-letter airline code, and for an airline without a single file the
first digit of the flight number. Never the callsign, a position or anything about the
aircraft.

**OurAirports** receives nothing: its data is bundled.

Nothing else: no account, no cookies, no identifiers of the device or its owner. As with
any web request, each service sees the IP address the request comes from. The default
centre is the middle of Zürich; if you move it to your home, the traffic requests carry
those coordinates. GitHub can see which airlines' files are requested, that is, roughly
which airlines fly near the display, and aviationweather.gov which airport it is near.
Zurich Air does not log the request URLs.

## What it uses from the answer

From the traffic services' answer:

| readsb field | Used as |
| --- | --- |
| `hex` | ICAO address, the aircraft's unique identifier. Entries without one are dropped. |
| `lat`, `lon` | Position. Entries without a valid position are dropped. |
| `flight` | Callsign, shown in the flight list. |
| `r` | Registration, shown when there is no callsign. |
| `t` | ICAO aircraft type designator, from the service's aircraft database. |
| `alt_baro` | Barometric altitude, or `"ground"`. |
| `gs`, `track` (or `true_heading`) | Ground speed and track: the direction of the symbol and the arrow in the list. |
| `baro_rate` (or `geom_rate`) | Vertical rate: the climb and descent marks, and arrival or departure. |
| `category` | ADS-B emitter category: helicopters, and ground vehicles to leave out. |
| `squawk`, `emergency` | Emergencies (7500, 7600, 7700 or an emergency status). |
| `dbFlags` | Military, interesting and privacy flags from the aircraft database. |
| `seen_pos` (or `seen`) | Age of the position. |
| `now` | Time of the data. |

Any field may be missing or malformed; such a value becomes "unknown" and the display
leaves it out.

## Routes: checked, never guessed

ADS-B messages carry an aircraft's identity, position, altitude and velocity, but no flight
plan: nothing says where it came from or where it is going. Routes come from the
[VRS standing data](#vrs-standing-data-routes), which matches callsigns to schedules, and
schedules are often enough out of date or wrong: airlines reuse a callsign for the return
flight, and crews key in the wrong one. A display that cannot show caveats should not
guess.

So Zurich Air shows only what the data says, and never invents a destination or an
aircraft type:

- A **route** is shown only when the aircraft is seen flying it: departing from the
  airport for a route that starts there, arriving for one that ends there, or, passing
  over, heading for the destination and away from the origin. Anything ambiguous gets no
  route. [How it works](how-it-works.md#routes-only-when-they-check-out) lists the checks.
- The **type** comes from the `t` field of the service's aircraft database, and the
  **operator** from the airline prefix of the callsign, looked up in a static table
  (`data/reference.py`). Unknown values are left out.
- **ARR** and **DEP** are not routes but an inference from the aircraft's altitude,
  vertical rate and track near the airport. It is deliberately conservative: when the data
  is ambiguous there is no label, only an arrow along the track (see
  [How it works](how-it-works.md#arrival-or-departure)).
- The **runways in use** are inferred from aircraft clearly lined up with a runway, never
  from the wind (see [How it works](how-it-works.md#runways-in-use)).

## Privacy

The aircraft databases behind both services mark some aircraft in `dbFlags`:

| Bit | Meaning | Effect in Zurich Air |
| --- | --- | --- |
| 1 | Military | Ranking bonus `military`. |
| 2 | Interesting | Ranking bonus `interesting`. |
| 4 | PIA: a Privacy ICAO Address, a temporary address that hides the owner | Never featured. |
| 8 | LADD: the owner opted out of public tracking (Limiting Aircraft Data Displayed) | Never featured. |

PIA and LADD aircraft remain on the radar as anonymous markers, like every unfeatured
aircraft, but never get a number, a row or a callsign on the display, and their routes are
never looked up. The log names only featured aircraft, so they never appear there either,
and `zurich-air explain` only counts them as "withheld for privacy".

Responses saved with `zurich-air once --save-json` contain real traffic, including those
aircraft. Keep them in `snapshots/`, which git ignores, and out of the repository.

## Your responsibility

If you build your own Zurich Air, you are responsible for following each provider's
current terms of use, for the weather and route data as for the traffic. They can change:
check them before you deploy, especially for anything beyond personal use, and keep the
attribution in the footer.
