"""Regenerate the images in docs/images from synthetic data, with the real renderer.

    uv run python scripts/render-docs-images.py [--out docs/images] [--only NAME ...]

Every screen in these images comes from the same pipeline and renderer as the live
display, fed with the synthetic scenes of ``zurich_air.data.synthetic``; only the frame,
callouts and captions around the screens are drawn here. Times are fixed and nothing is
random, so running this twice writes identical files (with the same Pillow build).
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from zurich_air.cli import preview_scene
from zurich_air.config import Config
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.extras import Extras, RouteLookupTable
from zurich_air.pipeline import build_scene
from zurich_air.processing.geo import KM_PER_NM, RadarProjection, destination
from zurich_air.processing.history import TrackHistory
from zurich_air.rendering import formatting
from zurich_air.rendering.fonts import FONT_DIR, cap_height, font, text_width
from zurich_air.rendering.layout import (
    CALLSIGN,
    FOOTER_GAP,
    LINE_GAP,
    MAX_ROW_PITCH,
    RADAR_GAP,
    RING_DOT_SPACING,
    SMALL,
    TITLE,
    TRACKING,
    Layout,
)
from zurich_air.rendering.radar import RadarPlot, draw_radar
from zurich_air.rendering.renderer import Renderer, Scene

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "docs" / "images"

Color = tuple[int, int, int]
Point = tuple[float, float]
Box = tuple[int, int, int, int]  # left, top, right, bottom (inclusive)

# E-paper tones: the panel's "white" is a warm light grey, its "black" not quite black.
PAPER: Color = (226, 223, 215)
INK: Color = (38, 38, 40)
PAGE: Color = (255, 255, 255)  # background of the diagrams, as on a docs page
MUTED: Color = (112, 110, 106)  # secondary caption text
SCREEN_EDGE: Color = (200, 196, 187)  # hairline around a screen on the page

SUPERSAMPLE = 4  # for the anti-aliased lines, circles and text drawn around the screens


# --- screens ----------------------------------------------------------------------------------


def epaper(image: Image.Image, scale: int = 1) -> Image.Image:
    """A 1-bit render as a two-colour palette image in e-paper tones, *scale* times enlarged."""
    indices = image.convert("L").point([0] * 128 + [1] * 128)
    tinted = Image.frombytes("P", image.size, indices.tobytes())
    tinted.putpalette([*INK, *PAPER])
    size = (image.width * scale, image.height * scale)
    return tinted.resize(size, Image.Resampling.NEAREST) if scale > 1 else tinted


def typical_scene(config: Config) -> Scene:
    return preview_scene("typical", config)


def outage_scene(config: Config, minutes: int) -> Scene:
    """The typical traffic, as last received *minutes* before the clock in the header.

    Only the traffic is out: the weather is still fresh, and the runways in use, seen
    longer ago than the runway inference remembers, are not shown.
    """
    data = synthetic.scene("typical")
    old = dataclasses.replace(data.snapshot, timestamp=data.now - timedelta(minutes=minutes))
    return build_scene(old, None, config, now=data.now, weather=data.weather)


# --- drawing helpers --------------------------------------------------------------------------


def caption_font(size: float) -> ImageFont.FreeTypeFont:
    """B612 for captions and callout numbers, anti-aliased (unlike the 1-bit screen)."""
    path = str(FONT_DIR / "B612-Regular.ttf")
    return ImageFont.truetype(path, round(size), layout_engine=ImageFont.Layout.BASIC)


class Overlay:
    """Anti-aliased drawing in canvas pixels: drawn supersampled, then scaled down."""

    def __init__(self, size: tuple[int, int]) -> None:
        self.size = size
        big = (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE)
        self.layer = Image.new("RGBA", big, (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.layer)

    @staticmethod
    def _xy(p: Point) -> tuple[float, float]:
        return p[0] * SUPERSAMPLE, p[1] * SUPERSAMPLE

    def line(self, points: Sequence[Point], color: Color, width: float) -> None:
        """A polyline with round joints and ends."""
        pts = [self._xy(p) for p in points]
        w = round(width * SUPERSAMPLE)
        self.draw.line(pts, fill=(*color, 255), width=w, joint="curve")
        for x, y in (pts[0], pts[-1]):
            self.draw.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=(*color, 255))

    def circle(
        self,
        centre: Point,
        radius: float,
        fill: Color | None,
        outline: Color | None = None,
        width: float = 1,
    ) -> None:
        x, y = self._xy(centre)
        r = radius * SUPERSAMPLE
        self.draw.ellipse(
            (x - r, y - r, x + r, y + r),
            fill=None if fill is None else (*fill, 255),
            outline=None if outline is None else (*outline, 255),
            width=round(width * SUPERSAMPLE),
        )

    def text(self, at: Point, text: str, size: float, color: Color, anchor: str = "mm") -> None:
        fnt = caption_font(size * SUPERSAMPLE)
        self.draw.text(self._xy(at), text, font=fnt, fill=(*color, 255), anchor=anchor)

    def paste_onto(self, canvas: Image.Image, clip: Box | None = None) -> None:
        """Composite onto *canvas* (RGB), only inside *clip* if given."""
        small = self.layer.resize(self.size, Image.Resampling.BOX)
        if clip is not None:
            inside = Image.new("L", self.size, 0)
            ImageDraw.Draw(inside).rectangle(clip, fill=255)
            small.putalpha(Image.composite(small.getchannel("A"), inside, inside))
        canvas.paste(small, (0, 0), small)


def tint(canvas: Image.Image, mask: Image.Image, color: Color, percent: int) -> Image.Image:
    """*canvas* blended towards *color* where *mask* is white, by up to *percent* %."""
    solid = Image.new("RGB", canvas.size, color)
    return Image.composite(solid, canvas, mask.point(lambda v: v * percent // 100))


def blurred_box(size: tuple[int, int], box: Box, radius: float) -> Image.Image:
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rectangle(box, fill=255)
    return mask.filter(ImageFilter.GaussianBlur(radius))


def save_png(image: Image.Image, path: Path, colors: int | None = None) -> None:
    """Save *image* as a compressed PNG, reduced to *colors* (without dithering) if given."""
    if colors is not None:
        image = image.convert("RGB").quantize(
            colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
    image.save(path, optimize=True)


# --- preview ----------------------------------------------------------------------------------


def render_preview(config: Config, out: Path) -> list[Path]:
    """The typical scene at 1x and 2x, as ``zurich-air preview --scene typical --scale 2``."""
    image = Renderer(config).render(typical_scene(config))
    paths = [out / "preview.png", out / "preview@2x.png"]
    image.save(paths[0])
    image.resize((image.width * 2, image.height * 2), Image.Resampling.NEAREST).save(paths[1])
    return paths


# --- hero -------------------------------------------------------------------------------------

HERO_SCALE = 2
HERO_SIZE = (1320, 1040)
HERO_BACKGROUND: Color = (230, 226, 219)
SHADOW: Color = (120, 112, 102)
MAT: Color = (247, 245, 240)
MAT_BEVEL: Color = (253, 252, 249)  # the cut edge of the mat window
FRAME: Color = (46, 43, 41)
FRAME_REBATE: Color = (70, 66, 62)  # where the frame meets the mat
PANEL_BORDER = 14  # px of blank e-paper around the screen, inside the mat window
MAT_WIDTH = 78
FRAME_WIDTH = 20


def render_hero(config: Config, out: Path) -> list[Path]:
    """The screen on e-paper, matted and framed on a wall: the README's first picture."""
    screen = epaper(Renderer(config).render(typical_scene(config)), HERO_SCALE).convert("RGB")
    window = (screen.width + 2 * PANEL_BORDER, screen.height + 2 * PANEL_BORDER)
    border = MAT_WIDTH + FRAME_WIDTH
    outer = (window[0] + 2 * border, window[1] + 2 * border)
    left, top = (HERO_SIZE[0] - outer[0]) // 2, (HERO_SIZE[1] - outer[1]) // 2 - 8
    fx0, fy0, fx1, fy1 = left, top, left + outer[0] - 1, top + outer[1] - 1

    canvas = Image.new("RGB", HERO_SIZE, HERO_BACKGROUND)
    canvas = tint(canvas, blurred_box(HERO_SIZE, (fx0 + 6, fy0 + 22, fx1 - 6, fy1 + 20), 26),
                  SHADOW, 50)  # fmt: skip
    canvas = tint(canvas, blurred_box(HERO_SIZE, (fx0 + 2, fy0 + 6, fx1 - 2, fy1 + 5), 6),
                  SHADOW, 30)  # fmt: skip

    draw = ImageDraw.Draw(canvas)
    draw.rectangle((fx0, fy0, fx1, fy1), fill=FRAME)
    fw, rebate = FRAME_WIDTH, 4
    draw.rectangle((fx0 + fw - rebate, fy0 + fw - rebate, fx1 - fw + rebate, fy1 - fw + rebate),
                   fill=FRAME_REBATE)  # fmt: skip
    mat = (fx0 + fw, fy0 + fw, fx1 - fw, fy1 - fw)
    draw.rectangle(mat, fill=MAT)
    # The frame shades the mat along its top and left edges.
    edge = Image.new("L", HERO_SIZE, 0)
    ImageDraw.Draw(edge).rectangle(mat, fill=255)
    ImageDraw.Draw(edge).rectangle((mat[0] + 3, mat[1] + 4, mat[2] + 6, mat[3] + 6), fill=0)
    canvas = tint(canvas, edge.filter(ImageFilter.GaussianBlur(3)), (190, 186, 178), 45)

    draw = ImageDraw.Draw(canvas)
    wx, wy = mat[0] + MAT_WIDTH, mat[1] + MAT_WIDTH
    win = (wx, wy, wx + window[0] - 1, wy + window[1] - 1)
    draw.rectangle((win[0] - 3, win[1] - 3, win[2] + 3, win[3] + 3), fill=MAT_BEVEL)
    draw.rectangle(win, fill=PAPER)
    canvas.paste(screen, (wx + PANEL_BORDER, wy + PANEL_BORDER))
    # The panel sits behind the mat, which shades it a little along the top and left.
    recess = Image.new("L", HERO_SIZE, 0)
    ImageDraw.Draw(recess).rectangle(win, fill=255)
    ImageDraw.Draw(recess).rectangle((win[0] + 2, win[1] + 3, win[2] + 4, win[3] + 4), fill=0)
    canvas = tint(canvas, recess.filter(ImageFilter.GaussianBlur(2)), (150, 146, 138), 40)

    path = out / "hero.png"
    save_png(canvas, path, colors=64)
    return [path]


