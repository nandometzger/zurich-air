"""Command-line entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from zurich_air import __version__, doctor
from zurich_air.app import FETCH_MARGIN_KM, App
from zurich_air.config import Config, ConfigError, load_config
from zurich_air.data import synthetic
from zurich_air.data.aircraft import TrafficSnapshot
from zurich_air.data.metar import Weather
from zurich_air.data.provider import ProviderError, build_provider
from zurich_air.data.readsb import ReadsbError, parse_snapshot
from zurich_air.display import DisplayUnavailableError, create_display
from zurich_air.extras import (
    Extras,
    RouteLookup,
    RouteLookupTable,
    build_metar,
    build_route_table,
    build_runway_monitor,
    check_route,
    verified_routes,
)
from zurich_air.pipeline import build_scene
from zurich_air.processing.filtering import visible_aircraft
from zurich_air.processing.history import TrackHistory
from zurich_air.processing.ranking import Candidate, rank
from zurich_air.processing.route_check import VerifiedRoute
from zurich_air.processing.runways import RunwayUse
from zurich_air.rendering import formatting
from zurich_air.rendering.renderer import Renderer, Scene
from zurich_air.systemd import JournalFormatter, logs_to_journal

log = logging.getLogger(__name__)
LOG_LEVELS = ("debug", "info", "warning", "error")
CONSOLE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
JOURNAL_FORMAT = "%(levelname)s %(name)s: %(message)s"  # journald adds the time itself
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
        help="show why aircraft are (not) featured: scores, phases, routes and reasons",
    )
    _add_config_arg(explain)
    traffic = explain.add_mutually_exclusive_group()
    traffic.add_argument(
        "--scene",
        choices=[*synthetic.SCENE_NAMES, "all"],
        help="synthetic scene to explain instead, as rendered by preview (no network)",
    )
    traffic.add_argument(
        "--snapshot",
        type=Path,
        nargs="+",
        metavar="FILE",
        help="saved readsb JSON response(s), oldest first; the earlier ones only carry the "
        "flight list and routes forward (routes come from the cache only, and there is no "
        "weather). Without this or --scene, live traffic and the METAR are fetched once",
    )

    check = commands.add_parser(
        "doctor",
        parents=[common],
        help="check the setup: config, clock, rendering, display, data sources, service",
        description="Check the setup and print one line per check: OK, WARN, FAIL or SKIP, "
        "with a fix under every problem. Changes nothing. Exits with status 1 if any check "
        "failed; warnings alone exit with 0.",
    )
    _add_config_arg(check)
    check.add_argument(
        "--offline", action="store_true", help="skip the data sources: no network requests"
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
    if args.command == "doctor":
        return _doctor(args)
    parser.print_help()
    return 0


def configure_logging(level: str, env: Mapping[str, str] | None = None) -> logging.Formatter:
    """Log to stderr at *level*; in the systemd journal's style when stderr goes there.

    Does nothing if logging is already set up (as under pytest). Returns the formatter.
    """
    if logs_to_journal(sys.stderr, env):
        formatter: logging.Formatter = JournalFormatter(JOURNAL_FORMAT)
    else:
        formatter = logging.Formatter(CONSOLE_FORMAT)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logging.basicConfig(level=level.upper(), handlers=[handler])
    return formatter


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
    app = App(config, provider, display, extras=Extras.from_config(config))
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
    if args.scene:
        print(explain_scenes(args.scene, config))
        return 0
    weather = None
    if args.snapshot:
        try:
            snapshots = read_snapshots(args.snapshot)
        except (OSError, ValueError) as exc:
            parser.error(f"cannot read snapshot: {exc}")
        table = build_route_table(config, fetch=False)  # saved traffic: no downloads
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
        table = build_route_table(config)
        metar = build_metar(config)
        weather = metar.weather() if metar is not None else None
    explained = explain_ranking(
        snapshots,
        config,
        weather=weather,
        runways=infer_runways(snapshots, config),
        route_lookup=table.lookup if table is not None else None,
    )
    print(explained)
    return 0


def _doctor(args: argparse.Namespace) -> int:
    results = doctor.run_checks(args.config, offline=args.offline)
    print(doctor.report(results))
    return doctor.exit_status(results)


def explain_ranking(
    snapshots: list[TrafficSnapshot],
    config: Config,
    *,
    weather: Weather | None = None,
    runways: RunwayUse | None = None,
    route_lookup: RouteLookup | None = None,
) -> str:
    """A table of every candidate in the last snapshot: rank, score, route and the reasons.

    Earlier snapshots only carry the flight list forward, and the routes verified in them,
    as the live app does. *runways* and *weather*, if known, head the table as the footer
    shows them. With *route_lookup* (callsign -> scheduled route) the ROUTE column shows
    each verified route, and a line under a row says why a scheduled route was rejected.
    """
    extras = Extras(config, route_table=RouteLookupTable(route_lookup)) if route_lookup else None
    previous: list[str] = []
    for snapshot in snapshots[:-1]:
        scene = build_scene(snapshot, None, config, snapshot.timestamp, None, previous)
        previous = [a.icao for a in scene.featured]
        if extras is not None:
            extras.routes(scene.aircraft, scene.featured, snapshot.timestamp)
    snapshot = snapshots[-1]
    aircraft = visible_aircraft(snapshot.aircraft, config)
    candidates = rank(aircraft, config, previous)
    featured = config.aircraft.featured
    if extras is not None:
        listed = [c.aircraft for c in candidates[:featured]]
        extras.routes(aircraft, listed, snapshot.timestamp)

    when = snapshot.timestamp.astimezone(ZoneInfo(config.location.timezone))
    withheld = sum(a.privacy for a in aircraft)
    lines = [
        f"{when:%Y-%m-%d %H:%M:%S} · {snapshot.source} · {len(aircraft)} aircraft on the "
        f"radar, {withheld} withheld for privacy (LADD/PIA)",
        *_airport_lines(weather, runways, config),
        "",
        f"{'#':>3}  {'FLIGHT':<9} {'TYPE':<5} {'ALT':>8} {config.airport.code:>6}  "
        f"{'PHASE':<5}  {'ROUTE':<9}  {'SCORE':>5}  REASONS",
    ]
    for i, c in enumerate(candidates, start=1):
        if i == featured + 1:
            lines.append(f"{'':>3}  {'-' * 40} not featured")
        a = c.aircraft
        altitude = "GND" if a.on_ground else "-" if a.altitude_ft is None else f"{a.altitude_ft} ft"
        phase = {"arrival": "ARR", "departure": "DEP"}.get(c.phase or "", "")
        route, rejected = _explain_route(c, extras, route_lookup, config)
        reasons = ", ".join(str(r) for r in c.reasons if round(r.points))
        lines.append(
            f"{i:>3}  {a.label:<9} {a.type_code or '-':<5} {altitude:>8} "
            f"{c.airport_km:>3.0f} km  {phase:<5}  {route:<9}  {c.score:>5.0f}  {reasons}"
        )
        if rejected:
            lines.append(f"{'':>3}  {'':<9} {rejected}")
    if not candidates:
        lines.append(f"{'':>3}  (no candidates)")
    return "\n".join(lines)


def _airport_lines(weather: Weather | None, runways: RunwayUse | None, config: Config) -> list[str]:
    """The runways in use and the weather, as the footer's airport line shows them."""
    lines = []
    icao = config.airport.icao.upper()
    if runways_text := formatting.SEPARATOR.join(formatting.runways(runways)):
        lines.append(f"runways in use at {icao}: {runways_text}")
    elif runways is not None:
        lines.append(f"runways in use at {icao}: none seen recently")
    if weather is not None:
        observed = weather.observed_at.astimezone(ZoneInfo(config.location.timezone))
        parts = formatting.SEPARATOR.join(formatting.airport_parts(weather, None))
        lines.append(f"weather at {weather.station}, observed {observed:%H:%M}: {parts}")
        if weather.raw:
            lines.append(f"  {weather.raw}")
    return lines


