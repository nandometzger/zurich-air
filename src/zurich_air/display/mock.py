"""A display that writes PNG files instead of driving hardware."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


class MockDisplay:
    """Saves every frame as ``frame-0001.png``, ``frame-0002.png``, … plus ``latest.png``."""

    def __init__(self, output_dir: str | Path, prefix: str = "frame") -> None:
        self.output_dir = Path(output_dir)
        self.prefix = prefix
        self.frames = 0

    def show(self, image: Image.Image) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.frames += 1
        path = self.output_dir / f"{self.prefix}-{self.frames:04d}.png"
        image.save(path)
        image.save(self.output_dir / "latest.png")
        return path

    def close(self) -> None:
        pass
