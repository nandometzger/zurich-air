from pathlib import Path

import pytest
from PIL import Image

from zurich_air.display.base import Display
from zurich_air.display.mock import MockDisplay

WHITE = Image.new("1", (400, 300), 255)
BLACK = Image.new("1", (400, 300), 0)


def names(path):
    return sorted(p.name for p in path.iterdir())


def test_mock_display_keeps_only_the_latest_frame(tmp_path):
    display: Display = MockDisplay(tmp_path / "out")
    display.show(WHITE)
    display.show(BLACK)
    display.close()
    assert names(tmp_path / "out") == ["latest.png"]  # no frame files, no leftovers
    with Image.open(tmp_path / "out" / "latest.png") as saved:
        assert saved.size == (400, 300)
        assert saved.convert("1").tobytes() == BLACK.tobytes()


def test_mock_display_keeps_numbered_frames_when_asked(tmp_path):
    display = MockDisplay(tmp_path, keep_frames=True)
    assert display.show(WHITE) == tmp_path / "frame-0001.png"
    display.show(WHITE)
    assert names(tmp_path) == ["frame-0001.png", "frame-0002.png", "latest.png"]


def test_latest_is_replaced_not_rewritten(tmp_path):
    display = MockDisplay(tmp_path)
    display.show(WHITE)
    before = (tmp_path / "latest.png").stat().st_ino
    display.show(BLACK)
    # A new file took its place (os.replace): a reader never sees a half-written PNG.
    assert (tmp_path / "latest.png").stat().st_ino != before


def test_failed_write_keeps_the_previous_frame(tmp_path, monkeypatch):
    display = MockDisplay(tmp_path)
    display.show(WHITE)

    def broken_save(self, fp, format=None, **params):
        Path(fp).write_bytes(b"\x89PNG half")
        raise OSError("No space left on device")

    monkeypatch.setattr(Image.Image, "save", broken_save)
    with pytest.raises(OSError):
        display.show(BLACK)
    monkeypatch.undo()
    assert names(tmp_path) == ["latest.png"]  # the partial file is gone
    with Image.open(tmp_path / "latest.png") as saved:
        assert saved.convert("1").tobytes() == WHITE.tobytes()
