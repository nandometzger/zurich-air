"""``zurich-air doctor``: a setup self-check with one line per check.

Each check is a small function that returns a :class:`CheckResult` (the display and the
data sources return one per item). Time, network, file system and process access are
parameters with real defaults, so that tests can replace them. :func:`run_checks` runs
every check in order, :func:`report` formats the results and :func:`exit_status` turns
them into the command's exit status.

Nothing here changes the system, and no secret is ever printed: of an API key, only
whether one is set.
"""

from __future__ import annotations

import importlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from zurich_air import __version__, schedule
from zurich_air.app import FETCH_MARGIN_KM
from zurich_air.config import Config, ConfigError, load_config
from zurich_air.data import metar, provider
from zurich_air.data.airports import lookup
from zurich_air.data.metar import METAR_URL, MetarSource
from zurich_air.data.provider import PRESETS, ProviderError, make_source
from zurich_air.display import AVAILABLE_BACKENDS
from zurich_air.extras import WEATHER_TIMEOUT_S
from zurich_air.pipeline import build_scene
from zurich_air.processing.geo import distance_km
from zurich_air.rendering import formatting
from zurich_air.rendering.renderer import Renderer

Status = Literal["OK", "WARN", "FAIL", "SKIP"]
STATUSES: tuple[Status, ...] = ("OK", "WARN", "FAIL", "SKIP")

SERVICE = "zurich-air"
CONFIG_NAME = "config.toml"  # the installer's config file, in the app directory
SPI_DEVICE = Path("/dev/spidev0.0")  # SPI0, chip select 0: the e-paper HAT
SYSTEMD_RUNTIME = Path("/run/systemd/system")  # exists only when systemd is running
HARDWARE_MODULES = ("spidev", "gpiozero")
# HARDWARE_PACKAGES in scripts/install-pi.sh; gpiozero uses lgpio as its pin library.
HARDWARE_PACKAGES = ("python3-spidev", "python3-lgpio", "python3-gpiozero")
SYSTEMCTL_TIMEOUT_S = 10.0

NEEDS_CONFIG = "needs a valid config"
AIRPORT_MISMATCH_KM = 5.0  # airport.icao and the configured position disagree beyond this
REPORT_HINT = "reinstall, or report it with this output: github.com/nandometzger/zurich-air/issues"

Timer = Callable[[], float]  # seconds, for measuring durations
Systemctl = Callable[..., str]  # systemctl(*args) -> its standard output, stripped


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    hint: str | None = None  # for a WARN or FAIL: how to fix it, in one line


def run_checks(
    config_path: Path | None, *, offline: bool = False, now: datetime | None = None
) -> list[CheckResult]:
    """Every check, in order. A check that needs an earlier one to pass is skipped."""
    now = now or datetime.now(UTC)
    config_result, config = check_config(config_path)
    results = [check_version(), config_result]

    timezone = check_timezone(config, now) if config else skipped("Time zone", NEEDS_CONFIG)
    results += [timezone, check_clock(now)]
    results.append(check_rendering(config, now) if config else skipped("Rendering", NEEDS_CONFIG))
    results += check_display(config) if config else [skipped("Display", NEEDS_CONFIG)]

    if config is None:
        names = ("Airport", "Route cache", "Data sources", "Weather")
        results += [skipped(name, NEEDS_CONFIG) for name in names]
    else:
        results += [check_airport(config), check_route_cache(config)]
        if offline:
            results += [skipped(source_name(s), "--offline") for s in config.provider.sources]
            results.append(skipped("Weather", "--offline"))
        else:
            results += check_sources(config)
            results.append(check_weather(config, now))
    results.append(check_service())
    return results


def skipped(name: str, reason: str) -> CheckResult:
    return CheckResult(name, "SKIP", reason)


def check_version(python: str | None = None) -> CheckResult:
    """The versions of Zurich Air and of the Python running it."""
    python = python or platform.python_version()
    return CheckResult("Version", "OK", f"zurich-air {__version__}, Python {python}")


