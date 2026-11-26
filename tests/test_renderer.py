import dataclasses
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from zurich_air.cli import preview_scene
from zurich_air.config import Config, DisplayConfig
from zurich_air.data import synthetic
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.metar import Weather
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse
from zurich_air.rendering import flight_list, formatting, radar, renderer
from zurich_air.rendering.fonts import ELLIPSIS, font, text_width
from zurich_air.rendering.layout import FOOTER_GAP, MIN_SPACE, SMALL, TRACKING, Layout
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


def test_waiting_frame_shows_an_empty_radar_and_a_calm_note(monkeypatch):
    typical = preview_scene("typical", CONFIG)
    empty = dataclasses.replace(typical, aircraft=[], featured=[], trails={}, status="waiting")
    drawn = record_text(monkeypatch)
    render(empty)
    assert [d.text for d in drawn if d.module == "flight_list"] == ["WAITING FOR DATA"]
    texts = [d.text for d in drawn]
    assert "NO DATA SINCE 15:42" in texts
    assert not any("AIRCRAFT" in t for t in texts)  # no count without data: not "NO AIRCRAFT"
    assert "60 KM" in texts  # the range is always there

    drawn.clear()
    render(dataclasses.replace(empty, updated_at=None))  # never had any data
    assert "NO DATA" in [d.text for d in drawn]


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
    scene = dataclasses.replace(preview_scene("typical", CONFIG), routes={})  # no route known
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
    assert "→ JFK" in texts
    assert "Swiss · 777" in texts  # 777-300ER does not fit beside the route
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


def test_fonts_use_basic_layout_engine_on_every_platform():
    # Guards against raqm/HarfBuzz shaping changing text widths between macOS and the Pi.
    from PIL import ImageFont

    from zurich_air.rendering.fonts import font

    assert font(10).font.layout_engine == ImageFont.Layout.BASIC


# --- airport line, routes, count and range -------------------------------------------------

LAYOUT = Layout.for_size(CONFIG.display.width, CONFIG.display.height)
MINUS = formatting.MINUS
OBSERVED = datetime(2026, 12, 24, 14, 20, tzinfo=UTC)
TYPICAL_WEATHER = Weather("LSZH", OBSERVED, 270, False, 8, 18, 4, 1, 1018, "")
STRESS_WEATHER = Weather("LSZH", OBSERVED, None, True, 12, 25, -12, -15, 1033, "")


@dataclass(frozen=True)
class Drawn:
    module: str
    x: int
    baseline: int
    text: str
    width: int
    align: str

    @property
    def left(self) -> int:
        return self.x - self.width if self.align == "right" else self.x

    @property
    def right(self) -> int:  # exclusive
        return self.left + self.width


def record_text(monkeypatch) -> list[Drawn]:
    """Every string the renderer, radar and flight list draw, with where it went."""
    drawn: list[Drawn] = []
    for module in (renderer, radar, flight_list):
        original = module.draw_text

        def record(draw, x, baseline, text, *args, _original=original, _name=module, **kwargs):
            width = _original(draw, x, baseline, text, *args, **kwargs)
            name = _name.__name__.rsplit(".", 1)[-1]
            drawn.append(Drawn(name, x, baseline, text, width, kwargs.get("align", "left")))
            return width

        monkeypatch.setattr(module, "draw_text", record)
    return drawn


def footer(drawn: list[Drawn]) -> list[Drawn]:
    """The footer's texts, from left to right."""
    texts = [d for d in drawn if d.module == "renderer" and d.baseline == LAYOUT.footer_baseline]
    return sorted(texts, key=lambda d: d.left)


def with_routes(scene: Scene) -> Scene:
    icao = {a.label: a.icao for a in scene.aircraft}
    routes = {
        icao["UAE87"]: VerifiedRoute("OMDB", "LSZH", "arrival"),
        icao["SWR8"]: VerifiedRoute("LSZH", "KJFK", "departure"),
        icao["EDW24"]: VerifiedRoute("LSZH", "MDPC", "departure"),
    }
    return dataclasses.replace(scene, routes=routes)


def test_footer_shows_the_airport_line_and_the_source(monkeypatch):
    scene = dataclasses.replace(
        preview_scene("typical", CONFIG),
        weather=TYPICAL_WEATHER,
        runways=RunwayUse(("14",), ("28",)),
    )
    drawn = record_text(monkeypatch)
    render(scene)
    left, right = footer(drawn)
    assert right.text == "SYNTHETIC DATA" and right.right == LAYOUT.width - LAYOUT.margin
    assert left.text == "LDG 14 · DEP 28 · 270° 8 KT G18 · 4 °C · QNH 1018"
    assert left.left == LAYOUT.margin
    assert left.right + FOOTER_GAP <= right.left


def test_footer_drops_parts_from_the_end_at_the_real_width(monkeypatch):
    scene = dataclasses.replace(
        preview_scene("stale", CONFIG),
        weather=STRESS_WEATHER,
        runways=RunwayUse(("14", "16"), ("28", "32")),
    )
    drawn = record_text(monkeypatch)
    render(scene)
    left, right = footer(drawn)
    assert right.text == "NO DATA SINCE 15:31"
    assert left.text == "LDG 14 16 · DEP 28 32 · VRB 12 KT G25"  # no room for temperature, QNH
    assert left.right + FOOTER_GAP <= right.left


