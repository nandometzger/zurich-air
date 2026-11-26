# Troubleshooting

Each entry below starts with what you see, then explains why it happens and how to fix
it. The entries are grouped roughly in the order you meet them: flashing and first boot,
network and SSH, installation, data and display, and the service.

Setup itself is described in [raspberry-pi.md](raspberry-pi.md). Commands marked *on your
computer* run in a terminal on your Mac, Linux or Windows computer; all others run on the
Pi, over SSH. `<user>` is the user name you chose in Raspberry Pi Imager.

> [!NOTE]
> The e-paper backend is not written yet; it lands once the panel has arrived and been
> inspected. Until then the Pi runs in **mock mode** and draws each frame to
> `~/zurich-air/previews/latest.png`. Everything said here about "the display" applies to
> that image.

## Start here

Four commands answer most questions. Run them on the Pi:

```sh
cd ~/zurich-air && .venv/bin/zurich-air doctor --config config.toml
systemctl status zurich-air --no-pager
journalctl -u zurich-air -e
journalctl -u zurich-air -p warning
```

- `zurich-air doctor` checks the configuration, time zone, clock, font rendering, display
  backend, each data source (one test request each; add `--offline` to skip them) and the
  service. It says which check failed and exits with a non-zero status if any did.
- `systemctl status` shows whether the service runs, and its last few log lines.
- `journalctl -u zurich-air -e` opens the log at its end (arrow keys to scroll, `q` to
  quit). `-p warning` shows only warnings and errors.

If `journalctl` shows no entries or complains about permissions, put `sudo` in front.

**Contents**

