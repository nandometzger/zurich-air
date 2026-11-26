"""Static checks of the Raspberry Pi deployment files: no Pi, systemd or network needed."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from zurich_air import doctor
from zurich_air.app import WATCHDOG_PING_S
from zurich_air.config import Config
from zurich_air.extras import MAX_EXTRA_S

ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "system" / "zurich-air.service"
INSTALLER = ROOT / "scripts" / "install-pi.sh"
GUIDE = ROOT / "docs" / "raspberry-pi.md"
PLACEHOLDERS = {"@USER@", "@APP_DIR@"}

Unit = dict[str, dict[str, list[str]]]


def parse_unit(text: str) -> Unit:
    """Section → key → values; a key may repeat, as systemd allows."""
    sections: Unit = {}
    section: dict[str, list[str]] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = sections.setdefault(line[1:-1], {})
            continue
        key, sep, value = line.partition("=")
        assert section is not None and sep, f"not a key=value line inside a section: {raw!r}"
        section.setdefault(key.strip(), []).append(value.strip())
    return sections


def words(values: list[str]) -> list[str]:
    return [word for value in values for word in value.split()]


@pytest.fixture(scope="module")
def unit() -> Unit:
    return parse_unit(UNIT.read_text())


def test_unit_starts_after_network_is_online(unit):
    assert "network-online.target" in words(unit["Unit"]["Wants"])
    assert "network-online.target" in words(unit["Unit"]["After"])


def test_unit_does_not_wait_for_time_sync(unit):
    # Waiting for the first NTP sync would keep the service from starting while the network
    # is down, leaving a stale frame on the panel; the app checks the clock itself.
    for key in ("Wants", "After", "Requires"):
        assert "time-sync.target" not in words(unit["Unit"].get(key, []))
    assert "systemd-time-wait-sync" not in INSTALLER.read_text()


def seconds(timespan: str) -> float:
    """A systemd time span such as ``5min`` or ``90s`` (single unit only)."""
    match = re.fullmatch(r"(\d+)\s*(min|s)?", timespan)
    assert match, f"unsupported time span: {timespan!r}"
    return int(match[1]) * (60 if match[2] == "min" else 1)


def test_unit_uses_the_watchdog(unit):
    service = unit["Service"]
    assert service["Type"] == ["notify"]  # the app sends READY=1
    assert service["NotifyAccess"] == ["main"]
    # The app pings at least every WATCHDOG_PING_S while sleeping and after every
    # update, which takes about a minute at worst: leave room for both, twice over.
    assert seconds(service["WatchdogSec"][0]) >= 2 * (WATCHDOG_PING_S + 60)
    # Every request timing out: each traffic source, then the METAR and route downloads.
    config = Config()
    worst = len(config.provider.sources) * config.provider.timeout_s + MAX_EXTRA_S
    assert worst <= config.update.interval_s - 20  # leaves time for DNS and the panel


def test_unit_keeps_the_working_directory_writable(unit):
    # The previews and the route cache live in the working directory, the app directory
    # in the user's home: these settings would make it read-only.
    service = unit["Service"]
    assert service["WorkingDirectory"] == ["@APP_DIR@"]
    for key in ("ProtectHome", "ReadOnlyPaths", "InaccessiblePaths", "TemporaryFileSystem"):
        assert key not in service
    assert service["ProtectSystem"] == ["full"]  # strict would need ReadWritePaths=
    config = Config()
    for path in (config.routes.cache_dir, config.display.output_dir):
        assert not Path(path).is_absolute()  # relative to the working directory


def test_downloaded_routes_are_not_committed():
    ignored = (ROOT / ".gitignore").read_text().splitlines()
    assert Path(Config().routes.cache_dir).parts[0] + "/" in ignored


def test_unit_restarts_without_spinning(unit):
    service = unit["Service"]
    assert service["Restart"] == ["always"]
    assert service["RestartSec"]
    assert unit["Unit"]["StartLimitIntervalSec"] and unit["Unit"]["StartLimitBurst"]


def test_unit_runs_unprivileged_with_hardware_groups(unit):
    service = unit["Service"]
    assert service["User"] == ["@USER@"]
    assert {"spi", "gpio"} <= set(words(service["SupplementaryGroups"]))
    assert service["WorkingDirectory"] == ["@APP_DIR@"]
    assert service["ExecStart"] == [
        "@APP_DIR@/.venv/bin/zurich-air run --config @APP_DIR@/config.toml"
    ]


def test_unit_logs_unbuffered_to_the_journal(unit):
    service = unit["Service"]
    assert "PYTHONUNBUFFERED=1" in words(service["Environment"])
    assert service.get("StandardOutput", ["journal"]) == ["journal"]


def test_unit_hardening_keeps_spi_and_gpio_reachable(unit):
    service = unit["Service"]
    assert service["NoNewPrivileges"] == ["yes"]
    assert service["ProtectSystem"] == ["full"]
    assert service["PrivateTmp"] == ["yes"]
    # Each of these hides /dev/spidev* and /dev/gpiochip* (ProtectClock implies DeviceAllow).
    for key in ("PrivateDevices", "DevicePolicy", "DeviceAllow", "ProtectClock"):
        assert key not in service


def test_unit_starts_at_boot(unit):
    assert unit["Install"]["WantedBy"] == ["multi-user.target"]


def test_unit_placeholders_are_the_ones_the_installer_fills_in():
    assert set(re.findall(r"@[A-Z_]+@", UNIT.read_text())) == PLACEHOLDERS
    script = INSTALLER.read_text()
    for placeholder in PLACEHOLDERS:
        assert f"s|{placeholder}|" in script


def test_installer_is_an_executable_strict_bash_script():
    script = INSTALLER.read_text()
    assert script.startswith("#!/usr/bin/env bash\n")
    assert "\nset -euo pipefail\n" in script
    assert INSTALLER.stat().st_mode & 0o111, "scripts/install-pi.sh must be executable"


def test_installer_keeps_the_deployment_decisions():
    script = INSTALLER.read_text()
    assert "--system-site-packages" in script  # apt's spidev/lgpio/gpiozero stay importable
    assert "sync --locked --no-dev" in script
    assert "id -un" in script  # the service user is whoever runs it, never a fixed name


def installer_steps() -> list[str]:
    """The commands main() runs, in order."""
    main = re.search(r"^main\(\) \{\n(.*?)^\}", INSTALLER.read_text(), re.MULTILINE | re.DOTALL)
    assert main, "install-pi.sh has no main() function"
    return re.findall(r"^  ([a-z_]+)\b", main[1], re.MULTILINE)


def test_installer_checks_the_setup_once_the_service_runs():
    steps = installer_steps()
    assert steps.index("install_service") < steps.index("run_doctor") < steps.index("next_steps")
    # Under `set -e` a failed check would abort the install, unless it is a condition.
    script = INSTALLER.read_text()
    assert re.search(r"^\s*if .*\bzurich-air doctor --config\b", script, re.MULTILINE)


def test_installer_names_the_hardware_packages_the_doctor_suggests():
    match = re.search(r"^HARDWARE_PACKAGES=\(([^)]*)\)", INSTALLER.read_text(), re.MULTILINE)
    assert match, "HARDWARE_PACKAGES not found in install-pi.sh"
    assert set(match[1].split()) == set(doctor.HARDWARE_PACKAGES)


def test_installer_never_traces_commands():
    # `set -x` would echo every command, including anything secret it handles.
    assert not re.search(r"^\s*set\s+-\w*x", INSTALLER.read_text(), re.MULTILINE)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
def test_installer_parses():
    result = subprocess.run(["bash", "-n", str(INSTALLER)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck is not installed")
def test_installer_passes_shellcheck():
    # Warnings and errors only: style suggestions vary between shellcheck releases.
    result = subprocess.run(
        ["shellcheck", "--severity=warning", str(INSTALLER)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout


FORBIDDEN = {
    "macOS home directory": r"/Users/\w",
    "Linux home directory": r"/home/\w",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "SSH public key": r"\bssh-(?:ed25519|rsa) AAAA",
    "Wi-Fi pre-shared key": r"\bpsk\s*=",
    "Wi-Fi password on an nmcli command line": r"\bnmcli\b[^\n]*\bpassword\b",
    "GitHub token": r"\b(?:ghp|gho|ghs|github_pat)_\w{20,}",
    "adsb.lol API key value": r"ZURICH_AIR_ADSB_LOL_KEY=\w",
}
DEPLOY_FILES = [GUIDE, UNIT, *sorted((ROOT / "scripts").glob("*.sh"))]
# Every Markdown page at the top (README, CONTRIBUTING, ROADMAP, …) and in docs/: what the
# documentation shows as an example gets copied too, and it is published with the code.
DOC_FILES = sorted({*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")})
# Issue and pull request templates: whatever they show as an example gets copied.
GITHUB_FILES = sorted(p for p in (ROOT / ".github").rglob("*") if p.is_file())
SCANNED_FILES = sorted({*DEPLOY_FILES, *DOC_FILES, *GITHUB_FILES})


def test_the_scan_covers_the_documentation():
    scanned = {p.relative_to(ROOT).as_posix() for p in SCANNED_FILES}
    assert {"README.md", "CONTRIBUTING.md", "docs/README.md", "docs/raspberry-pi.md"} <= scanned
    assert {"system/zurich-air.service", "scripts/install-pi.sh"} <= scanned


@pytest.mark.parametrize("path", SCANNED_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_machine_specific_paths_or_credentials(path):
    text = path.read_text()
    found = [name for name, pattern in FORBIDDEN.items() if re.search(pattern, text)]
    assert not found, f"{path.relative_to(ROOT)} contains: {', '.join(found)}"
