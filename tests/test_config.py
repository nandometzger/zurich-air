from pathlib import Path

import pytest

from zurich_air.config import Config, ConfigError, load_config

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
    assert cfg.radar.range_km == 80.0  # untouched default


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
