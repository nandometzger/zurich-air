"""The radar scope: range rings, compass, airport, trails and aircraft (north up).

Featured aircraft carry a numbered badge joined to their marker by a short leader line, as
on an ATC radar data block, so that every number points at exactly one aircraft even in the
cluster around the airport (see :func:`_place_badge`).
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from PIL import Image, ImageDraw

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.reference import is_helicopter
from zurich_air.processing.geo import RadarProjection
from zurich_air.rendering.fonts import cap_height, draw_text, font, text_width
from zurich_air.rendering.layout import (
    AIRPORT_GLYPH_RADIUS,
    BADGE_H,
    BADGE_W,
    FEATURED_LENGTH,
    LANDMARK_CLEARANCE,
    LEADER_MAX,
    LEADER_MIN,
    OTHER_LENGTH,
    OTHER_TRAIL_LENGTH,
    RING_DOT_SPACING,
    SMALL,
    TICK_LENGTHS,
    TICK_ZONE,
    TINY,
    TRACKING,
    TRAIL_FADE,
    TRAIL_LENGTH,
    Layout,
)
from zurich_air.rendering.symbols import (
    AIRLINER,
    BLACK,
    DART,
    WHITE,
    Sprite,
    draw_badge,
    helicopter,
    sprite,
)

_SQUARE = ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0))

Point = tuple[int, int]
Rect = tuple[int, int, int, int]  # left, top, right, bottom (inclusive)
Segment = tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass(frozen=True)
class Marker:
    """An aircraft symbol centred on a pixel."""

    centre: Point
    sprite: Sprite

    @property
    def origin(self) -> Point:
        return self.centre[0] - self.sprite.size // 2, self.centre[1] - self.sprite.size // 2


@dataclass(frozen=True)
class Badge:
    """A numbered badge and the leader line joining it to its marker."""

    number: int
    target: Point  # centre of the marker it labels
    box: Rect  # the black box
    leader: tuple[Point, ...]  # pixels of the line, from the marker to the badge


@dataclass(frozen=True)
class AirportGlyph:
    """The runway layout drawn as a small symbol."""

    box: Rect
    runways: list[Segment]  # empty when drawn as a plain ring

    @property
    def centre(self) -> Point:
        return (self.box[0] + self.box[2]) // 2, (self.box[1] + self.box[3]) // 2


@dataclass(frozen=True)
class RadarPlot:
    """Where everything on the scope went, for tests and diagnostics."""

    markers: dict[str, Marker]  # by ICAO address
    badges: list[Badge]
    landmarks: list[Rect]  # "N", centre cross, airport code (and a runway-less airport)
    airport: AirportGlyph


def draw_radar(
    image: Image.Image,
    layout: Layout,
    config: Config,
    aircraft: Sequence[Aircraft],
    featured: Sequence[Aircraft],
    trails: Mapping[str, Sequence[tuple[float, float]]],
) -> RadarPlot:
    projection = RadarProjection(
        origin_lat=config.location.latitude,
        origin_lon=config.location.longitude,
        range_km=config.radar.range_km,
        center_x=layout.radar_cx,
        center_y=layout.radar_cy,
        radius_px=layout.radar_radius,
    )
    draw = ImageDraw.Draw(image)
    _draw_rings(draw, layout, config)
    airport = _draw_airport(draw, projection, config)
    landmarks = [*_draw_compass(draw, layout), _draw_centre(draw, layout)]

    # Traffic goes onto a copy that is pasted back through a disc, clipping it to the scope.
    traffic = image.copy()
    plot = _draw_traffic(
        traffic, landmarks, airport, projection, config, aircraft, featured, trails
    )
    mask = Image.new("1", image.size, BLACK)
    r = layout.radar_radius - 1
    ImageDraw.Draw(mask).ellipse(
        (layout.radar_cx - r, layout.radar_cy - r, layout.radar_cx + r, layout.radar_cy + r),
        fill=WHITE,
    )
    image.paste(traffic, (0, 0), mask)
    return plot


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
) -> AirportGlyph:
    """The runway layout, enlarged around its centre into a small glyph."""
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
    runways: list[Segment] = []
    if extent > 0:
        k = AIRPORT_GLYPH_RADIUS / extent
        for (x1, y1), (x2, y2) in ends:
            runways.append(
                (round(ax + (x1 - ax) * k), round(ay + (y1 - ay) * k),
                 round(ax + (x2 - ax) * k), round(ay + (y2 - ay) * k))
            )  # fmt: skip
            draw.line(runways[-1], fill=BLACK)
    else:  # no runway data: a small ring
        draw.ellipse((cx - 3, cy - 3, cx + 3, cy + 3), outline=BLACK)
    r = AIRPORT_GLYPH_RADIUS if runways else 3
    return AirportGlyph((cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1), runways)


def _place_airport_code(
    draw: ImageDraw.ImageDraw, code: str, glyph: Rect, clutter: _Clutter
) -> Rect | None:
    """The airport code beside its glyph, on the first side clear of aircraft and badges.

    Right is preferred, then left, below, above and the corners. When aircraft surround
    the airport the code is left out rather than drawn half hidden. Returns its box.
    """
    if not code:
        return None
    fnt = font(TINY)
    cap, width = cap_height(fnt), text_width(code, fnt, TRACKING)
    middle = (glyph[1] + glyph[3]) // 2 + (cap + 1) // 2  # baseline for a centred label
    centre = (glyph[0] + glyph[2]) // 2 - width // 2
    left, right = glyph[0] - 2 - width, glyph[2] + 2
    top, bottom = glyph[1] - 2, glyph[3] + 2 + cap  # baselines above and below
    candidates = (
        (right, middle),
        (left, middle),
        (centre, bottom),
        (centre, top),
        *((x, y) for y in (top, bottom) for x in (right, left)),
    )
    boxes = [(x, base - cap - 1, x + width, base + 1) for x, base in candidates]
    costs = [clutter.box_cost(box) + 5 * i for i, box in enumerate(boxes)]
    best = costs.index(min(costs))
    if costs[best] >= BLOCKED:
        return None
    left, baseline = candidates[best]
    draw_text(draw, left, baseline, code, fnt, tracking=TRACKING)
    return boxes[best]


# --- traffic ------------------------------------------------------------------------------


def _draw_traffic(
    layer: Image.Image,
    landmarks: Sequence[Rect],
    airport: AirportGlyph,
    projection: RadarProjection,
    config: Config,
    aircraft: Sequence[Aircraft],
    featured: Sequence[Aircraft],
    trails: Mapping[str, Sequence[tuple[float, float]]],
) -> RadarPlot:
    draw = ImageDraw.Draw(layer)
    rank = {a.icao: i for i, a in enumerate(featured, start=1)}
    plotted: dict[str, Aircraft] = {}
    for a in (*aircraft, *featured):
        if projection.in_range(a.lat, a.lon):
            plotted.setdefault(a.icao, a)

    markers = {}
    for icao, a in plotted.items():
        x, y = projection.to_screen(a.lat, a.lon)
        markers[icao] = Marker((round(x), round(y)), _sprite(a, icao in rank))

    clutter = _Clutter.empty(layer.size)
    if config.radar.trails:
        for icao, marker in markers.items():
            history = trails.get(icao, ())
            if history:
                points = [marker.centre] + [projection.to_screen(*p) for p in reversed(history)]
                if icao in rank:
                    dots = _trail_dots(points, FEATURED_LENGTH // 2 + 2, TRAIL_LENGTH)
                else:
                    dots = _trail_dots(points, OTHER_LENGTH // 2 + 2, OTHER_TRAIL_LENGTH)
                draw.point(dots, fill=BLACK)
                clutter.add_trail(dots)
    for marker in markers.values():
        clutter.block_marker(marker)
    landmarks = [*landmarks] if airport.runways else [*landmarks, airport.box]
    for rect in landmarks:
        clutter.block_rect(_grow(rect, LANDMARK_CLEARANCE))
    for runway in airport.runways:  # just the strokes: traffic near the airport needs room
        clutter.block_runway(runway, 1 + 2 * LANDMARK_CLEARANCE)

    # Badges are placed before the airport code: telling aircraft apart matters more.
    scope = _Scope(projection.center_x, projection.center_y, projection.radius_px)
    landmark_centres = [((r[0] + r[2]) // 2, (r[1] + r[3]) // 2) for r in landmarks]
    landmark_centres.append(airport.centre)
    badges = []
    for icao in sorted(rank, key=rank.__getitem__):
        if icao not in markers:
            continue
        others = [m.centre for other, m in markers.items() if other != icao]
        badge = _place_badge(
            rank[icao], markers[icao], plotted[icao].track_deg, others, landmark_centres,
            clutter, scope,
        )  # fmt: skip
        clutter.block_rect(_grow(badge.box, 1))
        for px, py in badge.leader:
            clutter.block_rect((px - 1, py - 1, px + 1, py + 1))
        badges.append(badge)
    code = _place_airport_code(draw, config.airport.code.upper(), airport.box, clutter)
    if code:
        landmarks = [*landmarks, code]

    # Others first, then featured from last to first so that #1 ends up on top.
    for icao in sorted(markers, key=lambda icao: -rank.get(icao, len(rank) + 1)):
        markers[icao].sprite.stamp(layer, *markers[icao].centre)
    for badge in badges:
        _draw_badge(draw, badge)
    return RadarPlot(markers=markers, badges=badges, landmarks=landmarks, airport=airport)


def _sprite(a: Aircraft, is_featured: bool) -> Sprite:
    if a.track_deg is None:
        return sprite(_SQUARE, 6 if is_featured else 4, 0)
    angle = round(a.track_deg) % 360
    if not is_featured:
        return sprite(DART, OTHER_LENGTH, angle)
    if is_helicopter(a):
        return helicopter(FEATURED_LENGTH, angle)
    return sprite(AIRLINER, FEATURED_LENGTH, angle)


def _trail_dots(points: Sequence[tuple[float, float]], skip: float, length: float) -> list[Point]:
    """Pixels of a trail along *points* (newest first), from *skip* px out for *length* px.

    Dense dots right behind the aircraft grow ever sparser, so the trail fades instead of
    ending abruptly. Dotted, it never reads as a solid leader line, and dense, never as
    the sparse range rings. Its length is capped so fast jets do not streak across the
    scope.
    """
    dots: list[Point] = []
    next_dot, travelled = skip, 0.0
    end = skip + length
    for (x0, y0), (x1, y1) in itertools.pairwise(points):
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg == 0:
            continue
        while next_dot <= min(travelled + seg, end):
            t = (next_dot - travelled) / seg
            dots.append((round(x0 + (x1 - x0) * t), round(y0 + (y1 - y0) * t)))
            next_dot += 2 + int(next_dot - skip) // TRAIL_FADE
        travelled += seg
    return dots


def _draw_badge(draw: ImageDraw.ImageDraw, badge: Badge) -> None:
    draw.rectangle(_grow(badge.box, 1), fill=WHITE)  # a white margin keeps the box crisp
    draw.point(badge.leader, fill=BLACK)
    draw_badge(draw, badge.box[0], badge.box[1], badge.number)


# --- badge placement ----------------------------------------------------------------------

# Leader directions (dx, dy), so that every leader is a crisp horizontal, vertical or 45°
# line, or a regular 2:1 staircase where traffic leaves no other gap. Earlier ones are
# preferred when all else is equal (see ORDER): up and to the right, as labels usually sit.
_DIRECTIONS = (
    (1, -1), (-1, -1), (1, 1), (-1, 1), (1, 0), (-1, 0), (0, -1), (0, 1),
    (2, -1), (-2, -1), (2, 1), (-2, 1), (1, -2), (-1, -2), (1, 2), (-1, 2),
)  # fmt: skip

# Costs of a badge position, in points. Covering anything is ruled out by BLOCKED; the rest
# trade off against each other: a badge moves 1 px further out for every LENGTH point.
BLOCKED = 1000  # per obstacle pixel under the badge, its margin or the leader
OUTSIDE = 10_000  # per badge corner outside the scope
TICKS = 40  # per badge corner among the compass ticks
TRAIL = 12  # per trail or runway pixel under the badge or crossed by the leader
AMBIGUOUS = 25  # per px that another aircraft is closer to the badge than AMBIGUITY_PX
AMBIGUITY_PX = 12  # px between a badge and the centre of any other aircraft
CROWD = 40  # for pointing into the crowd rather than away from it (scaled by crowding)
CROWD_RADIUS = 45  # px; aircraft and landmarks this close count as crowding
CROWD_SCALE = 12  # crowding counts fully from one neighbour this close (px)
AHEAD = 30  # for a leader along the track: it would read as a speed vector
ASTERN = 15  # for a leader along the trail
LENGTH = 3  # per px of leader beyond LEADER_MIN
ORDER = 1  # per step down the list of _DIRECTIONS: the staircases come last


@dataclass(frozen=True)
class _Clutter:
    """What badges and labels should avoid, as masks that are black where taken.

    Badges must not cover markers, landmarks, the runway glyph or each other. Leaders
    must not cross markers, landmarks or other badges and leaders, but may cross a
    runway stroke: an aircraft landing sits right on top of the glyph. Trails are soft:
    better left uncovered, but not at any price.
    """

    hard: Image.Image  # for badges: everything that is taken
    crossable: Image.Image  # for leaders: the same, without the runway strokes
    soft: Image.Image  # trails and runway strokes

    @classmethod
    def empty(cls, size: tuple[int, int]) -> _Clutter:
        return cls(*(Image.new("1", size, WHITE) for _ in range(3)))

    def block_marker(self, marker: Marker) -> None:
        for mask in (self.hard, self.crossable):
            mask.paste(BLACK, marker.origin, marker.sprite.halo)

    def block_rect(self, rect: Rect) -> None:
        for mask in (self.hard, self.crossable):
            ImageDraw.Draw(mask).rectangle(rect, fill=BLACK)

    def add_trail(self, dots: Sequence[Point]) -> None:
        ImageDraw.Draw(self.soft).point(dots, fill=BLACK)

    def block_runway(self, segment: Segment, width: int) -> None:
        ImageDraw.Draw(self.hard).line(segment, fill=BLACK, width=width)
        ImageDraw.Draw(self.soft).line(segment, fill=BLACK)

    def box_cost(self, rect: Rect) -> float:
        return BLOCKED * _ink(self.hard, rect) + TRAIL * _ink(self.soft, rect)

    def leader_cost(self, pixels: Sequence[Point]) -> float:
        return BLOCKED * _ink_at(self.crossable, pixels) + TRAIL * _ink_at(self.soft, pixels)


@dataclass(frozen=True)
class _Scope:
    cx: float
    cy: float
    radius: float

    def outside(self, x: float, y: float, inset: float) -> bool:
        return math.hypot(x - self.cx, y - self.cy) > self.radius - inset


def _place_badge(
    number: int,
    marker: Marker,
    track_deg: float | None,
    others: Sequence[Point],
    landmarks: Sequence[Point],
    clutter: _Clutter,
    scope: _Scope,
) -> Badge:
    """The best badge for *marker*: in each direction, every leader length is scored.

    A badge never covers a marker, another badge or leader or a landmark if it can avoid
    it, stays inside the scope and keeps its distance from other aircraft. Among those,
    it keeps off trails, points away from crowded areas, keeps its leader off the
    aircraft's nose and tail, and is as close as possible.
    """
    x, y = marker.centre
    away = _away_from_crowd(marker.centre, [*others, *landmarks])
    best: tuple[float, Badge] | None = None
    for index, direction in enumerate(_DIRECTIONS):
        ux, uy = direction
        norm = math.hypot(ux, uy)
        step = norm / max(abs(ux), abs(uy))  # px of length per pixel of leader
        k0 = _first_clear_step(marker.sprite, direction)
        bias = _direction_cost(ux / norm, uy / norm, away, track_deg) + ORDER * index
        for steps in range(math.ceil(LEADER_MIN / step), math.floor(LEADER_MAX / step) + 1):
            offsets = (_ray(direction, k) for k in range(k0, k0 + steps))
            leader = tuple((x + dx, y + dy) for dx, dy in offsets)
            box = _badge_box(leader[-1], _sign(ux), _sign(uy))
            corners = ((box[0], box[1]), (box[2], box[1]), (box[0], box[3]), (box[2], box[3]))
            cost = bias + LENGTH * (steps * step - LEADER_MIN)
            cost += OUTSIDE * sum(scope.outside(px, py, 2) for px, py in corners)
            if cost >= OUTSIDE:
                continue
            cost += TICKS * sum(scope.outside(px, py, TICK_ZONE) for px, py in corners)
            cost += AMBIGUOUS * _ambiguity(box, others)
            if best is not None and cost >= best[0]:
                continue  # already worse, before looking at the masks
            cost += clutter.box_cost(_grow(box, 1)) + clutter.leader_cost(leader)
            if best is None or cost < best[0]:
                best = (cost, Badge(number, marker.centre, box, leader))
    if best is None:  # no room inside the scope at all: sit on the aircraft
        left, top = x - BADGE_W // 2, y - BADGE_H // 2
        return Badge(number, marker.centre, (left, top, left + BADGE_W - 1, top + BADGE_H - 1), ())
    return best[1]


def _ray(direction: Point, k: int) -> Point:
    """Offset of the *k*-th pixel of a line from the origin in *direction*.

    Straight and 45° lines advance one pixel per step; 2:1 lines move one pixel sideways
    every second step, a regular staircase.
    """
    ux, uy = direction
    if abs(ux) == 2:
        return k * _sign(ux), k // 2 * uy
    if abs(uy) == 2:
        return k // 2 * ux, k * _sign(uy)
    return k * ux, k * uy


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _first_clear_step(marker: Sprite, direction: Point) -> int:
    """Steps from the marker's centre in *direction* to the first pixel outside its halo."""
    half, k = marker.size // 2, 1
    while True:
        dx, dy = _ray(direction, k)
        px, py = half + dx, half + dy
        inside = 0 <= px < marker.size and 0 <= py < marker.size
        if not inside or not marker.halo.getpixel((px, py)):
            return k
        k += 1


