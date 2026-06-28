#!/usr/bin/env python3
"""Migrate legacy settings.ini to settings.toml."""

from __future__ import annotations

import argparse
from pathlib import Path

from rain_bypass.config import migrate_legacy_ini


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate v1 settings.ini to settings.toml")
    parser.add_argument("input", type=Path, nargs="?", default=Path("settings.ini"))
    parser.add_argument("-o", "--output", type=Path, default=Path("settings.toml"))
    args = parser.parse_args()

    if not args.input.is_file():
        raise SystemExit(f"Input file not found: {args.input}")

    migrate_legacy_ini(args.input, args.output)
    print(f"Wrote {args.output}")
    print("Review latitude/longitude and remove the legacy Google Maps dependency manually.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