# --- anatomy ----------------------------------------------------------------------------------

ANATOMY_SCALE = 2
ANATOMY_MARGIN = (104, 76, 104, 76)  # left, top, right, bottom
CALLOUT_RADIUS = 13.5
CALLOUT_TEXT = 15
DOT_RADIUS = 3.2
HALO = 1.6  # px of e-paper cleared around leaders and dots, so they read over the screen


@dataclass(frozen=True)
class Callout:
    """A number in the margin on *side*, joined by a leader to a feature of the screen.

    *path* runs from the feature, in screen pixels, to where the leader heads for the
    margin. A *bracket* (top and bottom screen row) marks a span instead of a dot.
    """

    number: int
    side: str  # "left", "top", "right" or "bottom"
    path: tuple[Point, ...]
    bracket: tuple[float, float] | None = None


def anatomy_callouts(config: Config, scene: Scene) -> list[Callout]:
    """The callouts of the anatomy picture, placed from the layout and the radar plot.

    The order is the one of the legend in docs/reading-the-display.md. The airport line
    fills most of the footer, so leaders from above reach the bottom margin through the
    gap before the data source, or go to the sides instead.
    """
    lay = Layout.for_size(config.display.width, config.display.height)
    plot = _radar_plot(config, scene)
    by_label = {a.label: a.icao for a in scene.aircraft}

    title = font(TITLE, bold=True)
    above_text = lay.header_baseline - cap_height(title) - 3.5
    assert scene.now is not None
    clock = formatting.clock(scene.now, config.location.timezone)
    live_dot_x = lay.width - lay.margin - text_width(clock, title, TRACKING) - 6 - 3
    north, distance, _, count = plot.landmarks[:4]
    cx, cy, r = lay.radar_cx, lay.radar_cy, lay.radar_radius
    # North's leader runs right, between the header rule and the ring, then up between the
    # title and the clock.
    beside_north = (north[2] + 2.5, north[1] + 2)
    title_end = lay.margin + text_width(
        f"{config.location.name.upper()}{formatting.SEPARATOR}LIVE AIR TRAFFIC", title, TRACKING
    )
    north_up = (title_end + 15, beside_north[1])

    # A dot of the 40 km ring, down and to the left, where it is clear of traffic.
    ring_r = 2 * config.radar.ring_step_km * r / config.radar.range_km
    n = round(2 * math.pi * ring_r / RING_DOT_SPACING / 4) * 4  # as radar._dotted_circle
    angle = 2 * math.pi * round(n * 225 / 360) / n
    ring_dot = (cx + round(ring_r * math.sin(angle)), cy - round(ring_r * math.cos(angle)))

    code = plot.landmarks[-1]  # the airport code, placed beside the runways
    code_corner = (code[2] + 1.5, code[1] - 1.5)
    marker = plot.markers[by_label["UAE87"]]  # number 1 in the list
    bbox = marker.sprite.shape.getbbox()
    assert bbox is not None
    beside_marker = (marker.origin[0] + bbox[0] - 3, marker.centre[1])
    trail = _trail_point(config, scene, by_label["SWR93D"], 20)
    badge = next(b for b in plot.badges if b.number == 2).box
    beside_badge = (badge[0] - 2.5, (badge[1] + badge[3]) / 2)
    between_radar_and_list = lay.flight_list.left - 7
    below_list = lay.footer_rule_y - 6
    source = (scene.source_label or "").upper()
    footer_gap = lay.width - lay.margin - text_width(source, font(SMALL), TRACKING) - FOOTER_GAP / 2
    small_middle = cap_height(font(SMALL)) / 2  # above a baseline: the middle of small caps
    row_top = lay.flight_list.top + RADAR_GAP  # cap line of the first row
    callsign_cap = cap_height(font(CALLSIGN, bold=True))
    row_bottom = row_top + callsign_cap + LINE_GAP  # its 2nd line
    second_route = row_bottom + MAX_ROW_PITCH - small_middle  # "→ JFK" in the second row

    def ray(start: Point, dx: float, dy: float, until_y: float) -> Point:
        return start[0] + dx * (until_y - start[1]) / dy, until_y

    return [
        Callout(1, "top", ((lay.margin + 24, above_text),)),
        Callout(2, "top", ((live_dot_x, above_text),)),
        Callout(3, "left", ((count[0] - 2.5, (count[1] + count[3]) / 2),)),
        Callout(4, "top", (beside_north, north_up)),
        Callout(5, "left", (ring_dot,)),
        Callout(6, "left", ((distance[0] - 1.5, (distance[1] + distance[3]) / 2),)),
        Callout(
            7,
            "bottom",
            (
                (cx + 3, cy - 3),
                (cx + 7, cy - 7),
                (between_radar_and_list, cy - 7),
                (between_radar_and_list, below_list),
                (footer_gap, below_list),
            ),
        ),
        Callout(8, "top", (code_corner, ray(code_corner, 1, -1, 0))),
        Callout(9, "left", (beside_marker,)),
        Callout(10, "left", (trail,)),
        Callout(11, "left", (beside_badge,)),
        Callout(
            12,
            "right",
            ((lay.width - 7.5, (row_top + row_bottom) / 2),),
            bracket=(row_top, row_bottom),
        ),
        Callout(13, "right", ((lay.width - 7.5, second_route),)),
        Callout(14, "bottom", ((lay.margin + 30, lay.footer_baseline + 4),)),
        Callout(15, "bottom", ((lay.width - lay.margin - 30, lay.footer_baseline + 4),)),
    ]


