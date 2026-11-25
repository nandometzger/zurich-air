"""Badge placement: every number must point at exactly one aircraft, whatever the traffic."""

import itertools
import math

import pytest
from PIL import Image, ImageDraw

from zurich_air.cli import preview_scene
from zurich_air.config import Config
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.processing.geo import destination
from zurich_air.rendering.layout import Layout
from zurich_air.rendering.radar import RadarPlot, draw_radar
from zurich_air.rendering.renderer import Scene

CONFIG = Config()
LAYOUT = Layout.for_size(CONFIG.display.width, CONFIG.display.height)
AIRPORT = (CONFIG.airport.latitude, CONFIG.airport.longitude)


def plot(scene: Scene) -> RadarPlot:
    image = Image.new("1", (LAYOUT.width, LAYOUT.height), 255)
    return draw_radar(image, LAYOUT, CONFIG, scene.aircraft, scene.featured, scene.trails)


def near_airport(icao, bearing, dist_km, track, **kwargs):
    lat, lon = destination(*AIRPORT, bearing, dist_km)
    return Aircraft(icao, lat, lon, track_deg=track, callsign=f"T{icao}", **kwargs)


def scene_of(featured, others=()):
    return Scene(aircraft=[*featured, *others], featured=featured, updated_at=synthetic.TIMESTAMP)


def cluster_scene() -> Scene:
    """Night traffic as it really looks, only denser: all of it within 20 km of ZRH."""
    featured = [
        near_airport("a1", 160, 1, 335),  # landing
        near_airport("a2", 170, 8, 335),  # on final behind it
        near_airport("a3", 320, 6, 320),  # departing
        near_airport("a4", 135, 14, 300),
        near_airport("a5", 70, 7, 250),
        near_airport("a6", 235, 7, 20),
    ]
    others = [
        near_airport("b1", 190, 16, 335),
        near_airport("b2", 15, 10, 200),
        near_airport("b3", 275, 13, 70),
        near_airport("b4", 105, 18, 270),
        near_airport("b5", 350, 19, None),  # position only
    ]
    return scene_of(featured, others)


SCENES = {name: lambda name=name: preview_scene(name, CONFIG) for name in synthetic.SCENE_NAMES}
SCENES["cluster"] = cluster_scene


def pixels(rect):
    return {(x, y) for x in range(rect[0], rect[2] + 1) for y in range(rect[1], rect[3] + 1)}


def marker_pixels(marker, mask="shape"):
    image = getattr(marker.sprite, mask)
    ox, oy = marker.origin
    size = range(image.width)
    return {(ox + x, oy + y) for x in size for y in size if image.getpixel((x, y))}


def runway_pixels(airport):
    canvas = Image.new("1", (LAYOUT.width, LAYOUT.height), 0)
    for runway in airport.runways:
        ImageDraw.Draw(canvas).line(runway, fill=255)
    return {
        (x, y) for x in range(canvas.width) for y in range(canvas.height) if canvas.getpixel((x, y))
    }


@pytest.fixture(params=sorted(SCENES))
def placed(request):
    return plot(SCENES[request.param]())


def test_every_featured_aircraft_in_range_has_one_badge(placed):
    numbers = [b.number for b in placed.badges]
    assert numbers == sorted(set(numbers))
    assert {b.target for b in placed.badges} <= {m.centre for m in placed.markers.values()}


def test_badges_cover_no_marker_badge_or_landmark(placed):
    taken = set().union(*(marker_pixels(m) for m in placed.markers.values()))
    taken |= set().union(set(), *(pixels(r) for r in placed.landmarks))
    taken |= runway_pixels(placed.airport)
    for badge in placed.badges:
        box = pixels(badge.box)
        assert not box & taken, badge
        taken |= box


def test_badges_stay_inside_the_scope(placed):
    cx, cy, r = LAYOUT.radar_cx, LAYOUT.radar_cy, LAYOUT.radar_radius
    for badge in placed.badges:
        for x in (badge.box[0], badge.box[2]):
            for y in (badge.box[1], badge.box[3]):
                assert math.hypot(x - cx, y - cy) < r - 1, badge


def test_leaders_join_badge_and_marker_and_touch_nothing_else(placed):
    by_centre = {m.centre: m for m in placed.markers.values()}
    for badge in placed.badges:
        leader = badge.leader
        steps = [(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(leader)]
        assert all(max(abs(dx), abs(dy)) == 1 for dx, dy in steps), "unbroken"
        assert len(set(steps)) <= 2, "straight, 45° or a regular 2:1 staircase"
        # It starts right outside its own marker's halo and ends next to its badge.
        (x0, y0), (x1, y1) = leader[0], leader[-1]
        own = by_centre[badge.target]
        assert min(max(abs(x0 - x), abs(y0 - y)) for x, y in marker_pixels(own, "halo")) == 1
        box = badge.box
        assert max(box[0] - x1, x1 - box[2], box[1] - y1, y1 - box[3]) == 1
        for marker in placed.markers.values():
            assert not set(leader) & marker_pixels(marker, "halo"), badge


def test_cluster_markers_do_not_overlap():
    """Guards the scene above: overlapping markers leave no clear spot for a leader."""
    centres = [m.centre for m in plot(cluster_scene()).markers.values()]
    for i, a in enumerate(centres):
        for b in centres[i + 1 :]:
            assert math.dist(a, b) >= 12


def test_pile_up_still_gets_every_badge_inside_the_scope():
    featured = [near_airport(f"p{i}", 0, 0.2 * i, 45 * i) for i in range(6)]
    placed = plot(scene_of(featured))
    assert [b.number for b in placed.badges] == [1, 2, 3, 4, 5, 6]
    for badge in placed.badges:
        others = [pixels(b.box) for b in placed.badges if b is not badge]
        assert not pixels(badge.box) & set().union(*others)
