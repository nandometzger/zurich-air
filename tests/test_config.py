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