def _radar_plot(config: Config, scene: Scene) -> RadarPlot:
    """Where the radar puts everything, from drawing it once more on a scratch image."""
    lay = Layout.for_size(config.display.width, config.display.height)
    scratch = Image.new("1", (lay.width, lay.height), 255)
    return draw_radar(
        scratch, lay, config, scene.aircraft, scene.featured, scene.trails,
        count=len(scene.aircraft),
    )  # fmt: skip


def _trail_point(config: Config, scene: Scene, icao: str, distance_px: float) -> Point:
    """The screen pixel *distance_px* back along the trail of aircraft *icao*."""
    lay = Layout.for_size(config.display.width, config.display.height)
    projection = RadarProjection(
        config.location.latitude, config.location.longitude, config.radar.range_km,
        lay.radar_cx, lay.radar_cy, lay.radar_radius,
    )  # fmt: skip
    aircraft = next(a for a in scene.aircraft if a.icao == icao)
    path = [projection.to_screen(aircraft.lat, aircraft.lon)]
    path += [projection.to_screen(*p) for p in reversed(scene.trails[icao])]
    left = distance_px
    for (x0, y0), (x1, y1) in itertools.pairwise(path):
        length = math.hypot(x1 - x0, y1 - y0)
        if length >= left:
            t = left / length
            return round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)
        left -= length
    raise ValueError(f"the trail of {icao} is shorter than {distance_px} px")


