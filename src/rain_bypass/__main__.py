from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rain_bypass.app import run
from rain_bypass.config import load_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprinkler rain bypass controller")
    parser.add_argument("-c", "--config", type=Path, default=Path("settings.toml"))
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    logging.basicConfig(
        level=getattr(logging, settings.runtime.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    try:
        run(settings, once=args.once)
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("stopped")
        return 0
    except Exception:
        logging.getLogger(__name__).exception("fatal error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