def _badge_box(anchor: Point, ux: int, uy: int) -> Rect:
    """The badge touching the leader's last pixel *anchor*, continuing in direction (ux, uy).

    A diagonal leader meets the badge at a corner, a straight one at the middle of a side.
    """
    ax, ay = anchor
    left = {1: ax + 1, -1: ax - BADGE_W, 0: ax - BADGE_W // 2}[ux]
    top = {1: ay + 1, -1: ay - BADGE_H, 0: ay - BADGE_H // 2}[uy]
    return left, top, left + BADGE_W - 1, top + BADGE_H - 1


def _away_from_crowd(
    centre: Point, neighbours: Iterable[Point]
) -> tuple[tuple[float, float], float] | None:
    """Unit vector pointing away from nearby aircraft and landmarks, and how crowded it is.

    Each neighbour pushes with a strength that falls off with distance, so the nearest
    ones dominate. Returns None when nothing is near.
    """
    x, y = centre
    vx = vy = 0.0
    for nx, ny in neighbours:
        dx, dy = x - nx, y - ny
        d2 = dx * dx + dy * dy
        if 0 < d2 <= CROWD_RADIUS**2:
            vx, vy = vx + dx / d2, vy + dy / d2
    size = math.hypot(vx, vy)
    if size == 0:
        return None
    return (vx / size, vy / size), min(1.0, size * CROWD_SCALE)


def _direction_cost(
    dx: float,
    dy: float,
    away: tuple[tuple[float, float], float] | None,
    track_deg: float | None,
) -> float:
    """Cost of a leader along unit vector (dx, dy), independent of its length."""
    cost = 0.0
    if away is not None:
        (ax, ay), crowding = away
        cost += CROWD * crowding * (1 - (dx * ax + dy * ay)) / 2
    if track_deg is not None:
        rad = math.radians(track_deg)
        along = dx * math.sin(rad) - dy * math.cos(rad)  # screen y points south
        if along > math.cos(math.radians(30)):
            cost += AHEAD
        elif along < -math.cos(math.radians(30)):
            cost += ASTERN
    return cost


def _ambiguity(box: Rect, others: Sequence[Point]) -> float:
    """How far other aircraft intrude into the AMBIGUITY_PX around a badge, summed.

    A badge right beside another aircraft could be read as its label, leader or not.
    """
    total = 0.0
    for ox, oy in others:
        dx = max(box[0] - ox, 0, ox - box[2])
        dy = max(box[1] - oy, 0, oy - box[3])
        total += max(0.0, AMBIGUITY_PX - math.hypot(dx, dy))
    return total


def _grow(rect: Rect, by: int) -> Rect:
    return rect[0] - by, rect[1] - by, rect[2] + by, rect[3] + by


def _ink(mask: Image.Image, rect: Rect) -> int:
    """Black pixels of *mask* inside *rect* (inclusive); outside the image counts as black."""
    return mask.crop((rect[0], rect[1], rect[2] + 1, rect[3] + 1)).histogram()[0]


def _ink_at(mask: Image.Image, pixels: Iterable[Point]) -> int:
    """Black pixels of *mask* among *pixels*; outside the image counts as black."""
    width, height = mask.size
    return sum(
        not (0 <= px < width and 0 <= py < height) or mask.getpixel((px, py)) == BLACK
        for px, py in pixels
    )
