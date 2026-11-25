"""A display that writes PNG files instead of driving hardware."""

from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

LATEST = "latest.png"


class MockDisplay:
    """Writes the frame on screen to ``latest.png``, replacing it atomically so a reader
    never sees half a file. With *keep_frames* (for development) every frame is also kept
    as ``frame-0001.png``, ``frame-0002.png``, …; a long-running service would otherwise
    fill the SD card at some 6 MB a day."""

    def __init__(
        self, output_dir: str | Path, *, keep_frames: bool = False, prefix: str = "frame"
    ) -> None:
        self.output_dir = Path(output_dir)
        self.keep_frames = keep_frames
        self.prefix = prefix
        self.frames = 0

    def show(self, image: Image.Image) -> Path:
        """Save *image*; returns the path of the file written for it."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames += 1
        latest = self.output_dir / LATEST
        partial = self.output_dir / f".{LATEST}.{os.getpid()}.tmp"
        try:
            image.save(partial, format="PNG")
            partial.replace(latest)  # os.replace: atomic on POSIX
        finally:
            partial.unlink(missing_ok=True)
        if not self.keep_frames:
            return latest
        path = self.output_dir / f"{self.prefix}-{self.frames:04d}.png"
        image.save(path)
        return path

    def close(self) -> None:
        pass