def check_config(path: Path | None, cwd: Path | None = None) -> tuple[CheckResult, Config | None]:
    """Load the config as the other commands do: from *path*, else the built-in defaults.

    Without *path*, warns if *cwd* holds a config.toml, which would then go unchecked.
    """
    try:
        config = load_config(path)
    except ConfigError as exc:
        if path is not None and not path.exists():
            hint = f"check the path, or create it: cp config.example.toml {path}"
        else:
            hint = "edit the file to fix that; config.example.toml shows every key (all optional)"
        return CheckResult("Config", "FAIL", str(exc), hint), None
    if path is not None:
        return CheckResult("Config", "OK", str(path.resolve())), config
    local = (cwd or Path.cwd()) / CONFIG_NAME
    if local.is_file():
        detail = f"built-in defaults; {CONFIG_NAME} here was not read"
        hint = f"to check it as well: zurich-air doctor --config {CONFIG_NAME}"
        return CheckResult("Config", "WARN", detail, hint), config
    return CheckResult("Config", "OK", "built-in defaults (no --config given)"), config


def check_timezone(config: Config, now: datetime) -> CheckResult:
    """The local time in ``location.timezone``.

    Loading the config has already rejected a zone the system does not know, so the
    Config check reports that.
    """
    name = config.location.timezone
    local = now.astimezone(ZoneInfo(name))
    return CheckResult("Time zone", "OK", f"{name}, local time {local:%H:%M %Z}")


def check_clock(now: datetime) -> CheckResult:
    """The system clock has been set: the app's own rule, see schedule.py."""
    shown = f"{now.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
    if schedule.clock_is_plausible(now):
        return CheckResult("Clock", "OK", shown)
    earliest = f"{schedule.EARLIEST_PLAUSIBLE:%Y-%m-%d}"
    return CheckResult(
        "Clock",
        "WARN",
        f"reads {shown}, before {earliest}: not set yet, so the app waits",
        "the clock is set over the network: check the connection, then: timedatectl status",
    )


def check_rendering(
    config: Config,
    now: datetime,
    *,
    renderer: Renderer | None = None,
    timer: Timer = time.perf_counter,
) -> CheckResult:
    """Render the frame shown before any data arrives, with the real renderer, and time it."""
    start = timer()
    try:
        scene = build_scene(None, None, config, now)
        image = (renderer or Renderer(config)).render(scene)
    except Exception as exc:  # whatever it is, the app could not draw a frame
        return CheckResult("Rendering", "FAIL", _describe(exc), REPORT_HINT)
    elapsed_ms = (timer() - start) * 1000
    return CheckResult(
        "Rendering", "OK", f"{image.width}x{image.height} frame in {elapsed_ms:.0f} ms"
    )


def check_display(
    config: Config,
    *,
    spi_device: Path = SPI_DEVICE,
    import_module: Callable[[str], Any] = importlib.import_module,
    access: Callable[[Path, int], bool] = os.access,
) -> list[CheckResult]:
    """The mock backend can write its PNG files; the panel's prerequisites are in place.

    The panel itself is not touched: the service may be driving it right now.
    """
    display = config.display
    if display.backend == "mock":
        return [_check_output_dir(Path(display.output_dir), access)]
    name = f"{display.backend} {display.width}x{display.height}, rotation {display.rotation}"
    if display.backend in AVAILABLE_BACKENDS:
        backend = CheckResult("Display", "OK", name)
    else:
        backend = CheckResult(
            "Display",
            "FAIL",
            f"{name}: this version has no driver for it yet",
            'set display.backend = "mock" until it has',
        )
    return [backend, check_spi(spi_device), check_hardware_modules(import_module)]