def _number_position(
    points: Sequence[Point], side: str, size: tuple[int, int], margin: tuple[int, int, int, int]
) -> Point:
    """Centre of a callout number, in the middle of the margin on *side*.

    A leader whose last segment already heads into that margin carries straight on;
    otherwise it turns square towards it.
    """
    ml, mt, mr, mb = margin
    target = {"left": ml / 2, "right": size[0] - mr / 2, "top": mt / 2}.get(side, size[1] - mb / 2)
    x1, y1 = points[-1]
    vertical = side in ("top", "bottom")
    if len(points) > 1:
        x0, y0 = points[-2]
        along, across = (y1 - y0, x1 - x0) if vertical else (x1 - x0, y1 - y0)
        remaining = target - (y1 if vertical else x1)
        if along * remaining > 0 and across:
            offset = across * remaining / along
            return (x1 + offset, target) if vertical else (target, y1 + offset)
    return (x1, target) if vertical else (target, y1)


def render_anatomy(config: Config, out: Path) -> list[Path]:
    """The typical screen at 2x with numbered callouts, for docs/reading-the-display.md."""
    scene = typical_scene(config)
    s = ANATOMY_SCALE
    screen = epaper(Renderer(config).render(scene), s).convert("RGB")
    ml, mt, mr, mb = ANATOMY_MARGIN
    size = (screen.width + ml + mr, screen.height + mt + mb)
    canvas = Image.new("RGB", size, PAGE)
    canvas.paste(screen, (ml, mt))
    screen_box = (ml, mt, ml + screen.width - 1, mt + screen.height - 1)
    edge = (screen_box[0] - 1, screen_box[1] - 1, screen_box[2] + 1, screen_box[3] + 1)
    ImageDraw.Draw(canvas).rectangle(edge, outline=SCREEN_EDGE)

    def at(p: Point) -> Point:  # centre of a screen pixel on the canvas
        return ml + s * (p[0] + 0.5), mt + s * (p[1] + 0.5)

    halo, ink = Overlay(size), Overlay(size)
    for callout in anatomy_callouts(config, scene):
        points = [at(p) for p in callout.path]
        number = _number_position(points, callout.side, size, ANATOMY_MARGIN)
        dx, dy = number[0] - points[-1][0], number[1] - points[-1][1]
        k = CALLOUT_RADIUS / math.hypot(dx, dy)
        leader = [*points, (number[0] - dx * k, number[1] - dy * k)]
        if callout.bracket is None:
            halo.circle(points[0], DOT_RADIUS + HALO, PAPER)
            ink.circle(points[0], DOT_RADIUS, INK)
        else:
            x, tick = points[0][0], 2.5 * s
            top, bottom = (at((0, row))[1] - s / 2 for row in callout.bracket)
            bottom += s
            ink.line([(x - tick, top), (x, top), (x, bottom), (x - tick, bottom)], INK, 1)
        halo.line(leader, PAPER, 1 + 2 * HALO)
        ink.line(leader, INK, 1)
        ink.circle(number, CALLOUT_RADIUS, PAGE, INK, 1.3)
        ink.text((number[0], number[1] + 0.5), str(callout.number), CALLOUT_TEXT, INK)
    halo.paste_onto(canvas, clip=screen_box)
    ink.paste_onto(canvas)

    path = out / "anatomy.png"
    save_png(canvas, path, colors=48)
    return [path]


