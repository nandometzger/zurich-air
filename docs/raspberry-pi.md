# Raspberry Pi setup

This guide takes a Raspberry Pi Zero 2 W from a blank microSD card to Zurich Air running
as a service, headless (no monitor or keyboard), and then prepares it for the Wi-Fi of the
home it will live in.

> **What has been tested.** This guide was written before the hardware arrived. Sections
> marked **Untested** follow the Raspberry Pi, Debian and NetworkManager documentation but
> have not yet been run on a Zurich Air device. The install script and the service file
> are checked off-device only: static tests, `bash -n`, shellcheck where it is installed,
> and a dry run with `apt`, `sudo` and `systemctl` stubbed out. The e-paper backend is not
> implemented yet; until it is, the service renders to PNG files (the mock display).
> If a step differs on real hardware, please open an issue, without Wi-Fi names or
> passwords.

Below, `<user>` is the user name you choose in Raspberry Pi Imager. Commands in blocks
marked *on the Mac* run in Terminal on your Mac; all others run on the Pi over SSH.

## What you need

- Raspberry Pi Zero 2 W**H** (the H means the GPIO header is already soldered on; the
  e-paper HAT plugs into it).
- A microSD card of 8 GB or more from a reputable brand. It runs around the clock, so an
  "endurance" card is a good choice.
- A 5 V micro-USB power supply that can deliver 2.5 A, plugged into the port labelled
  **PWR IN** (the other micro-USB port is for data).
- A 2.4 GHz Wi-Fi network: the Zero 2 W does not support 5 GHz. Networks that allow
  WPA2 are the safe choice; WPA3-only networks may not work.
- A Mac with an SD card reader and an SSH key. If `ls ~/.ssh/id_ed25519.pub` finds
  nothing, create one *on the Mac*:

  ```sh
  ssh-keygen -t ed25519 -C zurich-air
  ```

## 1. Flash the microSD card

> **Untested.** The labels follow Raspberry Pi Imager 2.x and may differ slightly in your
> version; Imager 1.x offers the same settings under "Edit settings" (OS customisation).

*On the Mac*:

```sh
brew install --cask raspberry-pi-imager
```

Insert the card, open **Raspberry Pi Imager** and choose:

1. **Device:** Raspberry Pi Zero 2 W.
2. **Operating system:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**, the
   release based on Debian 13 "trixie". It must be *Lite* (no desktop: the Zero has
   512 MB of RAM) and *64-bit* (the Python packages Zurich Air installs ship ready-made
   for 64-bit ARM; on 32-bit they would have to be compiled on the Pi).
