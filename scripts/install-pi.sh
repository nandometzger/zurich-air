#!/usr/bin/env bash
# Install or update Zurich Air on a Raspberry Pi running Raspberry Pi OS Lite (64-bit,
# Debian 13 "trixie"). See docs/raspberry-pi.md.
#
# Run it on the Pi as the user the service should run as (not as root); it calls sudo
# where it has to. Every step checks what is already in place, so running it again is
# safe, and it is also how you update: it pulls the branch, re-syncs the dependencies,
# refreshes the systemd unit and restarts the service.
#
# It never reads or prints secrets: Wi-Fi is NetworkManager's business, and the optional
# API key lives in the git-ignored .env file, which this script does not touch.

set -euo pipefail

readonly SERVICE=zurich-air
readonly UNIT_PATH=/etc/systemd/system/$SERVICE.service
# The venv is built on the system Python so that the apt-installed hardware bindings
# below (compiled for exactly this interpreter) are importable from it.
readonly SYSTEM_PYTHON=/usr/bin/python3
readonly UV_INSTALLER=https://astral.sh/uv/install.sh

# Needed now.
#   python3-venv is not strictly required (uv builds the venv itself) but gives the
#   system Python a working `-m venv`/ensurepip, which some tools expect.
REQUIRED_PACKAGES=(git curl ca-certificates python3-venv)
# Needed once the e-paper panel is driven (Waveshare's Python driver uses spidev and
# gpiozero; gpiozero 2 uses lgpio as its default pin library). The names below are those
# in Debian trixie and in the Raspberry Pi OS archive. They are installed if available
# and skipped with a warning if not, so a renamed package cannot break the install.
# If a driver needs the RPi.GPIO API, python3-rpi-lgpio provides it on top of lgpio.
HARDWARE_PACKAGES=(python3-spidev python3-lgpio python3-gpiozero)

APP_DIR=${ZURICH_AIR_DIR:-$HOME/zurich-air}
REPO_URL=${ZURICH_AIR_REPO:-https://github.com/nandometzger/zurich-air.git}
BRANCH=${ZURICH_AIR_BRANCH:-main}
PULL=1
USER_NAME=
UV=
SELF_CODE=

usage() {
  cat <<'EOF'
Usage: install-pi.sh [options]

Install or update Zurich Air and its systemd service. Safe to run again.

Options:
  --dir DIR      checkout to install or update
                 (default: $ZURICH_AIR_DIR, else ~/zurich-air)
  --repo URL     repository to clone if DIR does not exist yet
                 (default: $ZURICH_AIR_REPO, else the public GitHub repository)
  --branch NAME  branch to check out and fast-forward
                 (default: $ZURICH_AIR_BRANCH, else main)
  --no-pull      use the checkout as it is, e.g. to try local changes
  -h, --help     show this help
EOF
}

log() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

parse_args() {
  while (($#)); do
    case $1 in
      --dir | --repo | --branch)
        (($# >= 2)) || die "$1 needs a value"
        case $1 in
          --dir) APP_DIR=$2 ;;
          --repo) REPO_URL=$2 ;;
          --branch) BRANCH=$2 ;;
        esac
        shift 2
        ;;
      --no-pull)
        PULL=0
        shift
        ;;
      -h | --help)
        usage
        exit 0
        ;;
      *) die "unknown option: $1 (see --help)" ;;
    esac
  done
}