# --- states -----------------------------------------------------------------------------------

STATES_SCALE = 2
STATES_COLUMNS = 2
STATES_GAP = 56  # px between the panels and around them
CAPTION_TITLE = 36
CAPTION_TEXT = 27
CAPTION_HEIGHT = 116


def state_scenes(config: Config) -> list[tuple[str, str, Scene]]:
    """(title, explanation, scene) for each state the display can be in."""
    return [
        ("Live", "A typical afternoon", typical_scene(config)),
        ("Busy", "39 aircraft, and still five in the list", preview_scene("busy", config)),
        ("Quiet", "Only a few aircraft around", preview_scene("quiet", config)),
        (
            "No data since …",
            "The last picture stays; the live dot turns hollow",
            preview_scene("stale", config),
        ),
        (
            "Waiting for data",
            "After 30 minutes without data, the radar is cleared",
            outage_scene(config, 35),
        ),
        ("Empty sky", "No aircraft within 60 km", preview_scene("empty", config)),
    ]


def render_states(config: Config, out: Path) -> list[Path]:
    """A gallery of the display's states, with captions."""
    renderer = Renderer(config)
    s, cols, gap = STATES_SCALE, STATES_COLUMNS, STATES_GAP
    states = state_scenes(config)
    pw, ph = config.display.width * s, config.display.height * s
    rows = math.ceil(len(states) / cols)
    size = (
        cols * pw + (cols + 1) * gap,
        rows * (ph + CAPTION_HEIGHT) + (rows + 1) * gap - gap // 2,
    )
    canvas = Image.new("RGB", size, PAGE)
    draw = ImageDraw.Draw(canvas)
    text = Overlay(size)
    for i, (title, detail, scene) in enumerate(states):
        x = gap + i % cols * (pw + gap)
        y = gap + i // cols * (ph + CAPTION_HEIGHT + gap)
        canvas.paste(epaper(renderer.render(scene), s).convert("RGB"), (x, y))
        draw.rectangle((x - 1, y - 1, x + pw, y + ph), outline=SCREEN_EDGE)
        baseline = y + ph + 26
        text.text((x, baseline), title, CAPTION_TITLE, INK, anchor="lt")
        text.text((x, baseline + CAPTION_TITLE + 12), detail, CAPTION_TEXT, MUTED, anchor="lt")
    text.paste_onto(canvas)
    path = out / "states.png"
    save_png(canvas, path, colors=32)
    return [path]


