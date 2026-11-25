"""The radar scope: range rings, compass, airport, trails and aircraft (north up)."""

from __future__ import annotations

import itertools
import math
from collections.abc import Mapping, Sequence

from PIL import Image, ImageDraw

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import is_helicopter
from zurich_air.processing.geo import RadarProjection
from zurich_air.rendering.fonts import cap_height, draw_text, font, text_width
from zurich_air.rendering.layout import SMALL, TINY, TRACKING, Layout
from zurich_air.rendering.symbols import (
    AIRLINER,
    BADGE_H,
    BADGE_W,
    BLACK,
    DART,
    WHITE,
    Sprite,
    draw_badge,
    helicopter,
    sprite,
)

RING_DOT_SPACING = 4  # px between the dots of the inner range rings
TICK_LENGTHS = {90: 6, 30: 4, 10: 2}  # compass tick every 10°, longer at 30° and 90°
AIRPORT_GLYPH_RADIUS = 9  # px from the centre of the runway layout to its farthest end
FEATURED_LENGTH = 15  # px, airliner silhouette for aircraft in the flight list
OTHER_LENGTH = 9  # px, dart for all other aircraft
TRAIL_LENGTH = 26  # px of history drawn behind each aircraft
TRAIL_FADE = 8  # dot spacing grows by one pixel every this many px along the trail
_SQUARE = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))

Rect = tuple[int, int, int, int]  # left, top, right, bottom (inclusive)


def draw_radar(
    image: Image.Image,
    layout: Layout,
    config: Config,
    aircraft: Sequence[Aircraft],
    featured: Sequence[Aircraft],
    trails: Mapping[str, Sequence[tuple[float, float]]],
) -> None:
    projection = RadarProjection(
        origin_lat=config.location.latitude,
        origin_lon=config.location.longitude,
        range_km=config.radar.range_km,
        center_x=layout.radar_cx,
        center_y=layout.radar_cy,
        radius_px=layout.radar_radius,
    )
    draw = ImageDraw.Draw(image)
    obstacles = Image.new("1", image.size, WHITE)  # things a badge must not cover
    obstacle_draw = ImageDraw.Draw(obstacles)

    _draw_rings(draw, layout, config)
    for rect in (*_draw_compass(draw, layout), _draw_centre(draw, layout)):
        obstacle_draw.rectangle(rect, fill=BLACK)
    for rect in _draw_airport(draw, projection, config):
        obstacle_draw.rectangle(rect, fill=BLACK)

    # Traffic goes onto a copy that is pasted back through a disc, clipping it to the scope.
    traffic = image.copy()
    _draw_traffic(traffic, obstacles, projection, config, aircraft, featured, trails)
    mask = Image.new("1", image.size, BLACK)
    r = layout.radar_radius - 1
    ImageDraw.Draw(mask).ellipse(
        (layout.radar_cx - r, layout.radar_cy - r, layout.radar_cx + r, layout.radar_cy + r),
        fill=WHITE,
    )
    image.paste(traffic, (0, 0), mask)


# --- static scope -----------------------------------------------------------------------


def _dotted_circle(draw: ImageDraw.ImageDraw, cx: int, cy: int, radius: float) -> None:
    n = round(2 * math.pi * radius / RING_DOT_SPACING / 4) * 4  # symmetric in all quadrants
    points = {
        (cx + round(radius * math.sin(2 * math.pi * i / n)),
         cy - round(radius * math.cos(2 * math.pi * i / n)))
        for i in range(n)
    }  # fmt: skip
    draw.point(sorted(points), fill=BLACK)


def _draw_rings(draw: ImageDraw.ImageDraw, layout: Layout, config: Config) -> None:
    cx, cy, r = layout.radar_cx, layout.radar_cy, layout.radar_radius
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=BLACK)
    px_per_km = r / config.radar.range_km
    step = config.radar.ring_step_km
    k = 1
    while k * step * px_per_km < r - 4:
        _dotted_circle(draw, cx, cy, k * step * px_per_km)
        k += 1


