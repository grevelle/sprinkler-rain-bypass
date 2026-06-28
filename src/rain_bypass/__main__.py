from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rain_bypass.config import load_settings
from rain_bypass.logging_setup import configure_logging
from rain_bypass.runner import RainBypassRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rain-bypass",
        description="Control a sprinkler rain bypass relay from recent precipitation data.",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=Path("settings.toml"),
        help="Path to settings TOML file (default: settings.toml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single evaluation cycle and exit (useful for testing).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    configure_logging(settings.runtime.log_level)
    logger = logging.getLogger(__name__)

    try:
        runner = RainBypassRunner(settings)
        if args.once:
            runner.run_once()
        else:
            runner.run_forever()
    except KeyboardInterrupt:
        logger.info("Stopped by user")
        return 0
    except Exception:
        logger.exception("Rain bypass exited due to an unhandled error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