def _check_output_dir(path: Path, access: Callable[[Path, int], bool]) -> CheckResult:
    target = path.absolute()  # like the app: relative to the working directory
    hint = "set display.output_dir to a directory you can write to, such as previews"
    problem = _unwritable(target, access)
    if problem:
        return CheckResult("Display", "FAIL", f"mock: {problem}", hint)
    if target.is_dir():
        return CheckResult("Display", "OK", f"mock: latest.png in {target}")
    return CheckResult("Display", "OK", f"mock: latest.png in {target} (created on first frame)")


def _unwritable(target: Path, access: Callable[[Path, int], bool]) -> str | None:
    """Why the directory *target* cannot be written to (or created), or None if it can."""
    existing = next(p for p in (target, *target.parents) if p.exists())
    if not existing.is_dir():
        return f"{existing} is not a directory"
    if not access(existing, os.W_OK | os.X_OK):
        return f"cannot write to {existing}"
    return None


def check_airport(config: Config) -> CheckResult:
    """``airport.icao`` is in the bundled airport data, near the configured position.

    The bundled runways are what the runways in use are inferred from.
    """
    airport = config.airport
    icao = airport.icao.upper()
    bundled = lookup(icao)
    if bundled is None:
        return CheckResult(
            "Airport",
            "WARN",
            f"{icao} is not in the bundled airport data: runways in use are not shown",
            "set airport.icao to the airport's four-letter ICAO code, such as LSZH",
        )
    detail = f"{icao} {bundled.name}, {_count(len(bundled.runways), 'runway')}"
    off_km = distance_km(airport.latitude, airport.longitude, bundled.lat, bundled.lon)
    if off_km > AIRPORT_MISMATCH_KM:
        return CheckResult(
            "Airport",
            "WARN",
            f"{detail}, but {off_km:.0f} km from airport.latitude/longitude",
            "airport.icao and the airport's position must describe the same airport",
        )
    if airport.runways_in_use and not bundled.runways:
        return CheckResult("Airport", "WARN", f"{detail}: runways in use are not shown")
    return CheckResult("Airport", "OK", detail)


def check_route_cache(
    config: Config, *, access: Callable[[Path, int], bool] = os.access
) -> CheckResult:
    """The route files can be stored in ``routes.cache_dir``; how many are there."""
    if not config.routes.enabled:
        return skipped("Route cache", "routes.enabled = false")
    target = Path(config.routes.cache_dir).absolute()  # relative to the working directory
    problem = _unwritable(target, access)
    if problem:
        return CheckResult(
            "Route cache",
            "WARN",
            f"{problem}: no routes are shown",
            "set routes.cache_dir to a directory you can write to, such as cache/routes",
        )
    if not target.is_dir():
        return CheckResult("Route cache", "OK", f"{target} (created on the first download)")
    files = sum(1 for _ in target.glob("*.csv"))
    return CheckResult("Route cache", "OK", f"{target}, {files} airline files")


def check_spi(device: Path = SPI_DEVICE) -> CheckResult:
    """SPI is enabled, as the e-paper panel needs."""
    if device.exists():
        return CheckResult("SPI", "OK", f"{device} present")
    return CheckResult(
        "SPI",
        "FAIL",
        f"no {device}: SPI is off",
        "sudo raspi-config nonint do_spi 0 && sudo reboot",
    )


def check_hardware_modules(
    import_module: Callable[[str], Any] = importlib.import_module,
) -> CheckResult:
    """The Python bindings the panel driver needs can be imported."""
    missing = []
    for module in HARDWARE_MODULES:
        try:
            import_module(module)
        except Exception:  # ImportError, or a binding built for another Python
            missing.append(module)
    if not missing:
        return CheckResult("Libraries", "OK", f"{' and '.join(HARDWARE_MODULES)} import")
    return CheckResult(
        "Libraries",
        "FAIL",
        f"cannot import {' and '.join(missing)}",
        f"sudo apt install {' '.join(HARDWARE_PACKAGES)}, then re-run scripts/install-pi.sh",
    )