@pytest.mark.parametrize("status", ["live", "stale"])
@pytest.mark.parametrize(
    "runways", [None, RunwayUse(("14",), ()), RunwayUse(("14", "16", "34"), ("28", "32", "10"))]
)
@pytest.mark.parametrize("weather", [None, TYPICAL_WEATHER, STRESS_WEATHER])
def test_footer_never_runs_into_the_source(monkeypatch, status, runways, weather):
    scene = dataclasses.replace(
        preview_scene("typical", CONFIG), status=status, weather=weather, runways=runways
    )
    drawn = record_text(monkeypatch)
    render(scene)
    *left, right = footer(drawn)
    expected = formatting.airport_parts(weather, runways)
    if not expected:
        assert left == []  # nothing to say: the left side stays empty
        return
    (line,) = left
    assert line.left == LAYOUT.margin and line.right + FOOTER_GAP <= right.left
    assert expected[0] in line.text  # the runways (or the wind) always come first
    assert line.text == " · ".join(expected[: line.text.count(" · ") + 1])


def test_footer_uses_the_whole_width_without_a_source(monkeypatch):
    scene = dataclasses.replace(
        preview_scene("typical", CONFIG),
        source_label=None,
        weather=STRESS_WEATHER,
        runways=RunwayUse(("14", "16"), ("28", "32")),
    )
    drawn = record_text(monkeypatch)
    render(scene)
    (line,) = footer(drawn)
    assert line.text.endswith("QNH 1033") and f"{MINUS}12 °C" in line.text


def test_weather_shows_while_waiting_for_data(monkeypatch):
    empty = Scene(aircraft=[], featured=[], updated_at=None, status="waiting", now=NOW,
                  weather=TYPICAL_WEATHER)  # fmt: skip
    drawn = record_text(monkeypatch)
    render(empty)
    left, right = footer(drawn)
    assert left.text == "270° 8 KT G18 · 4 °C · QNH 1018" and right.text == "NO DATA"


def test_count_in_the_corner_and_range_on_the_ring(monkeypatch):
    drawn = record_text(monkeypatch)
    render(preview_scene("typical", CONFIG))
    labels = {d.text: d for d in drawn if d.module == "radar"}
    count, distance = labels["17 AIRCRAFT"], labels["60 KM"]
    cx, cy, r = LAYOUT.radar_cx, LAYOUT.radar_cy, LAYOUT.radar_radius
    assert count.left == cx - r and count.baseline < cy - r + 12  # top-left corner
    assert abs((distance.left + distance.right) / 2 - cx) <= 1  # centred under the scope
    assert distance.baseline - 8 < cy + r < distance.baseline  # set into the ring
    assert distance.baseline < LAYOUT.footer_rule_y - 2
    assert not [d for d in footer(drawn) if "AIRCRAFT" in d.text or "KM" in d.text]


def test_routes_replace_arr_dep_and_the_arrow(monkeypatch):
    scene = with_routes(preview_scene("typical", CONFIG))
    texts = drawn_texts(monkeypatch, scene)
    assert {"DXB →", "→ JFK", "→ PUJ"} <= set(texts)
    assert texts.count("ARR") == 1 and "DEP" not in texts  # SWR93D has no route here
    assert "Emirates · A380" in texts and "Edelweiss · A350" in texts  # operator kept


def test_an_overflight_route_replaces_the_track_arrow(monkeypatch):
    scene = preview_scene("quiet", CONFIG)
    overflight = next(a for a in scene.featured if a.label == "THY7HJ")
    assert overflight.icao not in scene.phases
    routed = dataclasses.replace(
        scene, routes={overflight.icao: VerifiedRoute("EGLL", "LTFM", "overflight")}
    )
    assert render(routed).tobytes() != render(scene).tobytes()
    assert "LHR → IST" in drawn_texts(monkeypatch, routed)


def test_route_keeps_its_distance_from_the_description(monkeypatch):
    long = Aircraft(
        "4b0001", 47.40, 8.55, callsign="GEC8197", type_code="B77L", altitude_ft=35_000,
        track_deg=125, vertical_rate_fpm=0,
    )  # fmt: skip
    route = VerifiedRoute("EGLL", "OMDB", "overflight")
    scene = Scene(aircraft=[long], featured=[long], updated_at=NOW, routes={"4b0001": route})
    drawn = record_text(monkeypatch)
    render(scene)
    rows = [d for d in drawn if d.module == "flight_list"]
    label = next(d for d in rows if d.text == "LHR → DXB")
    description = next(d for d in rows if d.baseline == label.baseline and d is not label)
    assert description.text == "777-200LR"  # shortened by meaning, never with an ellipsis
    assert description.right + MIN_SPACE <= label.left
    assert label.right == LAYOUT.flight_list.right
    assert label.width == text_width("LHR → DXB", font(SMALL), TRACKING)
