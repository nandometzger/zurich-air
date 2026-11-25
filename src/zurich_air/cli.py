"""Command-line entry point."""

from __future__ import annotations

import argparse

from zurich_air import __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="zurich-air", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
