from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from rain_bypass.config import load_settings
from rain_bypass.controller import run
from rain_bypass.logging_setup import configure_logging

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
    help="Sprinkler rain bypass controller",
)


@app.callback(invoke_without_command=True)
def main(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Path to settings.toml"),
    ] = Path("settings.toml"),
    once: Annotated[bool, typer.Option("--once", help="Run one cycle and exit")] = False,
) -> None:
    settings = load_settings(config)
    configure_logging(settings.runtime.log_level)
    logger = logging.getLogger(__name__)
    try:
        run(settings, once=once)
    except KeyboardInterrupt:
        logger.info("stopped")
        raise typer.Exit(0) from None
    except Exception:
        logger.exception("fatal error")
        raise typer.Exit(1) from None
