"""Compose the full 1-bit screen: header, radar, flight list and footer.

The footer holds the airport line on the left (``LDG 14 · DEP 28 · 270° 8 KT G18 · 4 °C ·
QNH 1018``), shortened from its end when it does not fit, and the data source, or since
when no data has arrived, on the right.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from PIL import Image, ImageDraw

from zurich_air.config import Config
from zurich_air.data.aircraft import Aircraft
from zurich_air.data.metar import Weather
from zurich_air.processing.phase import Phase
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse
from zurich_air.rendering import formatting
from zurich_air.rendering.flight_list import draw_flight_list
from zurich_air.rendering.fonts import Face, cap_height, draw_text, fit_parts, font
from zurich_air.rendering.layout import FOOTER_GAP, SMALL, TITLE, TRACKING, Layout
from zurich_air.rendering.radar import draw_radar
from zurich_air.rendering.symbols import BLACK, WHITE, draw_dot


@dataclass(frozen=True)
class Scene:
    """Everything one frame shows. The renderer draws exactly this and invents nothing."""

    aircraft: Sequence[Aircraft]  # plotted on the radar
    featured: Sequence[Aircraft]  # flight list, best first; numbered 1..n on the radar
    updated_at: datetime | None  # timezone-aware time the traffic data refers to, if any
    trails: Mapping[str, Sequence[tuple[float, float]]] = field(default_factory=dict)
    # "stale": old data, still drawn; "waiting": no usable data, an empty radar
    status: Literal["live", "stale", "waiting"] = "live"
    now: datetime | None = None  # clock in the header; defaults to updated_at
    source_label: str | None = None  # small data attribution in the footer, e.g. "ADSB.FI"
    phases: Mapping[str, Phase] = field(default_factory=dict)  # featured ICAO -> phase, if known
    # The airport line in the footer; each part may be missing.
    weather: Weather | None = None  # the airport's latest METAR, if recent enough
    runways: RunwayUse | None = None  # runways seen in use recently, if inferred
    routes: Mapping[str, VerifiedRoute] = field(default_factory=dict)  # featured ICAO -> route


class Renderer:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.layout = Layout.for_size(config.display.width, config.display.height)

    def render(self, scene: Scene) -> Image.Image:
        """Draw *scene* onto a new black-and-white image of the display's size."""
        image = Image.new("1", (self.layout.width, self.layout.height), WHITE)
        draw = ImageDraw.Draw(image)
        waiting = scene.status == "waiting"
        self._draw_header(draw, scene)
        draw_radar(
            image, self.layout, self.config, scene.aircraft, scene.featured, scene.trails,
            count=None if waiting else len(scene.aircraft),  # without data it would be a guess
        )  # fmt: skip
        draw_flight_list(
            image,
            self.layout.flight_list,
            scene.featured,
            self.config,
            scene.phases,
            routes=scene.routes,
            waiting=waiting,
        )
        self._draw_footer(draw, scene)
        return image

    def _draw_header(self, draw: ImageDraw.ImageDraw, scene: Scene) -> None:
        lay = self.layout
        fnt = font(TITLE, bold=True)
        title = f"{self.config.location.name.upper()}{formatting.SEPARATOR}LIVE AIR TRAFFIC"
        draw_text(draw, lay.margin, lay.header_baseline, title, fnt, tracking=TRACKING)

        right = lay.width - lay.margin
        when = scene.now or scene.updated_at
        if when is not None:
            clock = formatting.clock(when, self.config.location.timezone)
            width = draw_text(
                draw, right, lay.header_baseline, clock, fnt, tracking=TRACKING, align="right"
            )
            radius = 3
            cx = right - width - 6 - radius
            cy = lay.header_baseline - cap_height(fnt) // 2
            draw_dot(draw, cx, cy, radius, filled=scene.status == "live")
        draw.line((lay.margin, lay.header_rule_y, right - 1, lay.header_rule_y), fill=BLACK)

    def _draw_footer(self, draw: ImageDraw.ImageDraw, scene: Scene) -> None:
        lay = self.layout
        right = lay.width - lay.margin
        draw.line((lay.margin, lay.footer_rule_y, right - 1, lay.footer_rule_y), fill=BLACK)
        status = self._footer_status(scene)
        if status is not None:
            text, fnt = status
            width = draw_text(
                draw, right, lay.footer_baseline, text, fnt, tracking=TRACKING, align="right"
            )
            right -= width + FOOTER_GAP
        regular = font(SMALL)
        parts = formatting.airport_parts(scene.weather, scene.runways)
        line = fit_parts(parts, regular, right - lay.margin, TRACKING, formatting.SEPARATOR)
        if line:
            draw_text(draw, lay.margin, lay.footer_baseline, line, regular, tracking=TRACKING)

    def _footer_status(self, scene: Scene) -> tuple[str, Face] | None:
        """The data source, or since when no data has arrived, and its face."""
        if scene.status != "live":
            text = "NO DATA"
            if scene.updated_at is not None:
                text += (
                    f" SINCE {formatting.clock(scene.updated_at, self.config.location.timezone)}"
                )
            return text, font(SMALL, bold=True)
        if scene.source_label:
            return scene.source_label.upper(), font(SMALL)
        return None