3. **Storage:** your microSD card. Double-check: everything on it is erased.
4. **Customisation** (apply it; do not skip this step, it is what makes the setup
   headless):
   - **Hostname:** `zurich-air`
   - **Localisation:** time zone **Europe/Zurich**, the keyboard layout you would use on
     a local console, and Wi-Fi country **CH** (or wherever the Pi will be used). The
     Pi's Wi-Fi stays off until a country is set.
   - **User:** a user name of your choice (this is `<user>` below) and a strong password.
     SSH will not accept the password, but `sudo` and a local console may ask for it.
   - **Wi-Fi:** the network you are setting up on (typically your own), with its
     password. You add the network of the Pi's future home later, in step 5.
   - **Remote access:** enable SSH with **public-key authentication only**, and paste
     the contents of `~/.ssh/id_ed25519.pub` (*on the Mac*: `pbcopy < ~/.ssh/id_ed25519.pub`).
   - **Raspberry Pi Connect:** leave it off for now (see
     [Remote help after the move](#remote-help-after-the-move-optional)).
5. **Write.** macOS asks for your password to write to the card. If Imager offers to
   fill in the Wi-Fi password from the keychain, either answer works. Imager ejects the
   card when it has finished verifying.

## 2. First boot and SSH

> **Untested** on this device.

Put the card into the Pi and connect power. The first boot expands the file system and
applies your settings; it can take a few minutes and may reboot once. Then, *on the
Mac*:

```sh
ssh <user>@zurich-air.local
```

Confirm the host key prompt with `yes`. Optionally, add an entry to `~/.ssh/config` *on
the Mac* so that `ssh zurich-air` is enough from now on:

```text
Host zurich-air
    HostName zurich-air.local
    User <user>
```

### If `zurich-air.local` does not resolve

`.local` names are resolved by mDNS (Bonjour), which some networks block. Give the Pi
five minutes after power-on first, then find its IP address another way (*on the Mac*):

- Ask mDNS directly: `dns-sd -G v4 zurich-air.local` (Ctrl-C to stop). No answer means
  the Pi is not on the network yet or mDNS does not get through.
- Look at your router's list of connected devices (often under "Home network", "LAN" or
  "DHCP"); the Pi appears as `zurich-air`.
- `arp -a` lists devices your Mac has talked to recently; Raspberry Pi network
  interfaces have addresses starting with one of the prefixes below (macOS drops leading
  zeros, so `e4:5f:01` shows as `e4:5f:1`):

  ```sh
  arp -a | grep -iE 'b8:27:eb|dc:a6:32|e4:5f:1:|28:cd:c1|d8:3a:dd|2c:cf:67'
  ```

Then connect with `ssh <user>@<ip address>`. If nothing shows up at all, the Pi has not
joined the Wi-Fi: check that the network is 2.4 GHz and that SSID, password and Wi-Fi
country were right in Imager, and that your Mac is not on a guest network that isolates
its clients. Flashing the card again is quicker than debugging without a screen.

### Check that SSH refuses passwords

```sh
sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication) '
```

Both lines should end in `no`. If `passwordauthentication yes` appears, turn it off
(keep this session open and try a new login from a second terminal before you close it):

```sh
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/10-no-passwords.conf
sudo systemctl reload ssh
```

Also check the clock and the time zone: `timedatectl` should show `Time zone:
Europe/Zurich` and `System clock synchronized: yes`. If the time zone is wrong:
`sudo timedatectl set-timezone Europe/Zurich`. The system locale (`locale`) does not
matter to Zurich Air, which formats times itself; change it with `sudo raspi-config` →
Localisation Options if you like.

## 3. Update the system and enable SPI

> **Untested** on this device.

```sh
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_spi 0
sudo reboot
```

The upgrade can take a while on a Zero. `do_spi 0` means *enable* (raspi-config's
non-interactive mode uses 0 for yes); the e-paper panel talks SPI, and the setting takes
effect after the reboot. Reconnect and check:

```sh
ls -l /dev/spidev*
```

`/dev/spidev0.0` and `/dev/spidev0.1`, owned by group `spi`, should be listed.

## 4. Install Zurich Air

> **Untested on a Pi.** The script has only been dry-run with `apt`, `sudo` and `systemctl`
> stubbed out.

```sh
sudo apt install -y git
git clone https://github.com/nandometzger/zurich-air.git ~/zurich-air
~/zurich-air/scripts/install-pi.sh
```

The script asks for nothing except, possibly, your `sudo` password. It:

1. installs the apt packages Zurich Air needs (`git`, `curl`, `python3-venv`) and those
   the e-paper driver will need (`python3-spidev`, `python3-lgpio`, `python3-gpiozero`);
   a hardware package your apt sources lack is skipped with a warning;
2. installs [uv](https://docs.astral.sh/uv/) into `~/.local/bin` if it is missing;
3. clones the repository, or fast-forwards an existing checkout to `origin/main`;
4. creates `~/zurich-air/.venv` on the system Python with access to the system
   site packages, so the apt-installed hardware bindings are importable, and installs the
   locked dependencies without the development tools;
5. creates `~/zurich-air/config.toml` from `config.example.toml` unless it exists;
6. installs the systemd service `zurich-air` for your user and this directory, enables it
   at boot and (re)starts it.

Run `~/zurich-air/scripts/install-pi.sh --help` for its options (another directory, a
different branch, or `--no-pull` to try local changes). Running it again is safe.

Check that the service runs and fetches data:

```sh
systemctl status zurich-air --no-pager
journalctl -u zurich-air -f
```

The log should show `running: update every 60 s (600 s in quiet hours 23:30-06:00)` and
then, once a minute, a line such as
`update: adsb.fi, live, 23 aircraft, featured SWR8 EZY12 DLH4, display refreshed`
(Ctrl-C stops following the log; the service keeps running). Until the e-paper backend
exists, the service draws to `~/zurich-air/previews/latest.png`, replaced on every
change, so it never fills the card. To look at it, *on the Mac*:

```sh
scp <user>@zurich-air.local:zurich-air/previews/latest.png . && open latest.png
```

### Settings and the optional API key

Edit `~/zurich-air/config.toml` (`nano ~/zurich-air/config.toml`), then apply it with
`sudo systemctl restart zurich-air`. Every setting is explained in the file.

An adsb.lol API key, if you have one, does not belong in the config. Put it in
`~/zurich-air/.env`, which git ignores and the service reads at start:

```sh
nano ~/zurich-air/.env       # one line: ZURICH_AIR_ADSB_LOL_KEY=<your key>
chmod 600 ~/zurich-air/.env
sudo systemctl restart zurich-air
```

## 5. Before gifting: add the Wi-Fi of the new home

> **Untested** on this device.

The Pi will live on a different Wi-Fi network from the one you set it up on. Add that
network now, while you can still reach the Pi. You need its name (SSID) and password,
and it must offer 2.4 GHz.

```sh
sudo nmtui
```

In the text interface: **Edit a connection** → **Add** → **Wi-Fi** → **Create**. Enter a
profile name, the **SSID**, **Security** "WPA & WPA2 Personal" (or "WPA3 Personal" if
that is all the network offers) and the **Password**. Leave "Automatically connect" and
"Available to all users" ticked, choose **OK**, then **Back** and **Quit**. The Pi keeps
its current connection; the new profile is used when that network is in range.

Use `nmtui` rather than putting the password on an `nmcli` command line: typed into the
form, it never lands in your shell history. NetworkManager stores it in
`/etc/NetworkManager/system-connections/`, readable only by root.

> **Never commit Wi-Fi credentials or paste them into issues**, and do not paste the
> output of `nmcli --show-secrets` anywhere. Network names reveal where the device
> lives: redact them in logs you share too.

**How NetworkManager chooses.** When several saved networks are in range, it activates
the one with the highest `connection.autoconnect-priority` (default 0); among equal
priorities, the one used most recently. It does not drop a working connection because a
higher-priority network appears; the priority matters at boot and when the current
connection is lost. In the new home only that network is in range, so the default works;
giving it a higher priority makes the choice explicit:

```sh
nmcli -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE connection show
sudo nmcli connection modify "<profile name>" connection.autoconnect-priority 10
```

The list should show both Wi-Fi profiles with `AUTOCONNECT` `yes`; the active one has
`wlan0` under `DEVICE`. If the new home is in another country, also set its Wi-Fi
country code, for example `sudo raspi-config nonint do_wifi_country DE`.

**If you do not know the password yet:** also add your phone's hotspot as a profile (on an
iPhone, turn on "Maximise Compatibility" so it uses 2.4 GHz). On site, turn the hotspot on,
join it with your laptop, `ssh <user>@zurich-air.local` and add the home network with
`sudo nmtui` there.

### Hand-over checklist

- [ ] `sudo apt update && sudo apt full-upgrade -y` once more.
- [ ] The new home's Wi-Fi is listed by `nmcli connection show`.
- [ ] After `sudo reboot`, `systemctl status zurich-air` shows `active (running)` without
      you starting anything.
- [ ] You can explain the one rule to the recipient: if it ever looks stuck, unplug it
      and plug it back in.

Optionally, let Debian install security updates by itself:

```sh
sudo apt install -y unattended-upgrades
apt-config dump APT::Periodic::Unattended-Upgrade   # expect: ... "1";
```

If it shows `"0"` or nothing, turn it on with
`sudo dpkg-reconfigure -plow unattended-upgrades`.

### Remote help after the move (optional)

From your home, `ssh` will not reach the Pi behind someone else's router.
[Raspberry Pi Connect](https://www.raspberrypi.com/documentation/services/connect.html)
offers a remote shell through your Raspberry Pi ID without router changes: on the Lite
OS, install `rpi-connect-lite` and follow that page. It is a hosted service, so weigh the
convenience against giving a third party a path into the device. Not tried with Zurich
Air yet.

## 6. Updating later

Run the install script again. It fast-forwards the checkout to `origin/main`, installs
new apt packages and locked dependencies if there are any, refreshes the service file
and restarts the service:

```sh
~/zurich-air/scripts/install-pi.sh
```

or, *on the Mac*, in one go (`-t` lets `sudo` ask for a password if it needs one):

```sh
ssh -t zurich-air '~/zurich-air/scripts/install-pi.sh'
```

The same by hand, when only the code changed:

```sh
cd ~/zurich-air
git pull --ff-only
~/.local/bin/uv sync --locked --no-dev --python /usr/bin/python3
sudo systemctl restart zurich-air
```

### Logs and the service

```sh
journalctl -u zurich-air -f                     # follow the log (Ctrl-C to stop)
journalctl -u zurich-air -b --no-pager          # everything since the last boot
journalctl -u zurich-air --since "1 hour ago"
systemctl status zurich-air                     # running? last few log lines
sudo systemctl stop zurich-air
sudo systemctl start zurich-air
sudo systemctl restart zurich-air               # e.g. after editing config.toml
sudo systemctl disable --now zurich-air         # stop, and do not start at boot
sudo systemctl enable --now zurich-air          # start, and start at every boot
```

If the program crashes, systemd restarts it after 30 seconds, then waits longer after
each further crash, up to 10 minutes between attempts. After a fix, start afresh with
`sudo systemctl reset-failed zurich-air && sudo systemctl restart zurich-air`.

For more detail in the log, run it by hand for a while (Ctrl-C stops it):

```sh
sudo systemctl stop zurich-air
~/zurich-air/.venv/bin/zurich-air run --log-level debug --config ~/zurich-air/config.toml
sudo systemctl start zurich-air
```

### Uninstall

```sh
sudo systemctl disable --now zurich-air
sudo rm /etc/systemd/system/zurich-air.service
sudo systemctl daemon-reload
rm -rf ~/zurich-air
```

## Running unattended

> **Untested on a Pi.** The behaviour below is covered by the test suite; the systemd
> parts (the watchdog) have not yet been seen on a Zurich Air device.

**What the log says.** One line per update, as above, and once an hour a summary:

```text
last hour: 60 updates, 58 with new data, 14 display refreshes, 0 errors; failed requests: adsb.fi 2, adsb.lol 0
```

Problems are logged as warnings or errors with their journal priority, so
`journalctl -u zurich-air -p warning` lists only those. An unexpected error is logged
with its traceback at most once an hour, then as a single line. Zurich Air logs no
network names, addresses or location; callsigns in the log are public flight
identifiers, and aircraft that opted out of tracking never appear by name.

**Outages.** When no new data arrives, the last picture stays up, marked with a hollow
dot and `NO DATA SINCE 14:05` in the footer. After 30 minutes (`update.hide_after_s`)
the radar is cleared and the list says `WAITING FOR DATA`, so aircraft long gone never
pass for current traffic. The app keeps trying and returns to normal by itself.

**Quiet hours.** From 23:30 to 06:00 (`update.quiet_hours`, local time) it updates every
10 minutes instead of every minute (`update.quiet_interval_s`): fewer requests to the
data services and fewer panel refreshes while there is little traffic. Set
`quiet_hours = ""` to update all night.

**Watchdog.** The service is of `Type=notify`: the app tells systemd when it has
started, then that it is alive, after every update and at least once a minute while it
waits for the next (also during quiet hours). If it stays silent for 5 minutes
(`WatchdogSec=` in the unit), systemd kills and restarts it; the log then shows
`Watchdog timeout`. Errors inside an update are logged and the next update carries on;
only a display that fails three times in a row ends the program, so that systemd starts
it again.

**Clock.** The Pi has no battery-backed clock: after a power cut it starts with the time
it last saved, until NTP corrects it over the network (`timedatectl` then shows `System
clock synchronized: yes`). The service deliberately does not wait for that first sync:
with the network down at boot it would never start, and the panel would keep its last
picture, live dot included. It starts at once and shows `WAITING FOR DATA` instead;
only while the clock reads a date before 2026 does it neither fetch nor draw, looking
again every 30 seconds.

## Troubleshooting

- **`zurich-air.local` not found:** see [step 2](#if-zurich-airlocal-does-not-resolve).
- **`WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED`** after re-flashing the card: the
  Pi has new host keys. *On the Mac*: `ssh-keygen -R zurich-air.local`.
- **Locale warnings when you log in** (`setlocale: LC_CTYPE: cannot change locale`):
  macOS sends its locale over SSH and the Pi lacks it. Harmless; to silence it, untick
  "Set locale environment variables on startup" in Terminal → Settings → Profiles →
  Advanced, or generate that locale on the Pi with `sudo raspi-config` → Localisation
  Options → Locale.
- **The service keeps restarting:** `journalctl -u zurich-air -n 50 --no-pager` shows
  why. `status=216/GROUP` means the `spi` or `gpio` group is missing, which happens only
  outside Raspberry Pi OS. `status=203/EXEC` means `~/zurich-air/.venv` is missing or
  broken: run the install script again.
- **"no data" in the log or on the display:** check the network with
  `nmcli device status` and `curl -sI https://opendata.adsb.fi`. The Zero has no
  battery-backed clock: until `timedatectl` reports `System clock synchronized: yes`,
  HTTPS requests can fail. Zurich Air keeps retrying, so this sorts itself out once the
  Wi-Fi is up.
- **`uv: command not found`:** uv lives in `~/.local/bin`; open a new login shell or call
  `~/.local/bin/uv`. If `uv sync --locked` reports an outdated lock file, update uv with
  `~/.local/bin/uv self update`; if it still fails, the lock file in the repository needs
  fixing, not the Pi.
- **The install script cannot fast-forward:** the checkout on the Pi has local changes or
  commits (`git -C ~/zurich-air status`). Keep settings in `config.toml`, which git
  ignores, rather than in tracked files.
- **No `/dev/spidev0.0`:** run `sudo raspi-config nonint do_spi 0` and reboot;
  `/boot/firmware/config.txt` should then contain `dtparam=spi=on`.
- **The Pi does not come online in its new home:** check that the network offers
  2.4 GHz, that the password was typed correctly in `nmtui` and the Wi-Fi country
  matches. Without a working network, the fix is a monitor (mini-HDMI) and USB keyboard
  (micro-USB OTG adapter), the hotspot trick from step 5, or re-flashing the card.
- **A corrupted SD card after power cuts:** rare, but possible on any Pi. Use a good
  card, and `sudo poweroff` before unplugging when you can.