def source_name(source: str) -> str:
    """The check's name for the data source *source* (a ``provider.sources`` entry)."""
    return f"Source {PRESETS[source].label}"


def check_sources(
    config: Config,
    *,
    env: Mapping[str, str] | None = None,
    opener: provider.Opener = urllib.request.urlopen,
    timer: Timer = time.monotonic,
) -> list[CheckResult]:
    """Fetch once from every configured source, as the app would, and time it.

    A source that fails is a warning while another one works, since the app falls back
    to it; it fails the check only when no source delivers. Rate limiting is always just
    a warning: the app waits it out.
    """
    env = os.environ if env is None else env
    location = config.location
    radius_km = config.radar.range_km + FETCH_MARGIN_KM
    outcomes: list[tuple[str, ProviderError | str]] = []
    with _quiet(provider.log):  # its warnings would repeat the results
        for name in config.provider.sources:
            source = make_source(
                name, timeout_s=config.provider.timeout_s, env=env, opener=opener, clock=timer
            )
            start = timer()
            try:
                snapshot = source.fetch(location.latitude, location.longitude, radius_km)
            except ProviderError as exc:
                outcomes.append((name, exc))
                continue
            elapsed_ms = (timer() - start) * 1000
            outcomes.append((name, f"{len(snapshot.aircraft)} aircraft in {elapsed_ms:.0f} ms"))
    any_data = any(isinstance(outcome, str) for _, outcome in outcomes)
    return [_source_result(name, outcome, any_data, env) for name, outcome in outcomes]


def _source_result(
    name: str, outcome: ProviderError | str, any_data: bool, env: Mapping[str, str]
) -> CheckResult:
    preset = PRESETS[name]
    key = ""
    if preset.key_env:  # whether a key is set, never the key
        key = "; API key set" if env.get(preset.key_env, "").strip() else "; no API key"
    if isinstance(outcome, str):
        return CheckResult(source_name(name), "OK", outcome + key)
    reason = str(outcome).removeprefix(f"{preset.label}: ")
    cause = outcome.__cause__
    code = cause.code if isinstance(cause, urllib.error.HTTPError) else None
    if code == 429:
        hint = "the app waits and tries again on its own; check again in a few minutes"
        return CheckResult(source_name(name), "WARN", f"rate limited ({reason}){key}", hint)
    if code in (401, 403) and preset.key_env:
        hint = f"check {preset.key_env} in the .env file (or remove it)"
    elif code is not None:
        hint = "the service answered with an error, usually for a short while: check later"
    else:
        host = urllib.parse.urlsplit(preset.url_template).hostname
        hint = f"check the internet connection: ping -c 3 {host}"
    return CheckResult(source_name(name), "WARN" if any_data else "FAIL", reason + key, hint)


def check_weather(
    config: Config,
    now: datetime,
    *,
    opener: provider.Opener = urllib.request.urlopen,
    timer: Timer = time.monotonic,
) -> CheckResult:
    """Fetch the METAR once, as the app would, and time it. Without it the display only
    lacks the weather, so a failure is a warning."""
    if not config.weather.enabled:
        return skipped("Weather", "weather.enabled = false")
    station = config.weather_station
    source = MetarSource(station, timeout_s=WEATHER_TIMEOUT_S, opener=opener, clock=timer)
    start = timer()
    with _quiet(metar.log):  # its warning would repeat the result
        observation = source.fetch()
    elapsed_ms = (timer() - start) * 1000
    if observation is None:
        if source.last_error == "no usable observation":
            hint = f"{station} may not issue METARs: set weather.station to a station that does"
        else:
            host = urllib.parse.urlsplit(METAR_URL).hostname
            hint = f"check the internet connection: ping -c 3 {host}"
        return CheckResult("Weather", "WARN", f"METAR {station}: {source.last_error}", hint)
    age_min = observation.age_s(now) / 60
    summary = formatting.SEPARATOR.join(formatting.airport_parts(observation, None))
    detail = f"METAR {station} from {age_min:.0f} min ago in {elapsed_ms:.0f} ms: {summary}"
    if observation.age_s(now) > config.weather.max_age_s:
        hint = f"{station} may not report at all hours: set weather.station to one that does"
        return CheckResult("Weather", "WARN", f"{detail}; too old to show", hint)
    return CheckResult("Weather", "OK", detail)


