from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from rain_bypass.config import ConfigError, load_settings
from rain_bypass.controller import run
from rain_bypass.logging_setup import configure_logging
from rain_bypass.status import print_status

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
    help="Sprinkler rain bypass controller",
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Path to settings.toml"),
    ] = Path("settings.toml"),
    once: Annotated[bool, typer.Option("--once", help="Run one cycle and exit")] = False,
) -> None:
    if ctx.invoked_subcommand is not None:
        return
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


@app.command("status")
def status(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Path to settings.toml"),
    ] = Path("settings.toml"),
    cached: Annotated[
        bool,
        typer.Option("--cached", help="Show saved state only; skip live weather fetch"),
    ] = False,
) -> None:
    """Print a text dashboard of weather, balance, and relay state."""
    try:
        print_status(config, fetch_live=not cached)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
