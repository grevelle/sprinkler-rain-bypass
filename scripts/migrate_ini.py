#!/usr/bin/env python3
from pathlib import Path

from rain_bypass.config import migrate_legacy_ini


def main() -> None:
    migrate_legacy_ini(Path("settings.ini"), Path("settings.toml"))
    print("Wrote settings.toml — set latitude and longitude before running.")


if __name__ == "__main__":
    main()
