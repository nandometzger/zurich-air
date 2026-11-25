from PIL import Image

from zurich_air.cli import main


def test_preview_writes_native_and_scaled_png(tmp_path):
    assert main(["preview", "--scene", "quiet", "--out", str(tmp_path), "--scale", "2"]) == 0
    with Image.open(tmp_path / "quiet.png") as native:
        assert native.size == (400, 300)
    with Image.open(tmp_path / "quiet@2x.png") as scaled:
        assert scaled.size == (800, 600)


def test_preview_all_scenes(tmp_path):
    assert main(["preview", "--out", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*.png"))) == 6


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "preview" in capsys.readouterr().out
