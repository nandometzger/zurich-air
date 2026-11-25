"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from zurich_air import __version__
from zurich_air.app import FETCH_MARGIN_KM, App
from zurich_air.config import Config, ConfigError, load_config
from zurich_air.data import synthetic
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.data.provider import ProviderError, build_provider
from zurich_air.data.readsb import ReadsbError, parse_snapshot
from zurich_air.display import DisplayUnavailableError, create_display
from zurich_air.pipeline import build_scene
from zurich_air.processing.filtering import visible_aircraft
from zurich_air.processing.history import TrackHistory
from zurich_air.processing.ranking import rank
from zurich_air.rendering.renderer import Renderer, Scene

log = logging.getLogger(__name__)
LOG_LEVELS = ("debug", "info", "warning", "error")
CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
SNAPSHOT_LABEL = "SNAPSHOT"  # footer attribution of saved responses, unless told otherwise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zurich-air", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    log_help = "logging detail (default: info)"
    parser.add_argument("--log-level", default="info", choices=LOG_LEVELS, help=log_help)
    # Also accepted after the command (`zurich-air run --log-level debug`).
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--log-level", default=argparse.SUPPRESS, choices=LOG_LEVELS, help=log_help)
    commands = parser.add_subparsers(dest="command")

    preview = commands.add_parser(
        "preview",
        parents=[common],
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
    preview.add_argument(
        "--source-label",
        metavar="TEXT",
        help="data attribution in the footer of a --snapshot render, e.g. ADSB.FI "
        f"(default: {SNAPSHOT_LABEL})",
    )
    _add_config_arg(preview)
    preview.add_argument("--out", type=Path, help="output directory (default: display.output_dir)")
    preview.add_argument(
        "--scale", type=int, default=1, help="also write an Nx nearest-neighbour enlargement"
    )

    once = commands.add_parser(
        "once", parents=[common], help="fetch live traffic once and update the display"
    )
    _add_config_arg(once)
    _add_mock_args(once)
    once.add_argument(
        "--save-json", type=Path, metavar="FILE", help="also save the raw provider response"
    )

    run = commands.add_parser(
        "run", parents=[common], help="update the display continuously until stopped"
    )
    _add_config_arg(run)
    _add_mock_args(run)

    explain = commands.add_parser(
        "explain",
        parents=[common],
        help="show why aircraft are (not) featured: scores, phases and reasons",
    )
    _add_config_arg(explain)
    explain.add_argument(
        "--snapshot",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="saved readsb JSON response(s), oldest first; the earlier ones only carry the "
        "flight list forward. Without this, live traffic is fetched once",
    )

    args = parser.parse_args(argv)
    configure_logging(args.log_level)
    if args.command == "preview":
        return _preview(args, parser)
    if args.command == "once":
        return _live(args, parser, once=True)
    if args.command == "run":
        return _live(args, parser, once=False)
    if args.command == "explain":
        return _explain(args, parser)
    parser.print_help()
    return 0


def configure_logging(level: str) -> None:
    """Log to stderr at *level*; does nothing if logging is already set up (as under pytest)."""
    logging.basicConfig(level=level.upper(), format=CONSOLE_FORMAT)


def _add_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, help="TOML config file (default: built-in)")


def _add_mock_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--out", type=Path, help="mock display output directory (default: display.output_dir)"
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="mock display: keep every frame as frame-NNNN.png, not only latest.png",
    )


def _load(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Config:
    try:
        return load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))


def _live(args: argparse.Namespace, parser: argparse.ArgumentParser, *, once: bool) -> int:
    config = _load(args, parser)
    try:
        display = create_display(config.display, args.out, keep_frames=args.keep_frames)
    except DisplayUnavailableError as exc:
        print(f"zurich-air: error: {exc}", file=sys.stderr)
        return 2
    provider = build_provider(config.provider)
    app = App(config, provider, display)
    if not once:
        return app.run()
    try:
        report = app.tick()
    finally:
        app.close()
    last = provider.last_source
    if report.fresh and args.save_json and last and last.last_body:
        args.save_json.parent.mkdir(parents=True, exist_ok=True)
        args.save_json.write_bytes(last.last_body)
        print(args.save_json)
    return 0 if report.fresh and not report.errors else 1


