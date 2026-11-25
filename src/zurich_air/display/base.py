"""The interface every display backend implements."""

from __future__ import annotations

from typing import Protocol

from PIL import Image


class Display(Protocol):
    def show(self, image: Image.Image) -> None:
        """Put a full frame (mode "1", display-sized) on the screen."""

    def close(self) -> None:
        """Release the hardware; the display keeps showing the last frame."""
