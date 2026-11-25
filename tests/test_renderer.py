import dataclasses

import pytest

from zurich_air.cli import preview_scene
from zurich_air.config import Config, DisplayConfig
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.rendering import flight_list
from zurich_air.rendering.fonts import ELLIPSIS
from zurich_air.rendering.renderer import Renderer, Scene

CONFIG = Config()
NOW = synthetic.TIMESTAMP


def render(scene, config=CONFIG):
    return Renderer(config).render(scene)


@pytest.mark.parametrize("name", synthetic.SCENE_NAMES)
def test_every_scene_renders_at_display_size(name):
    image = render(preview_scene(name, CONFIG))
    assert image.mode == "1"
    assert image.size == (CONFIG.display.width, CONFIG.display.height)


def test_rendering_is_deterministic():
    scene = preview_scene("busy", CONFIG)
    assert render(scene).tobytes() == render(scene).tobytes()


def test_aircraft_with_only_a_position_renders():
    bare = Aircraft(icao="4b0001", lat=47.40, lon=8.55)
    image = render(Scene(aircraft=[bare], featured=[bare], updated_at=NOW))
    assert image.size == (400, 300)


def test_odd_values_render():
    odd = Aircraft(
        icao="ABCDEF",
        lat=47.45,
        lon=8.50,
        callsign="AVERYLONGCALLSIGN",
        type_code="NOT-A-REAL-TYPE-CODE",
        altitude_ft=-120,
        track_deg=725.0,
        vertical_rate_fpm=-9000,
        emergency=True,
    )
    far = Aircraft(icao="000001", lat=60.0, lon=8.5, track_deg=0)  # outside the scope
    scene = Scene(aircraft=[odd, far], featured=[odd, far], updated_at=NOW, trails={"x": []})
    render(scene)


def test_trails_change_the_image():
    scene = preview_scene("typical", CONFIG)
    without = dataclasses.replace(scene, trails={})
    assert render(scene).tobytes() != render(without).tobytes()


def test_status_and_source_label_change_the_image():
    scene = preview_scene("quiet", CONFIG)
    variants = [
        scene,
        dataclasses.replace(scene, status="stale"),
        dataclasses.replace(scene, source_label=None),
    ]
    images = {render(s).tobytes() for s in variants}
    assert len(images) == 3


def test_six_featured_rows_fit():
    config = dataclasses.replace(CONFIG, aircraft=dataclasses.replace(CONFIG.aircraft, featured=6))
    image = render(preview_scene("busy", config), config)
    assert image.size == (400, 300)


def test_other_display_size_and_metric_units():
    config = dataclasses.replace(
        CONFIG, display=DisplayConfig(width=640, height=384, altitude_units="metric")
    )
    image = render(preview_scene("typical", config), config)
    assert image.size == (640, 384)


def drawn_texts(monkeypatch, scene, config=CONFIG):
    """Every string the flight list draws, in order."""
    texts = []
    original = flight_list.draw_text

    def record(draw, x, baseline, text, *args, **kwargs):
        texts.append(text)
        return original(draw, x, baseline, text, *args, **kwargs)

    monkeypatch.setattr(flight_list, "draw_text", record)
    render(scene, config)
    return texts


def test_flight_list_labels_arrivals_and_departures(monkeypatch):
    scene = preview_scene("typical", CONFIG)
    texts = drawn_texts(monkeypatch, scene)
    phases = [scene.phases.get(a.icao) for a in scene.featured]
    labels = [t for t in texts if t in ("ARR", "DEP")]
    assert labels == [{"arrival": "ARR", "departure": "DEP"}[p] for p in phases if p]
    assert "ARR" in labels and "DEP" in labels
    without = drawn_texts(monkeypatch, dataclasses.replace(scene, phases={}))
    assert not {"ARR", "DEP"} & set(without)


def test_second_line_shortens_by_meaning_never_with_an_ellipsis(monkeypatch):
    scene = preview_scene("typical", CONFIG)
    texts = drawn_texts(monkeypatch, scene)
    assert "Swiss · 777" in texts  # 777-300ER does not fit beside DEP
    assert not any(ELLIPSIS in t for t in texts)


def test_long_operator_and_model_fall_back_to_the_model(monkeypatch):
    long = Aircraft(
        "4b0001", 47.40, 8.55, callsign="GEC8197", type_code="B77L", altitude_ft=17_000,
        track_deg=318, vertical_rate_fpm=1500,
    )  # fmt: skip
    scene = Scene(aircraft=[long], featured=[long], updated_at=NOW, phases={"4b0001": "departure"})
    texts = drawn_texts(monkeypatch, scene)
    assert "Lufthansa Cargo · 777" not in texts  # too wide even as a family
    assert "777-200LR" in texts
