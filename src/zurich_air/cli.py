"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image

from zurich_air import __version__
from zurich_air.app import App
from zurich_air.config import Config, ConfigError, load_config
from zurich_air.data import synthetic
from zurich_air.data.provider import build_provider
from zurich_air.data.readsb import ReadsbError, parse_snapshot
from zurich_air.display import DisplayUnavailableError, create_display
from zurich_air.pipeline import build_scene
from zurich_air.processing.history import TrackHistory
from zurich_air.rendering.renderer import Renderer, Scene

log = logging.getLogger(__name__)
LOG_LEVELS = ("debug", "info", "warning", "error")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zurich-air", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--log-level", default="info", choices=LOG_LEVELS, help="logging detail (default: info)"
    )
    commands = parser.add_subparsers(dest="command")

    preview = commands.add_parser(
        "preview",
        help="render synthetic scenes or saved readsb JSON to PNG files (no hardware or network)",
    )
    source = preview.add_mutually_exclusive_group()
    source.add_argument(
        "--scene",
        default="all",
        choices=[*synthetic.SCENE_NAMES, "all"],
        help="synthetic scene to render (default: all)",
    )
    source.add_argument(
        "--snapshot",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="saved readsb JSON response(s), oldest first; the last one is rendered and "
        "the earlier ones only build up the trails",
    )
    _add_config_arg(preview)
    preview.add_argument("--out", type=Path, help="output directory (default: display.output_dir)")
    preview.add_argument(
        "--scale", type=int, default=1, help="also write an Nx nearest-neighbour enlargement"
    )

    once = commands.add_parser("once", help="fetch live traffic once and update the display")
    _add_config_arg(once)
    once.add_argument("--out", type=Path, help="mock display output directory")
    once.add_argument(
        "--save-json", type=Path, metavar="FILE", help="also save the raw provider response"
    )

    run = commands.add_parser("run", help="update the display continuously until stopped")
    _add_config_arg(run)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if args.command == "preview":
        return _preview(args, parser)
    if args.command == "once":
        return _live(args, parser, once=True)
    if args.command == "run":
        return _live(args, parser, once=False)
    parser.print_help()
    return 0


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="TOML config file (default: built-in)")


def _load(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    try:
        return load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))


def _live(args: argparse.Namespace, parser: argparse.ArgumentParser, *, once: bool) -> int:
    config = _load(args, parser)
    try:
        display = create_display(config.display, getattr(args, "out", None))
    except DisplayUnavailableError as exc:
        print(f"zurich-air: error: {exc}", file=sys.stderr)
        return 2
    provider = build_provider(config.provider)
    app = App(config, provider, display)
    if not once:
        app.run()
        return 0
    try:
        fresh = app.tick()
    finally:
        app.close()
    if fresh and args.save_json and provider.last_source and provider.last_source.last_body:
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_json.write_bytes(provider.last_source.last_body)
        print(args.save_json)
    return 0 if fresh else 1


def _preview(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.scale < 1:
        parser.error("--scale must be at least 1")
    config = _load(args, parser)
    out = args.out or Path(config.display.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderer = Renderer(config)
    if args.snapshot:
        try:
            scenes = {args.snapshot[-1].stem: snapshot_scene(args.snapshot, config)}
        except (OSError, ValueError) as exc:
            parser.error(f"cannot read snapshot: {exc}")
    else:
        names = synthetic.SCENE_NAMES if args.scene == "all" else (args.scene,)
        scenes = {name: preview_scene(name, config) for name in names}
    for name, scene in scenes.items():
        image = renderer.render(scene)
        path = out / f"{name}.png"
        image.save(path)
        print(path)
        if args.scale > 1:
            size = (image.width * args.scale, image.height * args.scale)
            scaled = out / f"{name}@{args.scale}x.png"
            image.resize(size, Image.Resampling.NEAREST).save(scaled)
            print(scaled)
    return 0


def preview_scene(name: str, config: Config) -> Scene:
    """A renderable scene built from the synthetic traffic *name*."""
    data = synthetic.scene(name)
    history = TrackHistory()
    step = timedelta(seconds=synthetic.TRAIL_INTERVAL_S)
    for icao, points in data.trails.items():
        for age, (lat, lon) in zip(range(len(points), 0, -1), points, strict=True):
            history.add(icao, lat, lon, data.snapshot.timestamp - age * step)
    history.update(data.snapshot)
    return build_scene(data.snapshot, history, config, now=data.now)


def snapshot_scene(paths: list[Path], config: Config) -> Scene:
    """A scene from saved readsb responses, rendered as if live when the last was taken."""
    history = TrackHistory()
    snapshot = None
    for path in paths:
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        try:
            payload = json.loads(path.read_bytes())
            snapshot = parse_snapshot(payload, source="snapshot", fetched_at=fetched_at)
        except ValueError as exc:  # also JSON and Unicode decoding errors
            raise ReadsbError(f"{path}: {exc}") from exc
        history.update(snapshot)
    assert snapshot is not None
    return build_scene(snapshot, history, config, now=snapshot.timestamp)