def _preview(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.scale < 1:
        parser.error("--scale must be at least 1")
    if args.source_label is not None and not args.snapshot:
        parser.error("--source-label only applies to --snapshot renders")
    config = _load(args, parser)
    out = args.out or Path(config.display.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderer = Renderer(config)
    if args.snapshot:
        try:
            label = args.source_label or SNAPSHOT_LABEL
            scenes = {args.snapshot[-1].stem: snapshot_scene(args.snapshot, config, label)}
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


def _explain(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _load(args, parser)
    if args.snapshot:
        try:
            snapshots = read_snapshots(args.snapshot)
        except (OSError, ValueError) as exc:
            parser.error(f"cannot read snapshot: {exc}")
    else:
        centre = config.location
        radius_km = config.radar.range_km + FETCH_MARGIN_KM
        try:
            fetched = build_provider(config.provider).fetch(
                centre.latitude, centre.longitude, radius_km
            )
        except ProviderError as exc:
            print(f"zurich-air: error: {exc}", file=sys.stderr)
            return 1
        snapshots = [fetched]
    print(explain_ranking(snapshots, config))
    return 0


def explain_ranking(snapshots: list[TrafficSnapshot], config: Config) -> str:
    """A table of every candidate in the last snapshot: rank, score and the reasons.

    Earlier snapshots only carry the flight list forward, as the live app does.
    """
    previous: list[str] = []
    for snapshot in snapshots[:-1]:
        scene = build_scene(snapshot, None, config, snapshot.timestamp, None, previous)
        previous = [a.icao for a in scene.featured]
    snapshot = snapshots[-1]
    aircraft = visible_aircraft(snapshot.aircraft, config)
    candidates = rank(aircraft, config, previous)

    when = snapshot.timestamp.astimezone(ZoneInfo(config.location.timezone))
    withheld = sum(a.privacy for a in aircraft)
    lines = [
        f"{when:%Y-%m-%d %H:%M:%S} · {snapshot.source} · {len(aircraft)} aircraft on the "
        f"radar, {withheld} withheld for privacy (LADD/PIA)",
        "",
        f"{'#':>3}  {'FLIGHT':<9} {'TYPE':<5} {'ALT':>8} {config.airport.code:>6}  "
        f"{'PHASE':<5}  {'SCORE':>5}  REASONS",
    ]
    featured = config.aircraft.featured
    for i, c in enumerate(candidates, start=1):
        if i == featured + 1:
            lines.append(f"{'':>3}  {'-' * 40} not featured")
        a = c.aircraft
        altitude = "GND" if a.on_ground else "-" if a.altitude_ft is None else f"{a.altitude_ft} ft"
        phase = {"arrival": "ARR", "departure": "DEP"}.get(c.phase or "", "")
        reasons = ", ".join(str(r) for r in c.reasons if round(r.points))
        lines.append(
            f"{i:>3}  {a.label:<9} {a.type_code or '-':<5} {altitude:>8} "
            f"{c.airport_km:>3.0f} km  {phase:<5}  {c.score:>5.0f}  {reasons}"
        )
    if not candidates:
        lines.append(f"{'':>3}  (no candidates)")
    return "\n".join(lines)


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


def snapshot_scene(paths: list[Path], config: Config, source: str = SNAPSHOT_LABEL) -> Scene:
    """A scene from saved readsb responses, rendered as if live when the last was taken.

    *source* is the attribution shown in the footer.
    """
    history = TrackHistory()
    scene = None
    for snapshot in read_snapshots(paths, source):
        history.update(snapshot)
        previous = [a.icao for a in scene.featured] if scene else ()
        scene = build_scene(snapshot, history, config, snapshot.timestamp, None, previous)
    assert scene is not None
    return scene


def read_snapshots(paths: list[Path], source: str = SNAPSHOT_LABEL) -> list[TrafficSnapshot]:
    """Saved readsb responses, each timed by its file's modification time as a fallback."""
    snapshots = []
    for path in paths:
        fetched_at = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        try:
            payload = json.loads(path.read_bytes())
            snapshots.append(parse_snapshot(payload, source=source, fetched_at=fetched_at))
        except ValueError as exc:  # also JSON and Unicode decoding errors
            raise ReadsbError(f"{path}: {exc}") from exc
    return snapshots
