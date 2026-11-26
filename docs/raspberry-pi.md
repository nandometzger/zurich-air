# Raspberry Pi setup

This guide takes a Raspberry Pi Zero 2 W from a blank microSD card to Zurich Air running
as a service, headless (no monitor or keyboard). It then prepares the Pi for the Wi-Fi of
the home it will live in.

> [!NOTE]
> **What has been tested.** This guide was written before the hardware arrived. Sections
> marked **Untested** follow the Raspberry Pi, Debian and NetworkManager documentation
> and the source code of Raspberry Pi Imager 2.0, but have not yet been run on a Zurich
> Air device. The install script and the service file are checked off-device only:
> static tests, `bash -n`, ShellCheck where it is installed, and a dry run with `apt`,
> `sudo` and `systemctl` stubbed out.
>
> The e-paper backend is not written yet: it lands once the panel has arrived and been
> inspected. Until then the service runs in **mock mode** and draws each frame to
> `~/zurich-air/previews/latest.png` instead of a screen. Everything in this guide works
> the same in mock mode.
>
> If a step differs on real hardware, please open an issue, without Wi-Fi names or
> passwords.

In this guide, `<user>` stands for the user name you choose in Raspberry Pi Imager.
Commands marked *on your computer* run in a terminal on your Mac, Linux or Windows
computer; all others run on the Pi, over SSH.

If something goes wrong, see [troubleshooting.md](troubleshooting.md). The parts are
listed in [hardware.md](hardware.md).

## At a glance

1. **Prepare your computer:** install Raspberry Pi Imager and make sure you have an SSH
   key.
2. **Flash the card:** Raspberry Pi OS Lite (64-bit), with the hostname, user, Wi-Fi and
   SSH key filled in by Imager.
3. **First boot:** card into the Pi, power on, wait about five minutes, then
   `ssh <user>@zurich-air.local`.
