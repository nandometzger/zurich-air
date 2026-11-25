import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from zurich_air import cli
from zurich_air.cli import main, snapshot_scene
from zurich_air.config import Config
from zurich_air.data.provider import ProviderError
from zurich_air.data.readsb import parse_snapshot


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
    out = capsys.readouterr().out
    assert "preview" in out and "once" in out and "run" in out


FIXTURE = Path(__file__).parent / "fixtures" / "readsb_small.json"


def test_preview_snapshot_renders_saved_response(tmp_path):
    assert main(["preview", "--snapshot", str(FIXTURE), "--out", str(tmp_path)]) == 0
    with Image.open(tmp_path / "readsb_small.png") as image:
        assert image.size == (400, 300)


def test_preview_snapshot_sequence_builds_trails(tmp_path):
    later = json.loads(FIXTURE.read_text())
    later["now"] += 60_000
    for entry in later["ac"]:
        entry["lat"] += 0.02
    second = tmp_path / "later.json"
    second.write_text(json.dumps(later))
    single = snapshot_scene([second], Config())
    sequence = snapshot_scene([FIXTURE, second], Config())
    assert single.trails == {}
    assert set(sequence.trails) == {"4b0001", "4b0002", "~2b0004", "4b0005"}
    assert main(["preview", "--snapshot", str(FIXTURE), str(second), "--out", str(tmp_path)]) == 0
    assert (tmp_path / "later.png").exists()


@pytest.mark.parametrize("content", ["{not json", '{"msg": "error"}', "[]"])
def test_preview_snapshot_rejects_bad_files(tmp_path, content):
    bad = tmp_path / "bad.json"
    bad.write_text(content)
    with pytest.raises(SystemExit) as exc:
        main(["preview", "--snapshot", str(bad), "--out", str(tmp_path)])
    assert exc.value.code == 2


class FakeSource:
    label = "adsb.fi"
    last_body = FIXTURE.read_bytes()


class FakeProvider:
    def __init__(self, fail=False):
        self.fail = fail
        self.last_source = None

    def fetch(self, lat, lon, radius_km):
        if self.fail:
            raise ProviderError("adsb.fi: timed out")
        self.last_source = FakeSource()
        return parse_snapshot(
            json.loads(FakeSource.last_body), source="adsb.fi", fetched_at=datetime.now(UTC)
        )


def test_once_shows_a_frame_and_saves_the_response(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider())
    saved = tmp_path / "raw" / "snap.json"
    assert main(["once", "--out", str(tmp_path / "out"), "--save-json", str(saved)]) == 0
    assert (tmp_path / "out" / "latest.png").exists()
    assert saved.read_bytes() == FIXTURE.read_bytes()


def test_once_fails_when_no_source_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider(fail=True))
    saved = tmp_path / "snap.json"
    assert main(["once", "--out", str(tmp_path), "--save-json", str(saved)]) == 1
    assert not saved.exists()


def test_waveshare_backend_not_available_yet(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider())
    config = tmp_path / "config.toml"
    config.write_text('[display]\nbackend = "waveshare"\n')
    assert main(["once", "--config", str(config)]) == 2
    assert "not implemented until the panel arrives" in capsys.readouterr().err
    assert main(["run", "--config", str(config)]) == 2


def test_log_level_option(tmp_path):
    assert (
        main(["--log-level", "debug", "preview", "--scene", "empty", "--out", str(tmp_path)]) == 0
    )
    with pytest.raises(SystemExit):
        main(["--log-level", "loud", "preview"])