- [Flashing and first boot](#flashing-and-first-boot)
- [Network and SSH](#network-and-ssh)
- [Installation](#installation)
- [Data and display](#data-and-display)
- [Service](#service)
- [Asking for help](#asking-for-help)

## Flashing and first boot

### Imager does not list the microSD card

**Cause.** The card reader is not connected properly, the card is write-protected, or
Imager hides it as a system drive.

**Fix.** Take the card out and push it in again; try another USB port or card reader. If
the card sits in a full-size SD adapter, slide the small lock switch on the adapter's side
up, away from "LOCK". Leave **Exclude system drives** ticked: unticking it also shows your
computer's own disks, and writing to one of those destroys it.

### "Raspberry Pi OS Lite (64-bit)" is not in the list

**Cause.** It sits inside a category, or a different device was chosen in the first step.

**Fix.** Go **Back** to **Device** and choose **Raspberry Pi Zero 2 W**. In **OS**, open
**Raspberry Pi OS (other)**; the Lite (64-bit) entry is inside. Avoid the entries marked
**Legacy**.

### Writing or verifying fails

**Cause.** A faulty or counterfeit card, a loose card reader, or a download problem.

**Fix.** Try again with the card in another reader or port. If it fails twice, use another
card from a reputable brand. Do not skip verification to get past the error.

### The green LED stays dark

**Cause.** No power reaches the Pi.

**Fix.** Use the micro-USB port labelled **PWR IN**, nearest the corner of the board (the
middle port, labelled **USB**, is for data). Check the supply is switched on and try
another cable: some cables only charge phones slowly and are too thin.

### The LED blinks in a regular, repeating pattern, and the Pi never appears on the network

**Cause.** The Pi cannot boot from the card: the card is not seated, or the image on it is
damaged.

**Fix.** Unplug the power, take the card out and push it back in until it sits flat, then
power on again. If that does not help, flash the card again.

### First boot takes very long

**Cause.** On its first start the Pi enlarges the file system, applies the Imager settings,
and may restart once. On a slow card this takes longer.

**Fix.** Give it ten minutes before you assume something is wrong. Do not unplug it in the
meantime. Then continue with [`zurich-air.local` is not found](#zurich-airlocal-is-not-found).

## Network and SSH

### `zurich-air.local` is not found

You see `cannot resolve zurich-air.local`, `Unknown host` or `Could not resolve hostname`.

**Cause.** Either the Pi is not on the network (see the next entry), or your computer
cannot resolve `.local` names. Those are answered by the devices themselves through mDNS
(Bonjour), which some networks, VPNs and older Windows versions do not pass on.

**Fix.** Give the Pi five minutes after power-on, then find its IP address another way and
use it in place of `zurich-air.local` (for example `ssh <user>@192.168.1.23`):

- **Router:** look at the router's list of connected devices (often under "Home network",
  "LAN" or "DHCP"); the Pi appears as `zurich-air`.
- **macOS:** ask mDNS directly, *on your computer*: `dns-sd -G v4 zurich-air.local` (Ctrl-C
  to stop). No answer means the Pi is not on the network yet or mDNS does not get through.
- **macOS or Linux:** `arp -a` lists devices your computer has talked to recently.
  Raspberry Pi network interfaces have addresses starting with one of the prefixes below
  (macOS drops leading zeros, so `e4:5f:01` shows as `e4:5f:1`):

  ```sh
  arp -a | grep -iE 'b8:27:eb|dc:a6:32|e4:5f:1:|28:cd:c1|d8:3a:dd|2c:cf:67'
  ```

- **VPN:** turn off any VPN on your computer while you set up the Pi.

### The Pi does not join the Wi-Fi

Nothing answers at `zurich-air.local`, and the router does not list a device called
`zurich-air`, even after ten minutes.

**Causes**, roughly from most to least likely:

- A typing error in the network name (SSID) or password in Imager. Both are case-sensitive,
  and spaces count.
- The network is 5 GHz only. The Zero 2 W only supports 2.4 GHz.
- The Wi-Fi country is missing or wrong. Without one, the Pi keeps its Wi-Fi switched off.
  Imager sets it from the **Capital city** in the **Localisation** step.
- The network needs a user name as well as a password (WPA2-Enterprise or 802.1X, such as
  eduroam or office networks), or shows a login page (hotels, trains, many guest
  networks). Neither works.
- The Pi is connected, but your computer is on a guest network that isolates its devices,
  or on a different network.

**Fix.** Flashing the card again with corrected settings is usually quickest; Imager
remembers your other settings. Use your home Wi-Fi or a phone hotspot set to 2.4 GHz (see
[Choose the Wi-Fi to set up on](raspberry-pi.md#choose-the-wi-fi-to-set-up-on)).

If you have a screen and keyboard at hand, you can fix it on the Pi instead: connect a
monitor with a mini-HDMI cable or adapter, and a USB keyboard through a micro-USB OTG
adapter in the middle port labelled **USB**. Log in with your user name and password, then:

```sh
nmcli device status          # wlan0 should end up "connected"
nmcli device wifi list       # networks the Pi can see (2.4 GHz only)
sudo raspi-config nonint do_wifi_country CH
sudo nmtui                   # "Edit a connection" to correct the name or password
```

### SSH says "Permission denied (publickey)"

**Cause.** The Pi accepts only the SSH key you gave Imager, and your computer did not offer
it. Typical reasons:

- The user name before the `@` differs from the one in Imager (it is lowercase).
- You are on another computer, or your key has a different file name than
  `~/.ssh/id_ed25519`.
- In Imager, the key was not actually added (it must appear in the list after **Add**), or
  a different key was pasted.

**Fix.** Check the user name first. If your key has another name, point SSH at it:
`ssh -i ~/.ssh/<key file> <user>@zurich-air.local`. To see which keys SSH offers, run
`ssh -v <user>@zurich-air.local` and look for lines starting with `Offering public key`.

If none of that helps, flash the card again with the right public key (the `.pub` file;
see [SSH key](raspberry-pi.md#ssh-key)). With a screen and keyboard attached, you can
instead log in locally and add the key to `~/.ssh/authorized_keys`.

### SSH says "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"

**Cause.** The Pi has new host keys, which happens every time the card is flashed again.
Your computer still remembers the old ones and refuses to connect, as it should in case
someone is impersonating the Pi.

**Fix.** If you have just re-flashed the card, remove the old entry *on your computer*,
then connect again and answer `yes`:

```sh
ssh-keygen -R zurich-air.local
```

If you connected by IP address, also run `ssh-keygen -R <ip address>`. If you have *not*
re-flashed the card, find out why the identity changed before you answer `yes`.

### SSH asks for a passphrase or a password

**Cause.** `Enter passphrase for key` is your SSH key's own passphrase, set when you
created the key; it is not the Pi's password. A prompt like `<user>@zurich-air.local's
password:` means the Pi offered password login, which it should not.

**Fix.** For the passphrase, type it (on a Mac, `ssh-add --apple-use-keychain
~/.ssh/id_ed25519` remembers it). For a password prompt, log in with your key and switch
password login off as described in
[Check that SSH refuses passwords](raspberry-pi.md#check-that-ssh-refuses-passwords).

### Locale warnings when you log in

You see `setlocale: LC_CTYPE: cannot change locale` or similar.

**Cause.** Your computer sends its language settings over SSH, and the Pi does not have
that locale installed.

**Fix.** Harmless. To silence it on a Mac, untick "Set locale environment variables on
startup" in Terminal → Settings → Profiles → Advanced, or generate the locale on the Pi
with `sudo raspi-config` → Localisation Options → Locale.

## Installation

### `error: run this as the user the service should run as, not as root`

**Cause.** The install script was started with `sudo` or as root.

**Fix.** Run it as your normal user, without `sudo`: `~/zurich-air/scripts/install-pi.sh`.
It calls `sudo` itself where it needs to.

### `Could not get lock /var/lib/dpkg/lock-frontend`

**Cause.** Another package installation is running, often the system's automatic update
check shortly after boot.

**Fix.** Wait a few minutes and run the command again. Do not delete lock files.

### `warning: python3-... is not in your apt sources; skipped`

**Cause.** One of the hardware packages for the e-paper panel is not available on this
system.

**Fix.** Nothing to do in mock mode. Only the e-paper panel needs these packages; check
that you installed Raspberry Pi OS Lite (64-bit), based on Debian 13 "trixie".

### `uv: command not found`

**Cause.** uv lives in `~/.local/bin`, which your current shell does not know about yet.

**Fix.** Log out and in again, or call it as `~/.local/bin/uv`. If `uv sync --locked`
reports an outdated lock file, update uv with `~/.local/bin/uv self update`; if it still
fails, the lock file in the repository needs fixing, not the Pi.

### The install script cannot fast-forward

You see `cannot fast-forward ~/zurich-air to origin/main`.

**Cause.** The checkout on the Pi has local changes or commits (`git -C ~/zurich-air
status` shows them).

**Fix.** Keep settings in `config.toml` and secrets in `.env`, which git ignores, rather
than in tracked files. Undo or move away local edits to tracked files, then run the script
again.

### There is no `/dev/spidev0.0`

**Cause.** SPI, the interface the e-paper panel uses, is switched off.

**Fix.** Run `sudo raspi-config nonint do_spi 0` and reboot. `/boot/firmware/config.txt`
should then contain `dtparam=spi=on`. Mock mode does not need SPI.

## Data and display

[reading-the-display.md](reading-the-display.md) explains everything on the display. The
states that point to a problem are described here.

### The display shows "NO DATA SINCE hh:mm"

The aircraft are still drawn, but the dot next to the clock is hollow and the footer says,
for example, `NO DATA SINCE 14:05`.

**Cause.** No new data has arrived for more than 5 minutes (`update.stale_after_s`); the
time is that of the last good data. The picture stays up so the display does not go
blank during a short outage. Common reasons: the Wi-Fi or the internet connection is down,
both data sources are failing or [rate limiting](#the-log-shows-http-429-rate-limited), or
the clock is wrong.

**Fix.** Look at the warnings in the log:

```sh
journalctl -u zurich-air -p warning -e
```

A failed request shows up as `<source>: <reason>; next try in <n> s`, for example
`adsb.fi: timed out; next try in 30 s`, and each update without new data as a line like
`update: no new data, stale, ... (no data: adsb.fi: timed out; adsb.lol ready in 95 s)`.
After a failed request, Zurich Air leaves that source alone for 30 seconds, and for 1, 2
and then 5 minutes after further failures in a row; adsb.lol is asked at most every
3 minutes in any case.

Then check the network:

```sh
nmcli device status                 # wlan0 should be "connected"
curl -sI https://opendata.adsb.fi   # any "HTTP/..." line means internet and HTTPS work
timedatectl                         # "System clock synchronized: yes"
```

Zurich Air keeps trying on its own and returns to normal as soon as data arrives; you do
not need to restart it.

### The display shows "WAITING FOR DATA"

The radar is empty and the list says `WAITING FOR DATA`.

**Cause.** Zurich Air has no data recent enough to show. Either:

- it has just started and has not received any data yet (the footer then says just
  `NO DATA`), or
- no new data has arrived for more than 30 minutes (`update.hide_after_s`; the footer says
  `NO DATA SINCE hh:mm`), so the old aircraft were cleared rather than shown as if they
  were current.

**Fix.** As for [NO DATA SINCE](#the-display-shows-no-data-since-hhmm) above. Right after
a power cut it is normal for a minute or two, until the Wi-Fi is up and the clock is set.

### The display does not change at all, and the log mentions the system clock

The log says something like `system clock reads 2025-..., before 2026-01-01: not updating
until it is set (checking every 30 s)`.

**Cause.** The Pi has no battery-backed clock. After a power cut it starts with the time it
last saved, until it reaches a time server over the network. While the clock reads a date
before 2026, Zurich Air neither fetches nor draws, because HTTPS would fail and the
display would show a wrong time.

**Fix.** Get the Pi online; once the clock is set, the log says `system clock is set now`
and updates resume. If the network works but `timedatectl` keeps showing
`System clock synchronized: no`, the network may block time servers (NTP); try another
network, or restart the time service with `sudo systemctl restart systemd-timesyncd`.

### The display says "NO AIRCRAFT"

**Cause.** Usually not a fault: data arrives, but there is no aircraft within range that may
be listed (`NO AIRCRAFT within 60 km` by default). This can happen late at night. Aircraft
that opted out of public tracking are never listed; they stay anonymous markers on the
radar. The list is also always empty if `aircraft.featured` is set to `0`.

**Fix.** None needed. `zurich-air explain` lists every aircraft it sees and why it is or is
not featured:

```sh
cd ~/zurich-air && .venv/bin/zurich-air explain --config config.toml
```

### The log shows "HTTP 429 rate limited"

For example `adsb.fi: HTTP 429 rate limited; next try in 600 s`.

**Cause.** The data service received too many requests from your internet connection. Its
limits count requests per public IP address, so other devices or programs in the same
home using the same service count too. Zurich Air on its own stays within the limits: it
asks once a minute (every 10 minutes at night), and adsb.lol at most every 3 minutes.
Running `zurich-air once`, `explain` or `doctor` over and over adds to that. adsb.lol
without an API key allows 500 requests per day per IP address.

**What Zurich Air does.** It pauses that source for as long as the service asks (its
`Retry-After` header), or 10 minutes if it does not say, and uses the next source in
`provider.sources` meanwhile.

**Fix.** Usually just wait. Do not lower `update.interval_s` below 60 seconds. Use
`zurich-air doctor --offline` when you run it repeatedly. If you have an adsb.lol API key,
put it in `~/zurich-air/.env` (see
[Settings and the optional API key](raspberry-pi.md#settings-and-the-optional-api-key)).
[data-sources.md](data-sources.md) describes the services and their terms.

### The clock on the display is wrong

**Cause and fix**, depending on how wrong it is:

- **Off by whole hours:** the display shows the time in `location.timezone` from
  `config.toml` (default `Europe/Zurich`), not the Pi's system time zone. Check that
  setting, then `sudo systemctl restart zurich-air`. A misspelt time zone stops the
  program with `ZoneInfoNotFoundError` in the log; use a name such as `Europe/Zurich`.
- **Off by minutes, or by days:** the system clock is not synchronised. Check `timedatectl`
  as in [the clock entry above](#the-display-does-not-change-at-all-and-the-log-mentions-the-system-clock).
- **Up to 10 minutes behind at night:** expected during quiet hours; see the next entry.

Times in the log (`journalctl`) use the Pi's system time zone. If those are off by hours,
run `sudo timedatectl set-timezone Europe/Zurich`.

### At night the display only updates every 10 minutes

**Cause.** Quiet hours. From 23:30 to 06:00 local time (`update.quiet_hours`) Zurich Air
updates every 10 minutes (`update.quiet_interval_s`, 600 s) instead of every minute: there
is little traffic, and it saves requests and panel refreshes. The clock in the header shows
the time of the last update, so it advances in 10-minute steps too. The first update after
06:00 comes on time. The log marks the change with `quiet hours until 06:00: updating every
600 s` and `quiet hours over: updating every 60 s`.

**Fix.** None needed. To change it, edit `[update]` in `~/zurich-air/config.toml`: another
window such as `quiet_hours = "00:00-05:30"`, or `quiet_hours = ""` to update every minute
all night. Then `sudo systemctl restart zurich-air`. See [configuration.md](configuration.md).

### The preview image does not change

**Cause.** In mock mode the service writes `~/zurich-air/previews/latest.png` only when the
picture changes, which by day is about once a minute (the clock alone changes it). If the
file is much older, the service is not running or is not drawing.

**Fix.** Check the file's time with `ls -l ~/zurich-air/previews/latest.png`, then the
service (see [Service](#service)). `display.output_dir` in `config.toml` is relative to
`~/zurich-air`; if you changed it, look there. To copy the image to your computer, see
[Check the installation](raspberry-pi.md#check-the-installation).

## Service

### The service is not running

`systemctl status zurich-air` shows the service state in its `Active:` line:

| State | Meaning |
| --- | --- |
| `active (running)` | All well. |
| `activating (auto-restart)` | It stopped with an error; systemd starts it again shortly. |
| `failed` | It stopped with an error and systemd has given up for now. |
| `inactive (dead)` | It was stopped, or is not enabled at boot. |
| `Unit zurich-air.service could not be found.` | The service is not installed. |

**Fix.** Read the reason in the log with `journalctl -u zurich-air -e`. Common ones:

- **`zurich-air: error: ...` with `status=2/INVALIDARGUMENT`:** a problem in `config.toml`,
  such as `unknown config key(s): update.intervall_s` or `invalid TOML in config.toml: ...`.
  Correct the file; `zurich-air doctor` shows the same message.
- **`display.backend = "waveshare" is not implemented until the panel arrives; use "mock"
  for now`:** set `backend = "mock"` in the `[display]` section.
- **`ZoneInfoNotFoundError`:** `location.timezone` is misspelt.
- **`status=203/EXEC`:** `~/zurich-air/.venv` is missing or broken; run the install script
  again.
- **`status=216/GROUP`:** the `spi` or `gpio` group is missing, which happens only outside
  Raspberry Pi OS.

After fixing the cause, start afresh:

```sh
sudo systemctl reset-failed zurich-air
sudo systemctl restart zurich-air
```

If it is `inactive (dead)`, `sudo systemctl enable --now zurich-air` starts it and makes it
start at every boot. If the unit could not be found, run `~/zurich-air/scripts/install-pi.sh`.

### The log shows "Watchdog timeout"

**Cause.** The program stopped reporting to systemd for 5 minutes, which means it hung.
systemd stopped it and started it again, as designed.

**Fix.** A single occurrence needs no action. If it happens repeatedly, please open an
issue with the log lines around it.

### The log shows "the display failed 3 times in a row"

**Cause.** Drawing to the display failed three times running, so the program exits on
purpose and systemd restarts it. In mock mode this usually means the output folder cannot
be written, for example because the card is full.

**Fix.** Check free space with `df -h /` and look for the first error before this line in
the log (`journalctl -u zurich-air -p warning -e`). `zurich-air doctor` checks that the
output folder is writable.

### Power cuts and SD card safety

**What happens after a power cut.** The Pi starts by itself when power returns, and so
does the service. Until the Wi-Fi is up and the clock is set it shows
`WAITING FOR DATA`, then returns to normal.

**The SD card.** Unplugging a running Pi can, rarely, damage the file system on the card,
as on any computer. Zurich Air keeps writes small (one small image per change in mock
mode, plus the system log), but cannot rule it out. To reduce the risk:

- Use a good card (A1 class, reputable brand; "endurance" cards are made for constant
  use) and a proper 5 V, 2.5 A supply.
- When you can, shut down before unplugging: `sudo poweroff`, then wait until the green
  LED has stopped flickering before you pull the plug.
- Keep a copy of your `config.toml` and `.env` somewhere safe (not in git). If a card ever
  fails, flashing a new one and running the install script again is the way back.

**Check the power supply.** A weak supply or thin cable causes undervoltage, which leads to
crashes and card errors:

```sh
vcgencmd get_throttled
```

`throttled=0x0` means no problems since boot. Any other value (for example
`throttled=0x50000`) means the voltage dropped at some point: use a better supply or a
shorter, thicker cable.

## Asking for help

If none of this helps, open an issue on GitHub (see [CONTRIBUTING.md](../CONTRIBUTING.md))
and include:

- what you did and what you saw,
- the output of `zurich-air doctor`,
- the warnings from the last day: `journalctl -u zurich-air -p warning --since "1 day ago"
  --no-pager`,
- for display problems, a photo or the preview image.

Before you post, remove Wi-Fi names, IP addresses and anything that reveals where the
device lives. Never post passwords, the contents of `.env`, or a private SSH key.
