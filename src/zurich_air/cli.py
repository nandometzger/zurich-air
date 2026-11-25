"""Command-line entry point."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from zurich_air import __version__
from zurich_air.config import Config, ConfigError, load_config
from zurich_air.data import synthetic
from zurich_air.processing.geo import distance_km
from zurich_air.processing.ranking import select_featured
from zurich_air.rendering.renderer import Renderer, Scene


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zurich-air", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    preview = commands.add_parser(
        "preview", help="render synthetic scenes to PNG files (no hardware or network)"
    )
    preview.add_argument(
        "--scene",
        default="all",
        choices=[*synthetic.SCENE_NAMES, "all"],
        help="scene to render (default: all)",
    )
    preview.add_argument("--config", type=Path, help="TOML config file (default: built-in)")
    preview.add_argument("--out", type=Path, help="output directory (default: display.output_dir)")
    preview.add_argument(
        "--scale", type=int, default=1, help="also write an Nx nearest-neighbour enlargement"
    )

    args = parser.parse_args(argv)
    if args.command == "preview":
        return _preview(args, parser)
    parser.print_help()
    return 0


def _preview(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.scale < 1:
        parser.error("--scale must be at least 1")
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        parser.error(str(exc))
    out = args.out or Path(config.display.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    renderer = Renderer(config)
    names = synthetic.SCENE_NAMES if args.scene == "all" else (args.scene,)
    for name in names:
        image = renderer.render(preview_scene(name, config))
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
    lat, lon = config.location.latitude, config.location.longitude
    visible = [
        a
        for a in data.snapshot.aircraft
        if distance_km(lat, lon, a.lat, a.lon) <= config.radar.range_km
        and (config.aircraft.include_ground or not a.on_ground)
    ]
    age_s = (data.now - data.snapshot.timestamp).total_seconds()
    return Scene(
        aircraft=visible,
        featured=select_featured(visible, config),
        updated_at=data.snapshot.timestamp,
        trails=data.trails,
        status="stale" if age_s > 2 * config.update.interval_s else "live",
        now=data.now,
        source_label="SYNTHETIC DATA",
    )