preflight() {
  ((EUID != 0)) || die "run this as the user the service should run as, not as root"
  command -v apt-get >/dev/null || die "apt-get not found: this script is for Raspberry Pi OS"
  command -v sudo >/dev/null || die "sudo not found"

  local codename arch
  codename=$(sed -n 's/^VERSION_CODENAME=//p' /etc/os-release 2>/dev/null || true)
  [[ $codename == trixie ]] ||
    warn "written for Raspberry Pi OS based on Debian 13 (trixie), found: ${codename:-unknown}"
  arch=$(uname -m)
  [[ $arch == aarch64 ]] ||
    warn "expected the 64-bit OS (aarch64), found $arch: dependencies may need compiling"

  # The path ends up in a systemd unit and a sed expression: keep it plain.
  [[ $APP_DIR == /* ]] || APP_DIR=$PWD/$APP_DIR
  APP_DIR=${APP_DIR%/}
  [[ $APP_DIR =~ ^/[A-Za-z0-9._/+-]+$ ]] ||
    die "the install directory must be a plain absolute path (no spaces): $APP_DIR"
  USER_NAME=$(id -un)
  [[ $USER_NAME =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || die "unsupported user name: $USER_NAME"

  sudo -v || die "this script needs sudo"
}

is_installed() {
  [[ $(dpkg -s "$1" 2>/dev/null) == *"Status: install ok installed"* ]]
}

install_packages() {
  # Arrays are only expanded when non-empty (bash before 4.4 treats empty ones as unset).
  local pkg install=() hardware=()
  for pkg in "${REQUIRED_PACKAGES[@]}"; do
    is_installed "$pkg" || install+=("$pkg")
  done
  for pkg in "${HARDWARE_PACKAGES[@]}"; do
    is_installed "$pkg" || hardware+=("$pkg")
  done
  if ((${#install[@]} + ${#hardware[@]} == 0)); then
    log "apt packages already installed"
    return
  fi

  log "Updating the apt package lists"
  sudo apt-get update -q
  if ((${#hardware[@]})); then
    for pkg in "${hardware[@]}"; do
      if apt-cache show "$pkg" >/dev/null 2>&1; then
        install+=("$pkg")
      else
        warn "$pkg is not in your apt sources; skipped (only the e-paper panel needs it)"
      fi
    done
  fi
  if ((${#install[@]})); then
    log "Installing ${install[*]}"
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${install[@]}"
  fi
}

find_uv() {
  if command -v uv >/dev/null 2>&1; then
    command -v uv
  elif [[ -x $HOME/.local/bin/uv ]]; then
    printf '%s\n' "$HOME/.local/bin/uv"
  else
    return 1
  fi
}

install_uv() {
  if UV=$(find_uv); then
    log "Using $("$UV" --version) at $UV"
    return
  fi
  log "Installing uv into $HOME/.local/bin with the official installer"
  curl -LsSf "$UV_INSTALLER" | env UV_INSTALL_DIR="$HOME/.local/bin" sh
  UV=$(find_uv) || die "uv was installed but is not in $HOME/.local/bin"
}

update_checkout() {
  if [[ -d $APP_DIR/.git ]]; then
    if ((!PULL)); then
      log "Using $APP_DIR as it is (--no-pull)"
      return
    fi
    log "Updating $APP_DIR to the latest $BRANCH"
    git -C "$APP_DIR" fetch --quiet origin "$BRANCH"
    if [[ $(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD) != "$BRANCH" ]]; then
      git -C "$APP_DIR" switch --quiet "$BRANCH"
    fi
    git -C "$APP_DIR" merge --ff-only --quiet "origin/$BRANCH" ||
      die "cannot fast-forward $APP_DIR to origin/$BRANCH; check: git -C $APP_DIR status"
  elif [[ -e $APP_DIR && -n $(find "$APP_DIR" -mindepth 1 -maxdepth 1 -print -quit) ]]; then
    die "$APP_DIR exists but is not a git checkout; move it away or choose --dir"
  else
    log "Cloning Zurich Air ($BRANCH) into $APP_DIR"
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi

  # If the update brought a different installer (or this one was run from elsewhere),
  # finish with the checkout's version so new steps are not skipped until the next run.
  local installer=$APP_DIR/scripts/install-pi.sh
  if [[ -f $installer && $(<"$installer") != "$SELF_CODE" ]]; then
    log "Continuing with the installer from the checkout"
    exec bash "$installer" --dir "$APP_DIR" --branch "$BRANCH" --no-pull
  fi
}

venv_matches_system_python() {
  local venv=$APP_DIR/.venv
  local probe='import sys; print(sys.base_prefix, *sys.version_info[:2])'
  [[ -x $venv/bin/python && -f $venv/pyvenv.cfg ]] &&
    grep -qx 'include-system-site-packages = true' "$venv/pyvenv.cfg" &&
    [[ $("$venv/bin/python" -c "$probe" 2>/dev/null) == "$("$SYSTEM_PYTHON" -c "$probe")" ]]
}

setup_venv() {
  export UV_PYTHON_DOWNLOADS=never # only ever use the system Python
  [[ -x $SYSTEM_PYTHON ]] || die "$SYSTEM_PYTHON not found"
  if venv_matches_system_python; then
    log "Virtual environment is up to date with $SYSTEM_PYTHON"
  else
    # New, or built on another interpreter (e.g. after an OS upgrade): rebuild it.
    log "Creating $APP_DIR/.venv on $SYSTEM_PYTHON with system site packages"
    "$UV" venv --quiet --clear --system-site-packages --python "$SYSTEM_PYTHON" "$APP_DIR/.venv"
  fi
  log "Installing the locked dependencies (without dev tools)"
  # --python keeps uv on this venv even if .python-version asks for another version.
  "$UV" sync --locked --no-dev --python "$SYSTEM_PYTHON" --project "$APP_DIR"
  log "Installed $("$APP_DIR/.venv/bin/zurich-air" --version)"

  local missing
  missing=$("$APP_DIR/.venv/bin/python" -c 'import importlib.util as u
print(" ".join(m for m in ("spidev", "lgpio", "gpiozero") if u.find_spec(m) is None))')
  if [[ -n $missing ]]; then
    warn "not importable from the venv: $missing (only the e-paper panel needs them)"
  else
    log "Hardware bindings spidev, lgpio and gpiozero are importable"
  fi
}

create_config() {
  if [[ -e $APP_DIR/config.toml ]]; then
    log "Keeping the existing $APP_DIR/config.toml"
  else
    cp "$APP_DIR/config.example.toml" "$APP_DIR/config.toml"
    log "Created $APP_DIR/config.toml from config.example.toml"
  fi
}

# The service is ordered after time-sync.target, which systemd-timesyncd alone reaches
# before the clock is synchronised. systemd-time-wait-sync (in the systemd-timesyncd
# package, disabled by default) holds the target until the first NTP sync. Enabling it
# takes effect at the next boot; the clock is already right while this script runs.
enable_time_wait_sync() {
  if ! systemctl is-active --quiet systemd-timesyncd; then
    warn "systemd-timesyncd is not running; the service may start before the clock is set"
  elif systemctl is-enabled --quiet systemd-time-wait-sync 2>/dev/null; then
    log "systemd-time-wait-sync is enabled"
  else
    log "Enabling systemd-time-wait-sync: at boot the service waits for the first time sync"
    sudo systemctl enable --quiet systemd-time-wait-sync
  fi
}

install_service() {
  if [[ ! -d /run/systemd/system ]]; then
    warn "systemd is not running here; skipped installing the service"
    return
  fi
  local group
  for group in spi gpio; do
    getent group "$group" >/dev/null ||
      die "group $group does not exist, but the service needs it for SPI/GPIO (is this Raspberry Pi OS?)"
  done

  local rendered
  rendered=$(sed -e "s|@USER@|$USER_NAME|g" -e "s|@APP_DIR@|$APP_DIR|g" \
    "$APP_DIR/system/$SERVICE.service")
  if grep -Eq '@[A-Z_]+@' <<<"$rendered"; then
    die "the unit template has a placeholder this installer does not know"
  fi
  if [[ -f $UNIT_PATH && $(<"$UNIT_PATH") == "$rendered" ]]; then
    log "$UNIT_PATH is up to date"
  else
    log "Installing $UNIT_PATH (User=$USER_NAME, WorkingDirectory=$APP_DIR)"
    printf '%s\n' "$rendered" | sudo tee "$UNIT_PATH" >/dev/null
    sudo systemctl daemon-reload
  fi

  enable_time_wait_sync
  sudo systemctl enable --quiet "$SERVICE"
  sudo systemctl reset-failed "$SERVICE" 2>/dev/null || true
  log "Restarting $SERVICE"
  sudo systemctl restart "$SERVICE"
  sleep 5
  if systemctl is-active --quiet "$SERVICE"; then
    log "$SERVICE is running"
  else
    warn "$SERVICE is not running; see: journalctl -u $SERVICE -n 50 --no-pager"
  fi
}

next_steps() {
  cat <<EOF

Zurich Air is installed in $APP_DIR and runs as the $SERVICE service.

  Follow the log:   journalctl -u $SERVICE -f
  Service status:   systemctl status $SERVICE
  Settings:         nano $APP_DIR/config.toml
                    then: sudo systemctl restart $SERVICE
  Mock display:     $APP_DIR/previews/latest.png (newest frame)
  Update later:     $APP_DIR/scripts/install-pi.sh
EOF
  if [[ ! -e /dev/spidev0.0 ]]; then
    printf '\n  SPI is off (no /dev/spidev0.0). The e-paper panel will need it:\n'
    printf '    sudo raspi-config nonint do_spi 0 && sudo reboot\n'
  fi
}

main() {
  # The code that is running now, to notice when the checkout's installer differs.
  if [[ -f ${BASH_SOURCE[0]:-} ]]; then
    SELF_CODE=$(<"${BASH_SOURCE[0]}")
  fi
  parse_args "$@"
  preflight
  install_packages
  install_uv
  update_checkout
  setup_venv
  create_config
  install_service
  next_steps
}

# All the work happens inside functions and bash reads this whole line before running
# it, so a `git pull` that rewrites this file while it runs cannot confuse bash.
main "$@"; exit
