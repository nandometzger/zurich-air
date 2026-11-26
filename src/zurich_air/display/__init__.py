"""Display backends: PNG files for development, the e-paper panel on the Pi."""

from __future__ import annotations

from pathlib import Path

from zurich_air.config import DisplayConfig
from zurich_air.display.base import Display
from zurich_air.display.mock import MockDisplay

# The display.backend values this version can drive; "waveshare" arrives with the panel.
AVAILABLE_BACKENDS: tuple[str, ...] = ("mock",)


class DisplayUnavailableError(RuntimeError):
    """The configured display backend cannot be used."""


def create_display(
    config: DisplayConfig, output_dir: str | Path | None = None, *, keep_frames: bool = False
) -> Display:
    """The backend selected by ``display.backend``; *keep_frames* applies to the mock."""
    if config.backend not in AVAILABLE_BACKENDS:
        raise DisplayUnavailableError(
            f'display.backend = "{config.backend}" is not implemented until the panel arrives; '
            'use "mock" for now'
        )
    return MockDisplay(output_dir or config.output_dir, keep_frames=keep_frames)
