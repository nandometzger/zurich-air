import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from zurich_air import cli
from zurich_air.cli import configure_logging, main, snapshot_scene
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
    assert "preview" in out and "once" in out and "run" in out and "explain" in out


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


def test_preview_snapshot_source_label(tmp_path):
    assert snapshot_scene([FIXTURE], Config()).source_label == "SNAPSHOT"
    assert snapshot_scene([FIXTURE], Config(), "adsb.fi").source_label == "ADSB.FI"
    args = ["preview", "--snapshot", str(FIXTURE), "--out", str(tmp_path)]
    assert main([*args, "--source-label", "adsb.fi"]) == 0
    with Image.open(tmp_path / "readsb_small.png") as labelled:
        labelled = labelled.copy()
    assert main(args) == 0
    with Image.open(tmp_path / "readsb_small.png") as default:
        assert labelled.tobytes() != default.tobytes()


def test_source_label_needs_a_snapshot(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["preview", "--scene", "quiet", "--source-label", "X", "--out", str(tmp_path)])
    assert exc.value.code == 2


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


def test_mock_display_keeps_frames_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider())
    assert main(["once", "--out", str(tmp_path / "plain")]) == 0
    assert [p.name for p in (tmp_path / "plain").iterdir()] == ["latest.png"]
    assert main(["once", "--out", str(tmp_path / "dev"), "--keep-frames"]) == 0
    assert sorted(p.name for p in (tmp_path / "dev").iterdir()) == ["frame-0001.png", "latest.png"]


def test_run_exits_with_the_loop_status(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider())
    monkeypatch.setattr(cli.App, "run", lambda self: 1)  # e.g. the display kept failing
    assert main(["run", "--out", str(tmp_path)]) == 1


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


@pytest.mark.parametrize(
    ("args", "level"),
    [
        ([], "INFO"),
        (["--log-level", "debug"], "DEBUG"),  # before the command
        (["preview", "--log-level", "warning"], "WARNING"),  # or after it
        (["--log-level", "debug", "preview", "--log-level", "error"], "ERROR"),
    ],
)
def test_log_level_option(tmp_path, monkeypatch, args, level):
    calls = []
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kwargs: calls.append(kwargs))
    if "preview" not in args:
        args = [*args, "preview"]
    assert main([*args, "--scene", "empty", "--out", str(tmp_path)]) == 0
    assert calls[0]["level"] == level


@pytest.mark.parametrize("args", [["--log-level", "loud", "preview"], ["run", "--log-level", "x"]])
def test_log_level_rejects_unknown_levels(args):
    with pytest.raises(SystemExit):
        main(args)


def test_logs_without_time_under_journald(tmp_path, monkeypatch):
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **kwargs: None)
    record = logging.LogRecord("zurich_air.app", logging.WARNING, __file__, 1, "hi", None, None)
    with (tmp_path / "stderr").open("w") as stderr:
        monkeypatch.setattr(sys, "stderr", stderr)
        stat = os.fstat(stderr.fileno())
        journal = {"JOURNAL_STREAM": f"{stat.st_dev}:{stat.st_ino}"}
        assert configure_logging("info", journal).format(record) == "<4>WARNING zurich_air.app: hi"
        console = configure_logging("info", {}).format(record)
    assert console.endswith(" WARNING zurich_air.app: hi")
    assert console[:4].isdigit()  # starts with the date and time


def test_explain_snapshot_prints_ranked_table(capsys):
    assert main(["explain", "--snapshot", str(FIXTURE)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "aircraft on the radar" in lines[0]
    header = next(line for line in lines if "REASONS" in line)
    assert all(column in header for column in ("FLIGHT", "TYPE", "ALT", "ZRH", "PHASE", "SCORE"))
    first = lines[lines.index(header) + 1]
    assert first.split()[:3] == ["1", "SWR001", "BCS3"]
    assert "ARR" in first and "arrival ZRH" in first
    assert not any("SWR003" in line for line in lines)  # on the ground


def test_explain_withholds_private_aircraft(tmp_path, capsys):
    payload = json.loads(FIXTURE.read_text())
    payload["ac"][0]["dbFlags"] = 8  # LADD
    path = tmp_path / "private.json"
    path.write_text(json.dumps(payload))
    assert main(["explain", "--snapshot", str(path)]) == 0
    out = capsys.readouterr().out
    assert "1 withheld for privacy" in out
    assert "SWR001" not in out


def test_explain_snapshot_sequence_carries_the_flight_list(tmp_path, capsys):
    later = json.loads(FIXTURE.read_text())
    later["now"] += 60_000
    second = tmp_path / "later.json"
    second.write_text(json.dumps(later))
    assert main(["explain", "--snapshot", str(FIXTURE), str(second)]) == 0
    assert "featured before +15" in capsys.readouterr().out


def test_explain_marks_the_end_of_the_flight_list(tmp_path, capsys):
    config = tmp_path / "config.toml"
    config.write_text("[aircraft]\nfeatured = 1\n")
    assert main(["explain", "--config", str(config), "--snapshot", str(FIXTURE)]) == 0
    lines = capsys.readouterr().out.splitlines()
    marker = next(i for i, line in enumerate(lines) if "not featured" in line)
    assert lines[marker - 1].split()[0] == "1"
    assert lines[marker + 1].split()[0] == "2"


def test_explain_fetches_live_traffic_once(monkeypatch, capsys):
    provider = FakeProvider()
    monkeypatch.setattr(cli, "build_provider", lambda config: provider)
    assert main(["explain"]) == 0
    out = capsys.readouterr().out
    assert "adsb.fi" in out and "SWR001" in out


def test_explain_fails_when_no_source_answers(monkeypatch, capsys):
    monkeypatch.setattr(cli, "build_provider", lambda config: FakeProvider(fail=True))
    assert main(["explain"]) == 1
    assert "timed out" in capsys.readouterr().err


def test_explain_rejects_bad_snapshot(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        main(["explain", "--snapshot", str(bad)])
    assert exc.value.code == 2
