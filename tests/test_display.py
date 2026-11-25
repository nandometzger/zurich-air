from PIL import Image

from zurich_air.display.base import Display
from zurich_air.display.mock import MockDisplay


def test_mock_display_writes_numbered_frames_and_latest(tmp_path):
    display: Display = MockDisplay(tmp_path / "out")
    image = Image.new("1", (400, 300), 255)
    display.show(image)
    display.show(image)
    display.close()
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "frame-0001.png",
        "frame-0002.png",
        "latest.png",
    ]
    with Image.open(tmp_path / "out" / "latest.png") as saved:
        assert saved.size == (400, 300)