# --- timelapse --------------------------------------------------------------------------------

TIMELAPSE_FRAMES = 14  # one per minute
TIMELAPSE_FRAME_MS = 700
TIMELAPSE_HOLD_MS = 2100  # the last frame, before the loop starts again
TIMELAPSE_SCALE = 2
# A synthetic scene is one moment: on its own, its traffic would leave the radar within
# minutes and nothing would take its place. So the busy scene's extra flights come along,
# in turn this many minutes after the moment they were placed for.
LATER_MINUTES = (5, 8, 11, 14)
TRAIL_WARMUP_MINUTES = 5  # positions recorded before the first frame, for its trails
MINUTE = timedelta(minutes=1)


def flown(aircraft: Aircraft, config: Config, minutes: float) -> Aircraft | None:
    """*aircraft* *minutes* later (earlier if negative), flying straight on at its speed
    and vertical rate; None while it is on the ground (landed, or not yet taken off)."""
    a = aircraft
    if a.track_deg is None or not a.ground_speed_kt:
        return a
    km = a.ground_speed_kt * KM_PER_NM * minutes / 60
    lat, lon = destination(a.lat, a.lon, a.track_deg, km)
    altitude = a.altitude_ft
    if altitude is not None and a.vertical_rate_fpm:
        altitude = round(altitude + a.vertical_rate_fpm * minutes)
        if altitude <= config.airport.elevation_ft:
            return None
    return dataclasses.replace(a, lat=lat, lon=lon, altitude_ft=altitude)