def _draw_compass(draw: ImageDraw.ImageDraw, layout: Layout) -> list[Rect]:
    """Ticks inside the outer ring and an "N" set into the ring at the top."""
    cx, cy, r = layout.radar_cx, layout.radar_cy, layout.radar_radius
    for deg in range(10, 360, 10):
        length = next(v for k, v in TICK_LENGTHS.items() if deg % k == 0)
        s, c = math.sin(math.radians(deg)), math.cos(math.radians(deg))
        draw.line(
            (cx + round((r - 1) * s), cy - round((r - 1) * c),
             cx + round((r - length) * s), cy - round((r - length) * c)),
            fill=BLACK,
        )  # fmt: skip

    fnt = font(SMALL, bold=True)
    cap = cap_height(fnt)
    width = text_width("N", fnt)
    left, baseline = cx - width // 2, cy - r + (cap + 1) // 2
    rect = (left - 2, baseline - cap - 1, left + width + 1, baseline + 1)
    draw.rectangle(rect, fill=WHITE)
    draw_text(draw, left, baseline, "N", fnt)
    return [rect]


def _draw_centre(draw: ImageDraw.ImageDraw, layout: Layout) -> Rect:
    cx, cy = layout.radar_cx, layout.radar_cy
    draw.line((cx - 2, cy, cx + 2, cy), fill=BLACK)
    draw.line((cx, cy - 2, cx, cy + 2), fill=BLACK)
    return (cx - 2, cy - 2, cx + 2, cy + 2)


def _draw_airport(
    draw: ImageDraw.ImageDraw, projection: RadarProjection, config: Config
) -> list[Rect]:
    """The runway layout, enlarged around its centre into a small glyph, and the code."""
    airport = config.airport
    ends = [
        (projection.to_screen(lat1, lon1), projection.to_screen(lat2, lon2))
        for lat1, lon1, lat2, lon2 in airport.runways
    ]
    if ends:
        xs = [x for end in ends for x, _ in end]
        ys = [y for end in ends for _, y in end]
        ax, ay = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    else:
        ax, ay = projection.to_screen(airport.latitude, airport.longitude)
    extent = max((math.hypot(x - ax, y - ay) for end in ends for x, y in end), default=0.0)
    cx, cy = round(ax), round(ay)
    if extent > 0:
        k = AIRPORT_GLYPH_RADIUS / extent
        for (x1, y1), (x2, y2) in ends:
            draw.line(
                (round(ax + (x1 - ax) * k), round(ay + (y1 - ay) * k),
                 round(ax + (x2 - ax) * k), round(ay + (y2 - ay) * k)),
                fill=BLACK,
            )  # fmt: skip
    else:  # no runway data: a small ring
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=BLACK)
    r = AIRPORT_GLYPH_RADIUS
    glyph = (cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1)
    if not airport.code:
        return [glyph]
    fnt = font(TINY)
    cap = cap_height(fnt)
    left = cx + r + 3
    baseline = cy + (cap + 1) // 2
    width = draw_text(draw, left, baseline, airport.code.upper(), fnt, tracking=TRACKING)
    return [glyph, (left - 1, baseline - cap - 1, left + width, baseline + 1)]


# --- traffic ------------------------------------------------------------------------------


