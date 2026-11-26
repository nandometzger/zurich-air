# Zurich Air documentation

Zurich Air is a framed e-paper display of live air traffic around Zürich, driven by a
Raspberry Pi Zero 2 W. These pages cover building one, running it, and changing it. The
[project README](../README.md) has the overview.

## Use it

- [Reading the display](reading-the-display.md): every element on the screen, from the
  radar badges to `NO DATA SINCE`.
- [Hardware](hardware.md): the parts and how they fit together.
- [Raspberry Pi setup](raspberry-pi.md): from a blank microSD card to a running service,
  and handing the device over to its new home.
- [Configuration](configuration.md): every setting with its default, and recipes for
  another city, metric altitudes, quiet hours and the ranking.
- [Troubleshooting](troubleshooting.md): what the log and the display are telling you,
  and how to fix common problems.

## Understand it

- [How it works](how-it-works.md): the path from transponder to pixel, how the five
  aircraft are chosen, how the radar and the 1-bit rendering work, and how it keeps running
  unattended.
- [Data sources](data-sources.md): adsb.fi and adsb.lol, their terms and rate limits,
  what is sent to them, privacy, and why routes are not shown.

## Build on it

- [Development](development.md): setup with uv, the project layout, previews, saved
  snapshots, `explain`, tests and CI.
- [Contributing](../CONTRIBUTING.md): how to propose a change and the project's
  guidelines.
