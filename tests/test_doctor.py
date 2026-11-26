"""``zurich-air doctor``: every check without network, hardware or systemd."""

import dataclasses
import io
import json
import logging
import urllib.error
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zurich_air import __version__, doctor, schedule
from zurich_air.app import FETCH_MARGIN_KM
from zurich_air.cli import main
from zurich_air.config import Config, DisplayConfig, LocationConfig, ProviderConfig
from zurich_air.data import provider
from zurich_air.data.provider import ADSB_LOL_KEY_ENV, radius_nm
from zurich_air.doctor import CheckResult

CONFIG = Config()
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
BODY = json.dumps(
    {
        "now": 1798123320000,
        "total": 2,
        "ac": [
            {"hex": "4b0001", "flight": "SWR001 ", "lat": 47.4, "lon": 8.5},
            {"hex": "4b0002", "flight": "SWR002 ", "lat": 47.5, "lon": 8.6},
        ],
    }
).encode()
SECRET = "not-a-real-key-0123456789"


def with_display(**changes) -> Config:
    return dataclasses.replace(CONFIG, display=DisplayConfig(**changes))


# Version, config, time zone and clock


def test_version_names_both_versions():
    result = doctor.check_version("3.13.5")
    assert result == CheckResult("Version", "OK", f"zurich-air {__version__}, Python 3.13.5")


def test_config_defaults(tmp_path):
    result, config = doctor.check_config(None, cwd=tmp_path)
    assert (result.status, result.detail) == ("OK", "built-in defaults (no --config given)")
    assert config == Config()


def test_config_defaults_warn_about_an_unread_config_file(tmp_path):
    (tmp_path / "config.toml").write_text("")
    result, config = doctor.check_config(None, cwd=tmp_path)
    assert result.status == "WARN"
    assert "--config config.toml" in result.hint
    assert config == Config()