@contextmanager
def _quiet(logger: logging.Logger) -> Iterator[None]:
    """Hold back *logger*'s warnings (but not debug output, if asked for) for a while."""
    level = logger.level
    if logger.getEffectiveLevel() > logging.DEBUG:
        logger.setLevel(logging.ERROR)
    try:
        yield
    finally:
        logger.setLevel(level)


def check_service(
    *,
    system: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    runtime: Path = SYSTEMD_RUNTIME,
    systemctl: Systemctl | None = None,
) -> CheckResult:
    """The systemd service is installed, enabled at boot and running (Linux only)."""
    if not system.startswith("linux") or which("systemctl") is None or not runtime.is_dir():
        return skipped("Service", "no systemd here")
    systemctl = systemctl or _systemctl
    active = systemctl("is-active", SERVICE) or "unknown"
    enabled = systemctl("is-enabled", SERVICE)
    if enabled in ("", "not-found"):  # older systemd prints nothing for a missing unit
        hint = "on the Pi, scripts/install-pi.sh installs and starts it"
        return CheckResult("Service", "WARN", f"{SERVICE} is not installed", hint)
    detail = f"{active}, {enabled}"
    if active != "active":
        hint = f"see why: journalctl -u {SERVICE} -e; then: sudo systemctl restart {SERVICE}"
        return CheckResult("Service", "WARN", detail, hint)
    if enabled != "enabled":
        hint = f"to start it at boot: sudo systemctl enable {SERVICE}"
        return CheckResult("Service", "WARN", f"{detail}: it will not start at boot", hint)
    return CheckResult("Service", "OK", detail)


def _systemctl(*args: str) -> str:
    try:
        done = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=SYSTEMCTL_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip()


def report(results: Sequence[CheckResult]) -> str:
    """One line per result, status and name in aligned columns, a ``fix:`` line under
    every warning and failure that has a hint, and a summary at the end."""
    width = max(len(r.name) for r in results)
    status_width = max(len(s) for s in STATUSES)
    indent = " " * (status_width + 2 + width + 2)
    lines = []
    for r in results:
        lines.append(f"{r.status:<{status_width}}  {r.name:<{width}}  {r.detail}".rstrip())
        if r.hint and r.status in ("WARN", "FAIL"):
            lines.append(f"{indent}fix: {r.hint}")
    lines += ["", summary(results)]
    return "\n".join(lines)


def summary(results: Sequence[CheckResult]) -> str:
    """``All 9 checks passed.``, or how many warnings and failures there were."""
    failed = sum(r.status == "FAIL" for r in results)
    warned = sum(r.status == "WARN" for r in results)
    skipped_ = sum(r.status == "SKIP" for r in results)
    extra = f" ({skipped_} skipped)" if skipped_ else ""
    if not failed and not warned:
        return f"All {len(results) - skipped_} checks passed{extra}."
    parts = [_count(failed, "failure"), _count(warned, "warning")]
    return f"{', '.join(parts)}{extra}: see the fix: lines above."


def exit_status(results: Sequence[CheckResult]) -> int:
    """0 if no check failed, else 1; warnings do not count."""
    return 1 if any(r.status == "FAIL" for r in results) else 0


def _count(n: int, noun: str) -> str:
    return f"{n or 'no'} {noun}{'' if n == 1 else 's'}"


def _describe(exc: BaseException, limit: int = 200) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text if len(text) <= limit else text[: limit - 3] + "..."