def traffic(config: Config, minute: int) -> tuple[Aircraft, ...]:
    """The aircraft in the air *minute* minutes after the typical scene."""
    typical = synthetic.scene("typical").snapshot.aircraft
    busy = synthetic.scene("busy").snapshot.aircraft
    assert busy[: len(typical)] == typical  # the busy scene adds to the typical one
    flights = [(a, minute) for a in typical]
    for a, later in zip(busy[len(typical) :], itertools.cycle(LATER_MINUTES), strict=False):
        flights.append((a, minute - later))
    return tuple(x for a, m in flights if (x := flown(a, config, m)) is not None)


def timelapse_frames(config: Config, frames: int = TIMELAPSE_FRAMES) -> list[Image.Image]:
    """The display minute by minute, through the same pipeline as the live app: the
    history builds the trails, each flight list carries over to the next frame, and
    verified routes are remembered (departures keep theirs as they fly out). The weather
    and runways in use stay those of the typical scene."""
    start = synthetic.TIMESTAMP
    history = TrackHistory()
    for minute in range(-TRAIL_WARMUP_MINUTES, 0):
        for a in traffic(config, minute):
            history.add(a.icao, a.lat, a.lon, start + minute * MINUTE)
    typical = synthetic.scene("typical")
    template = typical.snapshot  # source label
    extras = Extras(config, route_table=RouteLookupTable(typical.route))
    renderer = Renderer(config)
    featured: list[str] = []
    images = []
    for minute in range(frames):
        now = start + minute * MINUTE
        snapshot = dataclasses.replace(template, aircraft=traffic(config, minute), timestamp=now)
        history.update(snapshot)
        scene = build_scene(
            snapshot, history, config, now, None, featured,
            weather=typical.weather, runways=typical.runways,
        )  # fmt: skip
        featured = [a.icao for a in scene.featured]
        routes = extras.routes(scene.aircraft, scene.featured, now)
        images.append(renderer.render(dataclasses.replace(scene, routes=routes)))
    return images


def render_timelapse(config: Config, out: Path) -> list[Path]:
    """An animated GIF of the display, one frame per synthetic minute."""
    frames = [epaper(f, TIMELAPSE_SCALE) for f in timelapse_frames(config)]
    path = out / "timelapse.gif"
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=[TIMELAPSE_FRAME_MS] * (len(frames) - 1) + [TIMELAPSE_HOLD_MS],
        loop=0,
    )
    return [path]


# --- main -------------------------------------------------------------------------------------

RENDERERS: dict[str, Callable[[Config, Path], list[Path]]] = {
    "preview": render_preview,
    "hero": render_hero,
    "anatomy": render_anatomy,
    "states": render_states,
    "timelapse": render_timelapse,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help="output directory (default: docs/images)"
    )
    parser.add_argument("--only", nargs="+", choices=list(RENDERERS), help="just these images")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    config = Config()  # the defaults, whatever a local config.toml says
    for name in args.only or RENDERERS:
        for path in RENDERERS[name](config, args.out):
            print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
