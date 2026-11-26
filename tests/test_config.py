import json
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

import pytest

from zurich_air import config
from zurich_air.config import Config, ConfigError, LocationConfig, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_are_zurich():
    cfg = Config()
    assert cfg.location.name == "Zurich"
    assert cfg.location.latitude == pytest.approx(47.3769)
    assert cfg.display.width == 400
    assert cfg.display.height == 300


def test_example_config_loads_and_matches_defaults():
    cfg = load_config(ROOT / "config.example.toml")
    assert cfg == Config()


def test_none_path_returns_defaults():
    assert load_config(None) == Config()


def test_partial_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[location]\nname = "Bern"\nlatitude = 46.948\nlongitude = 7.447\n')
    cfg = load_config(path)
    assert cfg.location.name == "Bern"
    assert cfg.radar.range_km == 60.0  # untouched default


def test_int_promoted_to_float(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[radar]\nrange_km = 50\n")
    cfg = load_config(path)
    assert isinstance(cfg.radar.range_km, float)


@pytest.mark.parametrize(
    "toml",
    [
        "[radar]\nrange_kms = 50\n",  # typo
        "[nonsense]\nx = 1\n",
        '[radar]\nrange_km = "far"\n',
        "[display]\nrotation = 45\n",
        '[display]\nbackend = "crt"\n',
        "[aircraft]\nfeatured = 12\n",
        "[radar]\ntrails = 1\n",
    ],
)
def test_invalid_configs_rejected(tmp_path, toml):
    path = tmp_path / "config.toml"
    path.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(path)


def test_time_zone_override(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[location]\ntimezone = "America/New_York"\n')
    assert load_config(path).location.timezone == "America/New_York"


@pytest.mark.parametrize(
    "name", ["Europe/Zurch", "Mars/Olympus_Mons", "Europe", "", "../etc/passwd", "/etc/localtime"]
)
def test_unknown_time_zone_rejected(tmp_path, name):
    path = tmp_path / "config.toml"
    path.write_text(f"[location]\ntimezone = {json.dumps(name)}\n")  # a TOML string, too
    with pytest.raises(ConfigError) as exc:
        load_config(path)
    assert str(exc.value).startswith(f"location.timezone: unknown time zone {name!r}; ")


def test_missing_time_zone_database_is_named(monkeypatch):
    def not_found(key):
        raise ZoneInfoNotFoundError(f"No time zone found with key {key}")

    monkeypatch.setattr(config, "ZoneInfo", not_found)
    monkeypatch.setattr(config, "available_timezones", set)  # neither system nor tzdata
    with pytest.raises(ConfigError, match=r"no time zone database.*install the tzdata package"):
        Config(location=LocationConfig(timezone="Europe/Zurich"))


def test_missing_file_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_provider_sources(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[provider]\nsources = ["adsb_lol"]\n')
    assert load_config(path).provider.sources == ("adsb_lol",)
    assert Config().provider.sources == ("adsb_fi", "adsb_lol")


def test_display_staleness_is_separate_from_position_age():
    cfg = Config()
    assert cfg.update.stale_after_s == 300.0
    assert cfg.aircraft.stale_after_s == 60.0


def test_update_defaults_hide_old_data_and_slow_down_at_night():
    update = Config().update
    assert update.stale_after_s < update.hide_after_s == 1800.0
    assert str(update.quiet) == "23:30-06:00"
    assert update.quiet_interval_s == 600.0


def test_quiet_hours_override_and_disable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[update]\nquiet_hours = "22:00-07:00"\nquiet_interval_s = 300\n')
    update = load_config(path).update
    assert str(update.quiet) == "22:00-07:00"
    assert update.quiet_interval_s == 300.0
    path.write_text('[update]\nquiet_hours = ""\n')
    assert load_config(path).update.quiet is None


@pytest.mark.parametrize(
    "toml",
    [
        '[provider]\nsources = ["flightradar"]\n',
        "[provider]\nsources = []\n",
        '[provider]\nsources = ["adsb_fi", "adsb_fi"]\n',
        '[provider]\nsources = "adsb_fi"\n',
        '[provider]\nname = "adsb_lol"\n',  # replaced by sources
        "[provider]\ntimeout_s = 0\n",
        "[update]\nstale_after_s = -1\n",
        "[update]\nhide_after_s = 60\n",  # shorter than stale_after_s
        '[update]\nquiet_hours = "late"\n',
        '[update]\nquiet_hours = "23:30-23:30"\n',
        '[update]\nquiet_hours = "23:30-25:00"\n',
        "[update]\nquiet_hours = 23\n",
        "[update]\nquiet_interval_s = 5\n",
    ],
)
def test_invalid_provider_and_update_configs_rejected(tmp_path, toml):
    path = tmp_path / "config.toml"
    path.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(path)


def test_ranking_defaults_and_partial_override(tmp_path):
    assert Config().ranking.weights.featured_before > 0
    assert Config().ranking.max_track_offset_deg <= 90
    path = tmp_path / "config.toml"
    path.write_text("[ranking]\nmax_track_offset_deg = 60\n\n[ranking.weights]\nhelicopter = 50\n")
    ranking = load_config(path).ranking
    assert ranking.max_track_offset_deg == 60.0
    assert ranking.weights.helicopter == 50.0
    assert isinstance(ranking.weights.helicopter, float)
    assert ranking.weights.arrival == Config().ranking.weights.arrival  # untouched default
    assert ranking.phase_radius_km == Config().ranking.phase_radius_km


@pytest.mark.parametrize(
    "toml",
    [
        "[ranking]\nphase_radius_km = 0\n",
        "[ranking]\nmax_track_offset_deg = 120\n",
        "[ranking]\nmin_vertical_rate_fpm = 0\n",
        "[ranking]\narrival_max_altitude_ft = -1\n",
        "[ranking]\nweights = 5\n",
        "[ranking.weights]\nhelicopter = 500\n",
        "[ranking.weights]\nno_identity = -101\n",
        "[ranking.weights]\nfeatured_before = -5\n",
        "[ranking.weights]\nhelicoptr = 5\n",  # typo
        '[ranking.weights]\narrival = "high"\n',
    ],
)
def test_invalid_ranking_configs_rejected(tmp_path, toml):
    path = tmp_path / "config.toml"
    path.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(path)


def test_airport_weather_and_routes_defaults():
    cfg = Config()
    assert cfg.airport.icao == "LSZH"
    assert cfg.airport.runways_in_use is True
    assert cfg.weather.enabled and cfg.weather.station == ""
    assert cfg.weather.interval_s == 600.0 and cfg.weather.max_age_s == 5400.0
    assert cfg.routes.enabled and cfg.routes.cache_dir == "cache/routes"
    assert not Path(cfg.routes.cache_dir).is_absolute()  # relative to the working directory


def test_weather_station_defaults_to_the_airport(tmp_path):
    assert Config().weather_station == "LSZH"
    path = tmp_path / "config.toml"
    path.write_text('[airport]\nicao = "lsgg"\n')
    assert load_config(path).weather_station == "LSGG"
    path.write_text('[airport]\nicao = "LSGG"\n\n[weather]\nstation = " lszh "\n')
    assert load_config(path).weather_station == "LSZH"


def test_weather_and_routes_can_be_turned_off(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[airport]\nrunways_in_use = false\n\n[weather]\nenabled = false\n\n"
        "[routes]\nenabled = false\n"
    )
    cfg = load_config(path)
    assert not cfg.airport.runways_in_use and not cfg.weather.enabled and not cfg.routes.enabled


@pytest.mark.parametrize(
    "toml",
    [
        '[airport]\nicao = "ZRH"\n',  # the IATA code
        '[airport]\nicao = ""\n',
        '[airport]\nicao = "LS ZH"\n',
        "[airport]\nruntways_in_use = true\n",  # typo
        '[airport]\nrunways_in_use = "yes"\n',
        '[weather]\nstation = "Zurich"\n',
        "[weather]\ninterval_s = 30\n",
        "[weather]\nmax_age_s = 0\n",
        "[weather]\nenabled = 1\n",
        '[weather]\nurl = "https://example.com"\n',
        '[routes]\ncache_dir = ""\n',
        '[routes]\ncache_dir = " "\n',
        "[routes]\ncache_dir = 5\n",
        "[routes]\nttl_s = 60\n",
    ],
)
def test_invalid_airport_weather_and_routes_configs_rejected(tmp_path, toml):
    path = tmp_path / "config.toml"
    path.write_text(toml)
    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_icao_code_is_named(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[airport]\nicao = "ZRH"\n')
    with pytest.raises(ConfigError, match=r"airport\.icao must be a four-character ICAO code"):
        load_config(path)
