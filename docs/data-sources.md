# Data sources

Zurich Air does not receive radio signals itself. It asks community ADS-B networks, which
collect the reports of receivers run by volunteers, for the aircraft around the radar
centre. Both supported services are free, need no account, and answer over HTTPS in
the JSON format of the readsb decoder, so one parser (`data/readsb.py`) reads both.

- [adsb.fi (default)](#adsbfi-default)
- [adsb.lol (fallback)](#adsblol-fallback)
- [Choosing the sources](#choosing-the-sources)
- [Fallback, backoff and rate limits](#fallback-backoff-and-rate-limits)
- [What Zurich Air sends](#what-zurich-air-sends)
- [What it uses from the answer](#what-it-uses-from-the-answer)
- [Why there are no routes or destinations](#why-there-are-no-routes-or-destinations)
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

## What Zurich Air sends

One HTTPS GET request per update, plus one to the next source if the first fails. With
the default settings it looks like this:

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

Nothing else: no account, no cookies, no identifiers of the device or its owner. As with
any web request, the service sees the IP address the request comes from. The default
centre is the middle of Zürich; if you move it to your home, the requests carry those
coordinates. Zurich Air does not log the request URL.

## What it uses from the answer

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

## Why there are no routes or destinations

ADS-B messages carry an aircraft's identity, position, altitude and velocity, but no flight
plan: nothing says where it came from or where it is going. Routes would have to come from
separate databases that match callsigns to schedules, and that data is often incomplete or
out of date. A display that cannot show caveats should not guess.

So Zurich Air shows only what the data says, and never invents a destination or an
aircraft type:

- The **type** comes from the `t` field of the service's aircraft database, and the
  **operator** from the airline prefix of the callsign, looked up in a static table
  (`data/reference.py`). Unknown values are left out.
- **ARR** and **DEP** are not routes but an inference from the aircraft's altitude,
  vertical rate and track near the airport. It is deliberately conservative: when the data
  is ambiguous there is no label, only an arrow along the track (see
  [How it works](how-it-works.md#arrival-or-departure)).

## Privacy

The aircraft databases behind both services mark some aircraft in `dbFlags`:

| Bit | Meaning | Effect in Zurich Air |
| --- | --- | --- |
| 1 | Military | Ranking bonus `military`. |
| 2 | Interesting | Ranking bonus `interesting`. |
| 4 | PIA: a Privacy ICAO Address, a temporary address that hides the owner | Never featured. |
| 8 | LADD: the owner opted out of public tracking (Limiting Aircraft Data Displayed) | Never featured. |

PIA and LADD aircraft remain on the radar as anonymous markers, like every unfeatured
aircraft, but never get a number, a row or a callsign on the display. The log names only
featured aircraft, so they never appear there either, and `zurich-air explain` only counts
them as "withheld for privacy".

Responses saved with `zurich-air once --save-json` contain real traffic, including those
aircraft. Keep them in `snapshots/`, which git ignores, and out of the repository.

## Your responsibility

If you build your own Zurich Air, you are responsible for following each provider's
current terms of use. They can change: check them before you deploy, especially for
anything beyond personal use, and keep the attribution in the footer.