def test_config_file_is_loaded(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[location]\nname = "Basel"\n')
    result, config = doctor.check_config(path)
    assert (result.status, result.detail) == ("OK", str(path.resolve()))
    assert config.location.name == "Basel"


def test_invalid_config_fails_with_the_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[display]\nbackend = 'lcd'\n")
    result, config = doctor.check_config(path)
    assert config is None
    assert result.status == "FAIL"
    assert result.detail == "display.backend must be 'mock' or 'waveshare'"
    assert "config.example.toml" in result.hint


def test_missing_config_file_fails(tmp_path):
    path = tmp_path / "config.toml"
    result, config = doctor.check_config(path)
    assert (result.status, config) == ("FAIL", None)
    assert "not found" in result.detail
    assert result.hint.startswith("check the path, or create it: cp config.example.toml")


def test_time_zone():
    result = doctor.check_timezone(CONFIG, NOW)
    assert result == CheckResult("Time zone", "OK", "Europe/Zurich, local time 13:00 CET")


def test_time_zone_of_another_location():
    config = dataclasses.replace(CONFIG, location=LocationConfig(timezone="America/New_York"))
    result = doctor.check_timezone(config, NOW)
    assert result == CheckResult("Time zone", "OK", "America/New_York, local time 07:00 EST")


def test_unknown_time_zone_fails_the_config_check(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[location]\ntimezone = "Europe/Zurch"\n')
    result, config = doctor.check_config(path)
    assert (result.status, config) == ("FAIL", None)
    assert result.detail.startswith("location.timezone: unknown time zone 'Europe/Zurch'; ")


def test_clock_that_is_set():
    assert doctor.check_clock(NOW) == CheckResult("Clock", "OK", "2026-11-26 12:00 UTC")


def test_clock_that_is_not_set_warns():
    result = doctor.check_clock(datetime(1970, 1, 1, tzinfo=UTC))
    assert result.status == "WARN"
    assert "1970-01-01 00:00 UTC" in result.detail
    assert f"{schedule.EARLIEST_PLAUSIBLE:%Y-%m-%d}" in result.detail
    assert "timedatectl" in result.hint


# Rendering


def test_rendering_reports_size_and_time():
    timer = iter([1.0, 1.025]).__next__
    result = doctor.check_rendering(CONFIG, NOW, timer=timer)
    assert result == CheckResult("Rendering", "OK", "400x300 frame in 25 ms")


def test_rendering_failure():
    class BrokenRenderer:
        def render(self, scene):
            raise ValueError("no fonts")

    result = doctor.check_rendering(CONFIG, NOW, renderer=BrokenRenderer())
    assert result.status == "FAIL"
    assert result.detail == "ValueError: no fonts"
    assert "issues" in result.hint


# Display


def test_mock_output_dir_exists(tmp_path):
    [result] = doctor.check_display(with_display(output_dir=str(tmp_path)))
    assert result == CheckResult("Display", "OK", f"mock: latest.png in {tmp_path}")


def test_mock_output_dir_can_be_created(tmp_path):
    out = tmp_path / "a" / "b"
    [result] = doctor.check_display(with_display(output_dir=str(out)))
    assert result.status == "OK"
    assert result.detail.endswith("(created on first frame)")
    assert not out.exists()  # the doctor changes nothing


def test_mock_output_dir_relative_to_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    [result] = doctor.check_display(with_display(output_dir="previews"))
    assert str(tmp_path / "previews") in result.detail


def test_mock_output_dir_that_is_a_file_fails(tmp_path):
    out = tmp_path / "previews"
    out.write_text("")
    [result] = doctor.check_display(with_display(output_dir=str(out / "sub")))
    assert result.status == "FAIL"
    assert "not a directory" in result.detail
    assert "display.output_dir" in result.hint


def test_mock_output_dir_not_writable_fails(tmp_path):
    [result] = doctor.check_display(
        with_display(output_dir=str(tmp_path)), access=lambda path, mode: False
    )
    assert result.status == "FAIL"
    assert result.detail == f"mock: cannot write to {tmp_path}"


def waveshare(tmp_path, *, spi=True, missing=(), available=True, monkeypatch):
    device = tmp_path / "spidev0.0"
    if spi:
        device.touch()

    def import_module(name):
        if name in missing:
            raise ImportError(f"No module named {name!r}")

    if available:
        monkeypatch.setattr(doctor, "AVAILABLE_BACKENDS", ("mock", "waveshare"))
    config = with_display(backend="waveshare")
    return doctor.check_display(config, spi_device=device, import_module=import_module)


def test_panel_prerequisites_in_place(tmp_path, monkeypatch):
    results = waveshare(tmp_path, monkeypatch=monkeypatch)
    assert [(r.name, r.status) for r in results] == [
        ("Display", "OK"),
        ("SPI", "OK"),
        ("Libraries", "OK"),
    ]
    assert results[0].detail == "waveshare 400x300, rotation 0"


def test_panel_without_spi_fails(tmp_path, monkeypatch):
    _, spi, _ = waveshare(tmp_path, spi=False, monkeypatch=monkeypatch)
    assert spi.status == "FAIL"
    assert spi.hint == "sudo raspi-config nonint do_spi 0 && sudo reboot"


@pytest.mark.parametrize("missing", [("spidev",), ("gpiozero",), ("spidev", "gpiozero")])
def test_panel_without_libraries_fails(tmp_path, monkeypatch, missing):
    *_, libraries = waveshare(tmp_path, missing=missing, monkeypatch=monkeypatch)
    assert libraries.status == "FAIL"
    assert libraries.detail == f"cannot import {' and '.join(missing)}"
    assert libraries.hint.startswith(
        "sudo apt install python3-spidev python3-lgpio python3-gpiozero"
    )


def test_panel_backend_without_a_driver_fails(tmp_path, monkeypatch):
    display, spi, libraries = waveshare(tmp_path, available=False, monkeypatch=monkeypatch)
    assert display.status == "FAIL"
    assert 'display.backend = "mock"' in display.hint
    assert spi.status == libraries.status == "OK"  # still worth knowing


# Data sources


class Clock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


class FakeOpener:
    """Answers like urllib.request.urlopen, per source: a body or an exception to raise."""

    def __init__(self, clock: Clock, delay_s: float = 0.2, **outcomes) -> None:
        self.clock = clock
        self.delay_s = delay_s
        self.outcomes = outcomes
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        self.clock.t += self.delay_s
        outcome = self.outcomes["adsb_fi" if "adsb.fi" in request.full_url else "adsb_lol"]
        if isinstance(outcome, BaseException):
            raise outcome
        response = io.BytesIO(outcome)
        response.headers = {}
        return response


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.invalid", code, "error", None, None)


def sources(env=None, config=CONFIG, **outcomes):
    clock = Clock()
    opener = FakeOpener(clock, **outcomes)
    results = doctor.check_sources(config, env=env or {}, opener=opener, timer=clock)
    return results, opener


def test_sources_report_aircraft_and_latency():
    results, opener = sources(adsb_fi=BODY, adsb_lol=BODY)
    assert results == [
        CheckResult("Source adsb.fi", "OK", "2 aircraft in 200 ms"),
        CheckResult("Source adsb.lol", "OK", "2 aircraft in 200 ms; no API key"),
    ]
    # Asked for the same area as the app.
    radius = radius_nm(CONFIG.radar.range_km + FETCH_MARGIN_KM)
    assert all(r.full_url.endswith(f"/{radius}") for r in opener.requests)


def test_api_key_is_used_but_never_shown(caplog):
    env = {ADSB_LOL_KEY_ENV: SECRET}
    with caplog.at_level(logging.DEBUG):
        results, opener = sources(env, adsb_fi=BODY, adsb_lol=http_error(403))
    assert results[1].detail == "HTTP 403; API key set"
    assert ADSB_LOL_KEY_ENV in results[1].hint
    assert opener.requests[1].get_header("X-api-key") == SECRET
    assert SECRET not in doctor.report(results)
    assert SECRET not in caplog.text


def test_rate_limit_is_only_a_warning():
    results, _ = sources(adsb_fi=http_error(429), adsb_lol=http_error(429))
    assert [r.status for r in results] == ["WARN", "WARN"]
    assert results[0].detail == "rate limited (HTTP 429)"
    assert doctor.exit_status(results) == 0


def test_a_failing_source_is_a_warning_while_another_works():
    error = urllib.error.URLError("Name or service not known")
    results, _ = sources(adsb_fi=error, adsb_lol=BODY)
    assert [r.status for r in results] == ["WARN", "OK"]
    assert results[0].detail == "network error: Name or service not known"
    assert results[0].hint == "check the internet connection: ping -c 3 opendata.adsb.fi"


def test_sources_fail_when_none_delivers():
    results, _ = sources(adsb_fi=TimeoutError(), adsb_lol=http_error(503))
    assert [(r.status, r.detail) for r in results] == [
        ("FAIL", "timed out"),
        ("FAIL", "HTTP 503; no API key"),
    ]
    assert "ping" in results[0].hint and "error" in results[1].hint


def test_only_the_configured_sources_are_asked():
    config = dataclasses.replace(CONFIG, provider=ProviderConfig(sources=("adsb_lol",)))
    results, opener = sources(config=config, adsb_lol=BODY)
    assert [r.name for r in results] == ["Source adsb.lol"]
    assert len(opener.requests) == 1


def test_provider_warnings_are_held_back_and_restored(caplog):
    level = provider.log.level
    with caplog.at_level(logging.INFO):
        sources(adsb_fi=http_error(429), adsb_lol=TimeoutError())
    assert not [r for r in caplog.records if r.name == provider.log.name]
    assert provider.log.level == level


# Service


def systemctl(active: str, enabled: str):
    answers = {"is-active": active, "is-enabled": enabled}
    return lambda command, unit: answers[command] if unit == "zurich-air" else ""


def service(active="active", enabled="enabled"):
    return doctor.check_service(
        system="linux",
        which=lambda name: f"/usr/bin/{name}",
        runtime=Path("/"),  # any directory that exists
        systemctl=systemctl(active, enabled),
    )


def test_service_running():
    result = service()
    assert result == CheckResult("Service", "OK", "active, enabled")


@pytest.mark.parametrize("active", ["inactive", "failed", "activating"])
def test_service_not_running_warns(active):
    result = service(active)
    assert (result.status, result.detail) == ("WARN", f"{active}, enabled")
    assert "journalctl -u zurich-air -e" in result.hint


@pytest.mark.parametrize("enabled", ["", "not-found"])
def test_service_not_installed_warns(enabled):
    result = service("inactive", enabled)
    assert (result.status, result.detail) == ("WARN", "zurich-air is not installed")
    assert "install-pi.sh" in result.hint


def test_service_not_enabled_warns():
    result = service("active", "disabled")
    assert result.status == "WARN"
    assert result.hint == "to start it at boot: sudo systemctl enable zurich-air"


def test_service_skipped_without_systemd(tmp_path):
    def never(*args):
        raise AssertionError("systemctl must not run")

    assert doctor.check_service(system="darwin", systemctl=never).status == "SKIP"
    no_binary = doctor.check_service(system="linux", which=lambda name: None, systemctl=never)
    assert no_binary.status == "SKIP"
    not_booted = doctor.check_service(
        system="linux",
        which=lambda name: f"/usr/bin/{name}",
        runtime=tmp_path / "missing",
        systemctl=never,
    )
    assert not_booted == CheckResult("Service", "SKIP", "no systemd here")


# Output and exit status


def test_report_aligns_columns_and_adds_fixes():
    text = doctor.report(
        [
            CheckResult("Version", "OK", "zurich-air 0.1.0"),
            CheckResult("Clock", "WARN", "not set", "timedatectl status"),
            CheckResult("SPI", "FAIL", "off", "sudo raspi-config nonint do_spi 0"),
            CheckResult("Service", "SKIP", "no systemd here", "never shown"),
        ]
    )
    assert text == (
        "OK    Version  zurich-air 0.1.0\n"
        "WARN  Clock    not set\n"
        "               fix: timedatectl status\n"
        "FAIL  SPI      off\n"
        "               fix: sudo raspi-config nonint do_spi 0\n"
        "SKIP  Service  no systemd here\n"
        "\n"
        "1 failure, 1 warning (1 skipped): see the fix: lines above."
    )


def test_summary_when_all_is_well():
    ok = CheckResult("Version", "OK", "")
    assert doctor.summary([ok, ok]) == "All 2 checks passed."
    skip = CheckResult("Service", "SKIP", "")
    assert doctor.summary([ok, ok, skip]) == "All 2 checks passed (1 skipped)."
    warn = CheckResult("Clock", "WARN", "", "fix")
    assert doctor.summary([ok, warn, warn]) == "no failures, 2 warnings: see the fix: lines above."


def test_exit_status_fails_only_on_failures():
    ok, warn = CheckResult("A", "OK", ""), CheckResult("B", "WARN", "", "x")
    fail, skip = CheckResult("C", "FAIL", "", "y"), CheckResult("D", "SKIP", "")
    assert doctor.exit_status([ok, warn, skip]) == 0
    assert doctor.exit_status([ok, fail]) == 1


# All checks together, and the command


@pytest.fixture
def no_service(monkeypatch):
    skip = CheckResult("Service", "SKIP", "no systemd here")
    monkeypatch.setattr(doctor, "check_service", lambda: skip)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no network requests offline")

    monkeypatch.setattr(doctor, "make_source", refuse)


def write_config(tmp_path, text=None) -> Path:
    path = tmp_path / "config.toml"
    out = json.dumps(str(tmp_path / "previews"))  # a valid TOML basic string, too
    path.write_text(text if text is not None else f"[display]\noutput_dir = {out}\n")
    return path


def test_all_checks_in_order_offline(tmp_path, no_service, no_network):
    results = doctor.run_checks(write_config(tmp_path), offline=True, now=NOW)
    assert [(r.name, r.status) for r in results] == [
        ("Version", "OK"),
        ("Config", "OK"),
        ("Time zone", "OK"),
        ("Clock", "OK"),
        ("Rendering", "OK"),
        ("Display", "OK"),
        ("Source adsb.fi", "SKIP"),
        ("Source adsb.lol", "SKIP"),
        ("Service", "SKIP"),
    ]
    assert results[6].detail == "--offline"


def test_online_checks_ask_the_sources(tmp_path, no_service, monkeypatch):
    fetched = CheckResult("Source adsb.fi", "OK", "2 aircraft in 200 ms")
    monkeypatch.setattr(doctor, "check_sources", lambda config: [fetched])
    results = doctor.run_checks(write_config(tmp_path), now=NOW)
    assert fetched in results


def test_checks_that_need_a_valid_config_are_skipped(tmp_path, no_service, no_network):
    path = write_config(tmp_path, "[nope]\n")
    results = doctor.run_checks(path, now=NOW)
    statuses = {r.name: (r.status, r.detail) for r in results}
    assert statuses["Config"] == ("FAIL", "unknown config key(s): nope")
    for name in ("Time zone", "Rendering", "Display", "Data sources"):
        assert statuses[name] == ("SKIP", "needs a valid config")
    assert statuses["Clock"][0] == "OK"
    assert doctor.exit_status(results) == 1


def test_unknown_time_zone_skips_the_checks_that_need_it(tmp_path, no_service):
    path = write_config(tmp_path, '[location]\ntimezone = "Mars/Olympus_Mons"\n')
    results = doctor.run_checks(path, offline=True, now=NOW)
    statuses = {r.name: (r.status, r.detail) for r in results}
    assert statuses["Config"][0] == "FAIL"
    assert "unknown time zone 'Mars/Olympus_Mons'" in statuses["Config"][1]
    for name in ("Time zone", "Rendering"):
        assert statuses[name] == ("SKIP", "needs a valid config")


def test_command_prints_the_report(tmp_path, capsys, no_service, no_network):
    path = write_config(tmp_path)
    assert main(["doctor", "--offline", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"OK    Config           {path.resolve()}\n" in out
    assert "SKIP  Source adsb.fi   --offline\n" in out
    assert out.rstrip().endswith("checks passed (3 skipped).")


def test_command_exits_1_on_a_failure(tmp_path, capsys, no_service, no_network):
    path = write_config(tmp_path, "[display]\nrotation = 45\n")
    assert main(["doctor", "--config", str(path)]) == 1  # reported, not a usage error
    out = capsys.readouterr().out
    assert "FAIL  Config" in out and "display.rotation must be one of" in out


def test_command_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--offline" in out and "--config" in out and "WARN" in out
    assert main([]) == 0
    assert "doctor" in capsys.readouterr().out