def _draw_traffic(
    layer: Image.Image,
    obstacles: Image.Image,
    projection: RadarProjection,
    config: Config,
    aircraft: Sequence[Aircraft],
    featured: Sequence[Aircraft],
    trails: Mapping[str, Sequence[tuple[float, float]]],
) -> None:
    draw = ImageDraw.Draw(layer)
    rank = {a.icao: i for i, a in enumerate(featured, start=1)}
    plotted: dict[str, Aircraft] = {}
    for a in (*aircraft, *featured):
        if projection.in_range(a.lat, a.lon):
            plotted.setdefault(a.icao, a)

    positions = {}
    for icao, a in plotted.items():
        x, y = projection.to_screen(a.lat, a.lon)
        positions[icao] = (round(x), round(y))

    if config.radar.trails:
        for icao in plotted:
            history = trails.get(icao, ())
            if history:
                points = [positions[icao]] + [projection.to_screen(*p) for p in reversed(history)]
                skip = FEATURED_LENGTH // 2 + 2 if icao in rank else OTHER_LENGTH // 2 + 2
                _draw_trail(draw, points, skip)

    # Others first, then featured from last to first so that #1 ends up on top.
    order = sorted(plotted, key=lambda icao: -rank.get(icao, len(rank) + 1))
    for icao in order:
        marker = _marker(plotted[icao], icao in rank)
        x, y = positions[icao]
        marker.stamp(layer, x, y)
        obstacles.paste(BLACK, (x - marker.size // 2, y - marker.size // 2), marker.halo)

    scope = (projection.center_x, projection.center_y, projection.radius_px)
    for icao in sorted(rank, key=rank.__getitem__):
        if icao not in positions:
            continue
        marker = _marker(plotted[icao], is_featured=True)
        left, top = _place_badge(positions[icao], marker, layer, obstacles, scope)
        draw.rectangle((left - 1, top - 1, left + BADGE_W, top + BADGE_H), fill=WHITE)
        draw_badge(draw, left, top, rank[icao])
        ImageDraw.Draw(obstacles).rectangle(
            (left - 1, top - 1, left + BADGE_W, top + BADGE_H), fill=BLACK
        )


def _marker(a: Aircraft, is_featured: bool) -> Sprite:
    if a.track_deg is None:
        return sprite(_SQUARE, 6 if is_featured else 4, 0)
    angle = round(a.track_deg) % 360
    if not is_featured:
        return sprite(DART, OTHER_LENGTH, angle)
    if is_helicopter(a):
        return helicopter(FEATURED_LENGTH, angle)
    return sprite(AIRLINER, FEATURED_LENGTH, angle)


def _draw_trail(
    draw: ImageDraw.ImageDraw, points: Sequence[tuple[float, float]], skip: float
) -> None:
    """Single-pixel dots along *points* (newest first), starting *skip* px out.

    The dots thin out with distance, so the trail fades instead of ending abruptly, and
    stop after TRAIL_LENGTH px so fast jets do not streak across the scope.
    """
    next_dot, travelled = skip, 0.0
    end = skip + TRAIL_LENGTH
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        while next_dot <= min(travelled + seg, end):
            t = (next_dot - travelled) / seg
            draw.point((round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)), fill=BLACK)
            next_dot += 2 + int(next_dot - skip) // TRAIL_FADE
        travelled += seg


# Where a badge may sit around its marker, in order of preference: NE, SE, NW, SW, E, W, N, S.
_DIRECTIONS = ((1, -1), (1, 1), (-1, -1), (-1, 1), (1, 0), (-1, 0), (0, -1), (0, 1))


def _place_badge(
    position: tuple[int, int],
    marker: Sprite,
    layer: Image.Image,
    obstacles: Image.Image,
    scope: tuple[float, float, float],
) -> tuple[int, int]:
    """Top-left corner for a badge: snug against its marker, covering as little as possible.

    In each direction the badge moves out until it clears the marker's halo; the
    candidate that covers the fewest other markers, badges and labels wins (then the
    least clutter, then the closest). Badges never leave the scope.
    """
    x, y = position
    cx, cy, radius = scope
    origin = (x - marker.size // 2, y - marker.size // 2)

    def box_at(ux: int, uy: int, d: int) -> tuple[int, int, int, int]:
        """Badge box *d* px from the marker centre towards (ux, uy), with a 1 px margin."""
        left = {1: x + d, -1: x - d - BADGE_W + 1, 0: x - BADGE_W // 2}[ux]
        top = {1: y + d, -1: y - d - BADGE_H + 1, 0: y - BADGE_H // 2}[uy]
        return left - 1, top - 1, left + BADGE_W + 1, top + BADGE_H + 1

    def touches_marker(box: tuple[int, int, int, int]) -> bool:
        local = (box[0] - origin[0], box[1] - origin[1], box[2] - origin[0], box[3] - origin[1])
        return marker.halo.crop(local).histogram()[255] > 0

    best: tuple[float, tuple[int, int]] | None = None
    for index, (ux, uy) in enumerate(_DIRECTIONS):
        d = 1
        while touches_marker(box_at(ux, uy, d)):
            d += 1
        box = box_at(ux, uy, d)
        corners = ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))
        outside = sum(math.hypot(px - cx, py - cy) > radius - 2 for px, py in corners)
        covered = obstacles.crop(box).histogram()[0]
        clutter = layer.crop(box).histogram()[0]
        cost = 1000 * outside + 10 * covered + clutter + d + 0.5 * index
        if best is None or cost < best[0]:
            best = (cost, (box[0] + 1, box[1] + 1))
    assert best is not None
    return best[1]