4. **Update the system and enable SPI** (the e-paper panel's interface).
5. **Install Zurich Air** with one script, then check it with `zurich-air doctor`.
6. **Before gifting:** add the Wi-Fi of the Pi's future home.

Plan on about an hour, most of it waiting for downloads and updates.

### Checklist

Print this, or copy it into a note, and tick things off as you go.

- [ ] Raspberry Pi Zero 2 W with pre-soldered header ("WH" or "with headers").
- [ ] microSD card, 16–32 GB, A1 class, and a way to plug it into your computer.
- [ ] 5 V, 2.5 A micro-USB power supply.
- [ ] Raspberry Pi Imager 2.0 or newer installed on your computer.
- [ ] An SSH key on your computer (`id_ed25519.pub`); see [step 1](#ssh-key).
- [ ] A **2.4 GHz** Wi-Fi network with a password (no login page, no user name), and its
      exact name and password at hand.
- [ ] A password for the Pi's user account, saved in your password manager.
- [ ] Later, before gifting: the name and password of the Wi-Fi in the Pi's new home.

### Raspberry Pi Imager settings at a glance

| Imager step | Setting | Value |
| --- | --- | --- |
| Device | Raspberry Pi model | **Raspberry Pi Zero 2 W** |
| OS | Operating system | **Raspberry Pi OS (other)** → **Raspberry Pi OS Lite (64-bit)** |
| Storage | Storage device | your microSD card (keep **Exclude system drives** ticked) |
| Hostname | Hostname | `zurich-air` |
| Localisation | Capital city | **Bern (Switzerland)**; this also sets the Wi-Fi country to CH |
| | Time zone | **Europe/Zurich** (suggested automatically) |
| | Keyboard layout | as suggested, or the layout of a keyboard you might plug in |
| User | Username | your choice, lowercase (this is `<user>`) |
| | Password | a strong password, saved in your password manager |
| Wi-Fi | Network type | **Secure network** |
| | SSID / password | your 2.4 GHz network and its password |
| Remote access | Enable SSH | on |
| | Authentication mechanism | **Use public key authentication**; paste your `.pub` key, then **Add** |
| Raspberry Pi Connect | Enable Raspberry Pi Connect | off |
| Interfaces & Features | (only if Imager shows this step) | **Enable SPI** may be ticked; step 4 enables it anyway |

## What you need

See [hardware.md](hardware.md) for the full parts list. For this guide:

- A **Raspberry Pi Zero 2 W with headers** (often sold as "Zero 2 WH"; the GPIO header
  is already soldered on, so the e-paper panel can be connected later without soldering).
- A **microSD card of 16–32 GB**, A1 class, from a reputable brand. It runs around the
  clock, so an "endurance" card is a good choice.
- A **5 V micro-USB power supply rated 2.5 A**, such as the official Raspberry Pi 12.5 W
  micro-USB supply. Phone chargers and computer USB ports often cannot deliver enough.
- A **computer** (macOS, Linux or Windows) with an SD card reader and an internet
  connection.
- A **2.4 GHz Wi-Fi network** that you control (see [Choose the Wi-Fi to set up
  on](#choose-the-wi-fi-to-set-up-on)).

## 1. Prepare your computer

### Install Raspberry Pi Imager

Download Imager for your system from
[raspberrypi.com/software](https://www.raspberrypi.com/software/) and install it. On a
Mac with Homebrew you can instead run, *on your computer*:

```sh
brew install --cask raspberry-pi-imager
```

This guide follows Imager 2.0, which leads you through the steps one screen at a time.
Imager checks for updates when it starts; its **App Options** dialog shows the version you
are running. Older 1.x versions offer the same settings in one dialog ("Edit settings").

### SSH key

You will log in to the Pi with an SSH key instead of a password. A key comes as a pair of
files: a **private key** that never leaves your computer, and a **public key** (ending in
`.pub`) that you give to the Pi.

> [!WARNING]
> Only ever copy, paste or share the file that ends in **`.pub`**. The file without `.pub`
> (`id_ed25519`) is your private key: never paste it into Imager, a chat, an e-mail or an
> issue. If what you are about to paste starts with `-----BEGIN`, stop: that is the
> private key.

**macOS and Linux.** Check whether you already have a key, *on your computer*:

```sh
ls ~/.ssh/id_ed25519.pub
```

If the file is listed, skip to the next step. If you see `No such file or directory`,
create a key:

```sh
ssh-keygen -t ed25519
```

Press Enter to accept the suggested file name. You may set a passphrase (recommended; it
protects the key if your computer is lost) or press Enter twice for none. On a Mac,
`ssh-add --apple-use-keychain ~/.ssh/id_ed25519` stores the passphrase in the keychain so
you do not have to type it each time.

Copy the public key to the clipboard:

```sh
pbcopy < ~/.ssh/id_ed25519.pub     # macOS
cat ~/.ssh/id_ed25519.pub          # Linux: prints it; select the whole line and copy it
```

**Windows 10 and 11.** Open **PowerShell** (the OpenSSH client is built in) and check:

```powershell
Test-Path $env:USERPROFILE\.ssh\id_ed25519.pub
```

`True` means you have a key. If it prints `False`, create one with `ssh-keygen -t ed25519`
and answer the questions as above. Then copy the public key to the clipboard:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub | Set-Clipboard
```

The public key is a single line that starts with `ssh-ed25519`, followed by a long string
of letters and digits and usually a comment. Imager may also find and offer it by itself.

### Choose the Wi-Fi to set up on

The Pi needs Wi-Fi from its very first start, because you have no screen or keyboard on
it. Choose the network carefully; most first-boot problems are Wi-Fi problems.

- **2.4 GHz only.** The Zero 2 W has 2.4 GHz Wi-Fi (802.11b/g/n) and cannot see 5 GHz
  networks. Most home routers send the same network name on both bands, which is fine. If
  your router has a separate name for 5 GHz (for example one ending in `-5G`), use the
  other one.
- **Password networks only.** Imager can set up a network with just a name and a password
  (WPA2/WPA3 "Personal"), or an open one. It cannot set up networks that need a user name
  as well (WPA2-Enterprise or 802.1X, such as eduroam or most office networks).
- **No login pages.** Networks that show a login or terms page in a browser (hotels,
  trains, cafés, many guest networks) do not work: the Pi cannot click through the page.
- **Your computer must be able to reach the Pi.** Guest networks often isolate devices
  from each other; then the Pi is online but you cannot connect to it. Put your computer
  on the same network as the Pi.

Your home Wi-Fi is usually the best choice. A **phone hotspot** works well too, and is the
fallback for everything above:

- **iPhone:** Settings → **Personal Hotspot**: turn on **Allow Others to Join** and
  **Maximise Compatibility** (spelt "Maximize" in US English). The latter switches the
  hotspot to 2.4 GHz. The network name is your iPhone's name.
- **Android:** in the hotspot settings, set the band (often called **AP band**) to
  **2.4 GHz**. The exact menu names vary by manufacturer.

Then connect your computer to the same hotspot.

## 2. Flash the microSD card

> [!NOTE]
> **Untested** on this device. The screen and button names below were checked against the
> Raspberry Pi documentation and the source code of Imager 2.0.12; small differences in
> later versions are possible.

Insert the microSD card into your computer and open **Raspberry Pi Imager**. Imager leads
you through a series of steps, listed on the left. Move on with **Next**; **Back** returns
to the previous step.

1. **Device.** Select **Raspberry Pi Zero 2 W**, then **Next**.

2. **OS.** The list starts with the desktop version, **Raspberry Pi OS (64-bit)**: do *not*
   choose that one. Instead, select the category **Raspberry Pi OS (other)**, then
   **Raspberry Pi OS Lite (64-bit)**, described as "A port of Debian Trixie with no desktop
   environment". Then **Next**.
   - It must be **Lite**: no desktop, because the Zero has 512 MB of memory.
   - It must be **64-bit**: the Python packages Zurich Air installs come ready-made for
     64-bit ARM; on 32-bit they would have to be compiled on the Pi.
   - Do not choose the entries marked **Legacy** (based on the older Debian 12
     "bookworm"). The install script is written for Debian 13 "trixie".

3. **Storage.** Select your microSD card. Check the name and size: everything on the card
   will be erased. Keep **Exclude system drives** ticked so that your computer's own disk
   cannot be chosen by mistake. Then **Next**.

4. **Customisation.** Fill in every step. Do **not** select **Skip customisation**: without
   these settings, a headless Pi cannot get onto the network.

   1. **Hostname:** `zurich-air`. This is the name you will use to find the Pi
      (`zurich-air.local`).
   2. **Localisation:** open **Capital city** and type `Bern`, then choose
      **Bern (Switzerland)**. Imager suggests **Europe/Zurich** as **Time zone** and a
      Swiss keyboard layout; change **Keyboard layout** if you would plug in a different
      keyboard. The capital city also sets the Wi-Fi country (regulatory domain) to
      **CH**; the Pi's Wi-Fi does not work without one. If the Pi will be used in another
      country, choose that country's capital instead.
   3. **User:** enter a **Username** (lowercase letters, digits, `-` and `_`; this is
      `<user>` in the rest of this guide) and a strong **Password**, twice. Save the
      password in your password manager now. SSH will not accept it, but `sudo` (running
      commands as administrator) asks for it, and so does a local console. If you ever
      share logs publicly, a neutral user name (not your real name) means less to redact.
      If Imager offers **Enable passwordless sudo**, leave it off.
   4. **Wi-Fi:** choose **Secure network**, enter the **SSID** (the network name, exactly,
      including capitals and spaces) and the password, twice. Imager may fill in the
      network your computer is on. On a Mac, it may ask whether to *prefill the Wi-Fi
      password from the system keychain*, which needs your Mac's administrator password;
      either answer is fine. Tick **Hidden SSID** only if your network does not broadcast
      its name. You add the Wi-Fi of the Pi's future home later, in
      [step 6](#6-before-gifting-add-the-wi-fi-of-the-new-home).
   5. **Remote access:** turn on **Enable SSH**. Under **Authentication mechanism**, choose
      **Use public key authentication**. Paste your public key (from the clipboard) into
      the key field and select **Add**. The key must appear in the list ("1 SSH key
      configured") before **Next** becomes available. Imager may already have added your
      key. (**Browse** selects a `.pub` file instead; **Import from GitHub** adds the keys
      published for a GitHub account.)
   6. **Raspberry Pi Connect:** leave **Enable Raspberry Pi Connect** off for now (see
      [Remote help after the move](#remote-help-after-the-move-optional)).
   7. **Interfaces & Features**, if Imager shows this step for your choice: you may tick
      **Enable SPI**; step 4 below enables it in any case. Leave everything else off.

   Imager remembers these settings, including the Wi-Fi password, for the next card. On a
   shared computer, clear them afterwards in **App Options** (**Saved Customisation** →
   **Clear**).

5. **Writing.** Check the summary and select **Write**. Imager warns that it is about to
   erase all data on the card; select **I understand, erase and write**. Your computer may
   ask for your password or for permission to access the card.

6. Wait while Imager writes and then verifies the card (several minutes). Let the
   verification finish; do not select **Skip verification**.

7. **Done.** When **Write complete!** appears, Imager ejects the card (or select
   **Eject**). Then select **Finish** and take out the card.

Imager applies your settings on the Pi's first boot, not now. If you put the card back
into your computer later, it may say that it cannot read the card or offer to format it,
because it does not understand the Linux part of the card. That is normal: decline, and
do not format the card.

## 3. First boot and SSH

> [!NOTE]
> **Untested** on this device.

### Power on

1. Push the microSD card into the slot on the Pi (contacts facing the board) until it sits
   flat.
2. Plug the power supply into the micro-USB port labelled **PWR IN**: the one nearest the
   corner of the board. The middle port, labelled **USB**, is for data and will not power
   the Pi properly. Labels are printed on the board next to the ports.
3. Plug the supply into the wall. There is no power switch.

The green LED on the board lights up and flickers while the Pi reads the card. On its
first start the Pi enlarges the file system to fill the card, applies your Imager settings
and joins the Wi-Fi; it may restart once on its own. This usually takes a few minutes. Do
not unplug it during this time.

- **No light at all:** check that the supply is in **PWR IN** and plugged in.
- **A regular, repeating blink pattern** instead of irregular flickering usually means the
  Pi cannot read the card: take the card out, push it in again, or flash it again.

### Find the Pi on the network

Wait **five minutes** after power-on, then, *on your computer*:

```sh
ping zurich-air.local
```

When the Pi is on the network, you see replies such as
`64 bytes from 192.168.1.23: icmp_seq=0 ttl=64 time=8.4 ms` (the numbers differ). Stop
`ping` with Ctrl-C (on Windows it stops by itself after four replies).

If you see `cannot resolve zurich-air.local` or `Unknown host`, give it another five
minutes. Still nothing after ten minutes? Look in your router's list of connected devices
(often under "Home network", "LAN" or "DHCP") for a device called `zurich-air` and note
its IP address, then use that address instead of `zurich-air.local` below. More ways to
find it are in [troubleshooting.md](troubleshooting.md#zurich-airlocal-is-not-found).

### Log in

*On your computer*:

```sh
ssh <user>@zurich-air.local
```

The first time, SSH asks you to confirm the Pi's identity:

```text
The authenticity of host 'zurich-air.local (192.168.1.23)' can't be established.
ED25519 key fingerprint is SHA256:...
Are you sure you want to continue connecting (yes/no/[fingerprint])?
```

Type `yes` and press Enter. If your SSH key has a passphrase, SSH asks for it (this is the
key's passphrase, not the Pi's password). You are in when the prompt changes to
`<user>@zurich-air:~ $`. Type `exit` to log out.

> [!TIP]
> If you flash the card again later, the Pi gets a new identity and SSH refuses to connect
> with `WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!`. That is expected after
> re-flashing. Remove the old entry, *on your computer*, then connect again:
>
> ```sh
> ssh-keygen -R zurich-air.local
> ```

Optionally, add an entry to `~/.ssh/config` *on your computer* so that `ssh zurich-air` is
enough from now on:

```text
Host zurich-air
    HostName zurich-air.local
    User <user>
```

### Check that SSH refuses passwords

On the Pi:

```sh
sudo sshd -T | grep -E '^(passwordauthentication|kbdinteractiveauthentication) '
```

Both lines should end in `no`. If `passwordauthentication yes` appears, turn it off. Keep
this session open and try a new login from a second terminal before you close it:

```sh
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/10-no-passwords.conf
sudo systemctl reload ssh
```

### Check the clock and time zone

```sh
timedatectl
```

It should show `Time zone: Europe/Zurich` and `System clock synchronized: yes`. If the time
zone is wrong: `sudo timedatectl set-timezone Europe/Zurich`. The system locale
(`locale`) does not matter to Zurich Air, which formats times itself; change it with
`sudo raspi-config` → Localisation Options if you like.

## 4. Update the system and enable SPI

> [!NOTE]
> **Untested** on this device.

```sh
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config nonint do_spi 0
sudo reboot
```

The upgrade can take a while on a Zero; let it finish. `do_spi 0`
means *enable* (raspi-config's non-interactive mode uses 0 for yes). The e-paper panel
talks SPI, and the setting takes effect after the reboot, which ends your SSH session.

Wait a minute, log in again and check:

```sh
ls -l /dev/spidev*
```

`/dev/spidev0.0` and `/dev/spidev0.1`, owned by group `spi`, should be listed.

## 5. Install Zurich Air

> [!NOTE]
> **Untested on a Pi.** The script has only been dry-run with `apt`, `sudo` and
> `systemctl` stubbed out.

Run these three commands as your normal user (not with `sudo` in front):

```sh
sudo apt install -y git
git clone https://github.com/nandometzger/zurich-air.git ~/zurich-air
~/zurich-air/scripts/install-pi.sh
```

The script asks for nothing except, possibly, your password for `sudo`. It:

1. installs the apt packages Zurich Air needs (`git`, `curl`, `ca-certificates`,
   `python3-venv`) and those the e-paper driver will need (`python3-spidev`,
   `python3-lgpio`, `python3-gpiozero`); a hardware package your apt sources lack is
   skipped with a warning;
2. installs [uv](https://docs.astral.sh/uv/) into `~/.local/bin` if it is missing;
3. clones the repository, or fast-forwards an existing checkout to `origin/main`;
4. creates `~/zurich-air/.venv` on the system Python with access to the system site
   packages, so the apt-installed hardware bindings are importable, and installs the
   locked dependencies without the development tools;
5. creates `~/zurich-air/config.toml` from `config.example.toml` unless it exists;
6. installs the systemd service `zurich-air` for your user and this directory, enables it
   at boot and (re)starts it;
7. checks the result with `zurich-air doctor` (below).

Run `~/zurich-air/scripts/install-pi.sh --help` for its options (another directory, a
different branch, or `--no-pull` to try local changes). Running it again is safe.

### Check the installation

`zurich-air doctor` checks, one by one: the configuration file, the time zone, the system
clock, font rendering (and how long a frame takes to draw), the display backend (in mock
mode, that the output folder is writable), the airport in the bundled airport data, the
route cache folder, one test request to each configured data source and to the weather
service, and the state of the service. It reports each check; if one fails, it says so and
exits with a non-zero status. Run it whenever something seems off:

```sh
cd ~/zurich-air
.venv/bin/zurich-air doctor --config config.toml
```

Add `--offline` to skip the test requests, for example when you run it several times in a
row.

Then watch the service at work:

```sh
systemctl status zurich-air --no-pager
journalctl -u zurich-air -f
```

`systemctl status` should show `active (running)`. The log should show
`running: update every 60 s (600 s in quiet hours 23:30-06:00)` and then, once a minute,
a line such as
`update: adsb.fi, live, 23 aircraft, featured SWR8 EZY12 DLH4, display refreshed`.
Ctrl-C stops following the log; the service keeps running.

In mock mode the service draws to `~/zurich-air/previews/latest.png`, replaced on every
change, so it never fills the card. To look at it, *on your computer*:

```sh
scp <user>@zurich-air.local:zurich-air/previews/latest.png .
open latest.png        # macOS; on Linux or Windows, open the file in an image viewer
```

[reading-the-display.md](reading-the-display.md) explains what you see.

### Settings and the optional API key

Edit `~/zurich-air/config.toml` (`nano ~/zurich-air/config.toml`; save with Ctrl-O, Enter,
and leave with Ctrl-X), then apply it with `sudo systemctl restart zurich-air`. Every
setting is explained in the file and in [configuration.md](configuration.md).

An adsb.lol API key, if you have one, does not belong in the config. Put it in
`~/zurich-air/.env`, which git ignores and the service reads at start:

```sh
nano ~/zurich-air/.env       # one line: ZURICH_AIR_ADSB_LOL_KEY=<your key>
chmod 600 ~/zurich-air/.env
sudo systemctl restart zurich-air
```

See [data-sources.md](data-sources.md) for the services Zurich Air uses and their terms.

## 6. Before gifting: add the Wi-Fi of the new home

> [!NOTE]
> **Untested** on this device.

The Pi will live on a different Wi-Fi network from the one you set it up on. Add that
network now, while you can still reach the Pi. You need its name (SSID) and password, and
it must offer 2.4 GHz.

```sh
sudo nmtui
```

In the text interface (arrow keys and Enter to move, Tab between fields):
**Edit a connection** → **Add** → **Wi-Fi** → **Create**. Enter a profile name, the
**SSID**, **Security** "WPA & WPA2 Personal" (or "WPA3 Personal" if that is all the
network offers) and the **Password**. Leave "Automatically connect" and "Available to all
users" ticked, choose **OK**, then **Back** and **Quit**. The Pi keeps its current
connection; the new profile is used when that network is in range.

Type the password into the `nmtui` form rather than into a command: that way it never
lands in your shell history. NetworkManager stores it in
`/etc/NetworkManager/system-connections/`, readable only by root.

> [!WARNING]
> **Never commit Wi-Fi credentials or paste them into issues**, and do not paste the
> output of `nmcli --show-secrets` anywhere. Network names reveal where the device lives:
> redact them in logs you share too.

**How NetworkManager chooses.** When several saved networks are in range, it activates the
one with the highest `connection.autoconnect-priority` (default 0); among equal
priorities, the one used most recently. It does not drop a working connection because a
higher-priority network appears; the priority matters at boot and when the current
connection is lost. In the new home only that network is in range, so the default works;
giving it a higher priority makes the choice explicit:

```sh
nmcli -f NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY,DEVICE connection show
sudo nmcli connection modify "<profile name>" connection.autoconnect-priority 10
```

The list should show both Wi-Fi profiles with `AUTOCONNECT` `yes`; the active one has
`wlan0` under `DEVICE`. If the new home is in another country, also set its Wi-Fi country
code, for example `sudo raspi-config nonint do_wifi_country DE`.

**If you do not know the password yet:** also add your phone's hotspot as a profile (see
[Choose the Wi-Fi to set up on](#choose-the-wi-fi-to-set-up-on) for the 2.4 GHz setting).
On site, turn the hotspot on, join it with your laptop, `ssh <user>@zurich-air.local` and
add the home network with `sudo nmtui` there.

### Hand-over checklist

- [ ] `sudo apt update && sudo apt full-upgrade -y` once more.
- [ ] The new home's Wi-Fi is listed by `nmcli connection show`.
- [ ] After `sudo reboot`, `systemctl status zurich-air` shows `active (running)` without
      you starting anything, and `zurich-air doctor` passes.
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
offers a remote shell through your Raspberry Pi ID without router changes: on the Lite OS,
install `rpi-connect-lite` and follow that page. It is a hosted service, so weigh the
convenience against giving a third party a path into the device. Not tried with Zurich Air
yet.

## 7. Updating later

Run the install script again. It fast-forwards the checkout to `origin/main`, installs new
apt packages and locked dependencies if there are any, refreshes the service file and
restarts the service:

```sh
~/zurich-air/scripts/install-pi.sh
```

or, *on your computer*, in one go (`-t` lets `sudo` ask for a password if it needs one):

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
journalctl -u zurich-air -e                     # jump to the end of the log (q to quit)
journalctl -u zurich-air -p warning             # only warnings and errors
journalctl -u zurich-air -b --no-pager          # everything since the last boot
journalctl -u zurich-air --since "1 hour ago"
systemctl status zurich-air                     # running? last few log lines
sudo systemctl stop zurich-air
sudo systemctl start zurich-air
sudo systemctl restart zurich-air               # e.g. after editing config.toml
sudo systemctl disable --now zurich-air         # stop, and do not start at boot
sudo systemctl enable --now zurich-air          # start, and start at every boot
```

If the program crashes, systemd restarts it after 30 seconds, then waits longer after each
further crash, up to 10 minutes between attempts. After a fix, start afresh with
`sudo systemctl reset-failed zurich-air && sudo systemctl restart zurich-air`.

For more detail in the log, run it by hand for a while (Ctrl-C stops it):

```sh
sudo systemctl stop zurich-air
cd ~/zurich-air
.venv/bin/zurich-air run --log-level debug --config config.toml
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

> [!NOTE]
> **Untested on a Pi.** The behaviour below is covered by the test suite; the systemd parts
> (the watchdog) have not yet been seen on a Zurich Air device.

**What the log says.** One line per update, as above, and once an hour a summary:

```text
last hour: 60 updates, 58 with new data, 14 display refreshes, 0 errors; failed requests: adsb.fi 2, adsb.lol 0
```

Problems are logged as warnings or errors with their journal priority, so
`journalctl -u zurich-air -p warning` lists only those. An unexpected error is logged with
its traceback at most once an hour, then as a single line. Zurich Air logs no network
names, addresses or location; callsigns in the log are public flight identifiers, and
aircraft that opted out of tracking never appear by name.

**Outages.** When no new data arrives, the last picture stays up. After 5 minutes without
new data (`update.stale_after_s`) the dot next to the clock turns hollow and the footer
says `NO DATA SINCE 14:05`. After 30 minutes (`update.hide_after_s`) the radar is cleared
and the list says `WAITING FOR DATA`, so aircraft long gone never pass for current
traffic. The app keeps trying and returns to normal by itself.

**Quiet hours.** From 23:30 to 06:00 (`update.quiet_hours`, local time) it updates every
10 minutes instead of every minute (`update.quiet_interval_s`): fewer requests to the data
services and fewer panel refreshes while there is little traffic. Set `quiet_hours = ""`
to update all night.

**Watchdog.** The service is of `Type=notify`: the app tells systemd when it has started,
then that it is alive, after every update and at least once a minute while it waits for
the next (also during quiet hours). If it stays silent for 5 minutes (`WatchdogSec=` in
the unit), systemd kills and restarts it; the log then shows `Watchdog timeout`. Errors
inside an update are logged and the next update carries on; only a display that fails
three times in a row ends the program, so that systemd starts it again.

**Clock.** The Pi has no battery-backed clock: after a power cut it starts with the time it
last saved, until NTP corrects it over the network (`timedatectl` then shows `System clock
synchronized: yes`). The service deliberately does not wait for that first sync: with the
network down at boot it would never start, and the panel would keep its last picture,
live dot included. It starts at once and shows `WAITING FOR DATA` instead; only while the
clock reads a date before 2026 does it neither fetch nor draw, looking again every 30
seconds.

## Troubleshooting

Symptoms, causes and fixes, from a card that does not boot to a display that says
`NO DATA`, are collected in [troubleshooting.md](troubleshooting.md).
