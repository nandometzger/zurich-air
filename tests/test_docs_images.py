"""The script that draws the images in docs/images: it runs, and its output is sound."""

from __future__ import annotations

import importlib.util
import itertools
import math
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageSequence

from zurich_air.config import Config
from zurich_air.data import synthetic

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render-docs-images.py"


@pytest.fixture(scope="module")
def docs():
    """The script as a module (its file name is not importable)."""
    spec = importlib.util.spec_from_file_location("render_docs_images", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        del sys.modules[spec.name]


@pytest.fixture(scope="module")
def images(docs, tmp_path_factory):
    out = tmp_path_factory.mktemp("docs-images")
    assert docs.main(["--out", str(out)]) == 0
    return out


def size_of(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.size


def test_writes_every_image_at_its_size(docs, images):
    config = Config()
    w, h = config.display.width, config.display.height
    ml, mt, mr, mb = docs.ANATOMY_MARGIN
    s = docs.ANATOMY_SCALE
    assert size_of(images / "preview.png") == (w, h)
    assert size_of(images / "preview@2x.png") == (2 * w, 2 * h)
    assert size_of(images / "hero.png") == docs.HERO_SIZE
    assert size_of(images / "anatomy.png") == (s * w + ml + mr, s * h + mt + mb)
    assert (
        size_of(images / "states.png")[0]
        == docs.STATES_COLUMNS * (docs.STATES_SCALE * w + docs.STATES_GAP) + docs.STATES_GAP
    )
    assert size_of(images / "timelapse.gif") == (docs.TIMELAPSE_SCALE * w, docs.TIMELAPSE_SCALE * h)


def test_images_stay_small(images):
    for name in ("hero.png", "anatomy.png", "states.png"):
        assert (images / name).stat().st_size < 300_000, name
    assert (images / "timelapse.gif").stat().st_size < 800_000


def test_preview_stays_one_bit(images):
    with Image.open(images / "preview.png") as image:
        assert image.mode == "1"


def test_timelapse_is_a_loop_of_one_frame_per_minute(docs, images):
    with Image.open(images / "timelapse.gif") as gif:
        assert gif.info["loop"] == 0
        durations = [frame.info["duration"] for frame in ImageSequence.Iterator(gif)]
    assert len(durations) == docs.TIMELAPSE_FRAMES
    assert set(durations[:-1]) == {docs.TIMELAPSE_FRAME_MS}
    assert durations[-1] == docs.TIMELAPSE_HOLD_MS


def test_traffic_lands_and_takes_off(docs):
    config = Config()
    arrival = next(a for a in synthetic.scene("typical").snapshot.aircraft if a.label == "UAE87")
    assert arrival.vertical_rate_fpm is not None and arrival.vertical_rate_fpm < 0
    later = docs.flown(arrival, config, 1)
    assert later is not None and later.altitude_ft < arrival.altitude_ft
    assert docs.flown(arrival, config, 10) is None  # landed
    departure = next(a for a in synthetic.scene("typical").snapshot.aircraft if a.label == "SWR8")
    assert docs.flown(departure, config, -10) is None  # not yet in the air
    labels = {a.label for a in docs.traffic(config, 0)}
    assert {"UAE87", "SWR8"} <= labels


def test_callout_numbers_sit_in_the_margin_apart(docs):
    config = Config()
    scene = docs.typical_scene(config)
    callouts = docs.anatomy_callouts(config, scene)
    assert [c.number for c in callouts] == list(range(1, len(callouts) + 1))

    s = docs.ANATOMY_SCALE
    ml, mt, mr, mb = docs.ANATOMY_MARGIN
    size = (s * config.display.width + ml + mr, s * config.display.height + mt + mb)
    r = docs.CALLOUT_RADIUS
    numbers = []
    for callout in callouts:
        points = [(ml + s * (x + 0.5), mt + s * (y + 0.5)) for x, y in callout.path]
        x, y = docs._number_position(points, callout.side, size, docs.ANATOMY_MARGIN)
        inside_x = ml - r < x < size[0] - mr + r
        inside_y = mt - r < y < size[1] - mb + r
        assert not (inside_x and inside_y), f"callout {callout.number} covers the screen"
        assert r <= x <= size[0] - r and r <= y <= size[1] - r, callout.number
        numbers.append((callout.number, x, y))
    for (n1, x1, y1), (n2, x2, y2) in itertools.combinations(numbers, 2):
        assert math.hypot(x2 - x1, y2 - y1) > 2 * r + 8, f"callouts {n1} and {n2} touch"