def _explain_route(
    candidate: Candidate,
    extras: Extras | None,
    route_lookup: RouteLookup | None,
    config: Config,
) -> tuple[str, str | None]:
    """The ROUTE column for *candidate*, and why its scheduled route was rejected, if so.

    A route verified in this or an earlier snapshot is shown, as on the display; for the
    others, the scheduled route is checked once more to say why it does not fit.
    """
    a = candidate.aircraft
    if extras is None or route_lookup is None or a.privacy:
        return "-", None
    remembered = extras.memory.recall(a)
    if remembered is not None:
        return formatting.route_label(remembered), None
    schedule = route_lookup(a.callsign)
    reasons: list[str] = []
    verified: VerifiedRoute | None = check_route(a, schedule, config, candidate.phase, reasons)
    if verified is not None:
        return formatting.route_label(verified), None
    if schedule is None:
        return "-", None
    return "-", f"route {'-'.join(schedule.airports)} not shown: {'; '.join(reasons)}"


def infer_runways(snapshots: list[TrafficSnapshot], config: Config) -> RunwayUse | None:
    """The runways seen in use over *snapshots* (oldest first), as the live app infers them;
    None if ``airport.runways_in_use`` is off or the airport is not in the bundled data."""
    monitor = build_runway_monitor(config)
    if monitor is None:
        return None
    history = TrackHistory()
    use = RunwayUse()
    for snapshot in snapshots:
        history.update(snapshot)
        use = monitor.update(snapshot.aircraft, snapshot.timestamp, history)
    return use


def explain_scenes(name: str, config: Config) -> str:
    """:func:`explain_ranking` for the synthetic scene *name*, or for every scene ("all"),
    with the scene's weather, runways in use and routes.

    The header names the source as ``synthetic data``, as the footer of a preview does;
    with every scene, a ``== name ==`` line heads each one.
    """
    names = synthetic.SCENE_NAMES if name == "all" else (name,)
    explained = []
    for scene_name in names:
        data = synthetic.scene(scene_name)
        text = explain_ranking(
            [data.snapshot],
            config,
            weather=data.weather,
            runways=data.runways,
            route_lookup=data.route,
        )
        explained.append(f"== {scene_name} ==\n{text}" if name == "all" else text)
    return "\n\n".join(explained)


def preview_scene(name: str, config: Config) -> Scene:
    """A renderable scene built from the synthetic scene *name*: its traffic, weather and
    runways in use, and those of its routes that the real check verifies."""
    data = synthetic.scene(name)
    history = TrackHistory()
    step = timedelta(seconds=synthetic.TRAIL_INTERVAL_S)
    for icao, points in data.trails.items():
        for age, (lat, lon) in zip(range(len(points), 0, -1), points, strict=True):
            history.add(icao, lat, lon, data.snapshot.timestamp - age * step)
    history.update(data.snapshot)
    scene = build_scene(
        data.snapshot, history, config, now=data.now, weather=data.weather, runways=data.runways
    )
    return dataclasses.replace(scene, routes=verified_routes(scene.featured, data.route, config))


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
