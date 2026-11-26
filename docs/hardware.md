# Hardware

Zurich Air is a small computer behind a picture frame, driving an e-paper screen. This page
lists the parts and explains why they were chosen. How to set up the software is in
[raspberry-pi.md](raspberry-pi.md).

> [!NOTE]
> **Status.** The e-paper panel has not arrived yet, so its exact model and revision are
> not known, and the e-paper backend (Milestone 6) is not written. Wiring, refresh
> behaviour and driver details will be documented once the panel has been inspected.
> Until then the Pi runs in **mock mode**: it draws each frame to
> `~/zurich-air/previews/latest.png` instead of a screen.

## Bill of materials

| Part | Notes |
| --- | --- |
| Raspberry Pi Zero 2 W with headers ("Zero 2 WH") | The 40-pin GPIO header comes pre-soldered, so the panel can be connected without soldering. Raspberry Pi calls this version "with headers"; shops often sell it as "WH". |
| Waveshare 4.2-inch e-Paper, 400 × 300 pixels, black and white | Talks SPI. Waveshare sells this size in several variants and revisions; the exact one used here, and how it connects, is confirmed when it arrives. |
| microSD card, 16–32 GB, A1 class | From a reputable brand. The Raspberry Pi OS Lite image is about 3 GB; the rest is headroom. An "endurance" card is a good choice for a device that runs around the clock. |
| Power supply, 5 V, 2.5 A, micro-USB | For example the official Raspberry Pi 12.5 W Micro USB Power Supply. It goes into the port labelled **PWR IN**. |
| Picture frame | Chosen in Milestone 8. |

Useful to have for rescue, not needed in normal use: a mini-HDMI cable or adapter, and a
micro-USB OTG adapter with a USB keyboard, to work on the Pi directly if it cannot reach
the network (see [troubleshooting.md](troubleshooting.md#the-pi-does-not-join-the-wi-fi)).

## Why these parts

**Raspberry Pi Zero 2 W.** Small enough to hide behind a frame (65 × 30 mm), with Wi-Fi
built in and no fan, so it is silent. Its quad-core 64-bit processor and 512 MB of memory
are meant to be enough for one update a minute; `zurich-air doctor` reports how long a
frame takes to draw on the device. It runs the standard 64-bit Raspberry Pi OS, so the
Python packages Zurich Air uses install ready-made instead of being compiled on the device.
Raspberry Pi states it will stay in production until at least January 2030. Its Wi-Fi is
2.4 GHz only, which matters when choosing a network (see
[raspberry-pi.md](raspberry-pi.md#choose-the-wi-fi-to-set-up-on)).

**E-paper.** An e-paper screen reflects light like printed paper instead of glowing: there
is no backlight, it is easy to read in daylight, and it does not light up a room at night.
It keeps showing its image without power and draws power only while it changes the
picture. It is silent. A black-and-white panel matches what Zurich Air draws: every frame
is a 1-bit image, with no grey levels to convert. A full refresh takes seconds rather than
milliseconds, which does not matter for a display that changes once a minute; Zurich Air
only sends a frame when it differs from the one already shown.

**400 × 300 pixels at 4.2 inches.** Enough room for the radar and a list of five flights in
legible type, at a size that suits a small picture frame.

## Power draw

No measurements of the finished device exist yet; they will be added once it runs. The
published figures are:

- **Raspberry Pi Zero 2 W:** Raspberry Pi specifies the input as 5 V DC, 2.5 A
  ([product brief](https://datasheets.raspberrypi.com/rpizero2/raspberry-pi-zero-2-w-product-brief.pdf)).
  That is what the power supply must be able to deliver, not what the Pi draws.
- **Waveshare 4.2-inch e-Paper:** Waveshare gives a typical refresh power of 26.4 mW, a
  standby current below 0.01 µA and a full refresh time of 5 s for its 4.2-inch module
  ([Waveshare manual](https://www.waveshare.com/wiki/4.2inch_e-Paper_Module_Manual)). The
  revision used here may differ.

## Display wiring

Coming with **Milestone 6**, once the panel has arrived and its model and revision are
known. Until then, do not rely on any pinout found elsewhere for this project.

## Frame

Coming with **Milestone 8**.

## Other displays

Rendering does not depend on the hardware. One renderer draws every frame as a
black-and-white (1-bit) Pillow image of `display.width` × `display.height` pixels, 400 × 300
by default. A **display backend** then puts that image somewhere: today the mock backend
writes PNG files, and the e-paper backend will drive the panel.

A backend is a small class with the two methods of the `Display` interface in
[`src/zurich_air/display/base.py`](../src/zurich_air/display/base.py):

- `show(image)` puts a full frame on the screen. The image is in Pillow mode `"1"` and has
  the display's size. The app calls it only when the frame differs from the one already
  shown.
- `close()` releases the hardware when the program stops. The display keeps showing the
  last frame.

`create_display()` in
[`src/zurich_air/display/__init__.py`](../src/zurich_air/display/__init__.py) picks the
backend named by `display.backend` in the config; the accepted names are checked in
`src/zurich_air/config.py`. Turning the picture (`display.rotation`) is left to hardware
backends.

So another 400 × 300 black-and-white panel could be supported by writing a backend for it.
The layout is computed from the configured width and height, but it was designed and
tested at 400 × 300, so other sizes may need layout work as well. See
[CONTRIBUTING.md](../CONTRIBUTING.md) and [development.md](development.md) before you start.
