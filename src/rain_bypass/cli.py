from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer

from rain_bypass.config import ConfigError, load_settings
from rain_bypass.controller import run
from rain_bypass.history import print_history
from rain_bypass.logging_setup import configure_logging
from rain_bypass.status import print_status
from rain_bypass.web import run_server

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


@app.command("history")
def history(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Path to settings.toml"),
    ] = Path("settings.toml"),
    limit: Annotated[
        int,
        typer.Option("--limit", help="Maximum number of recent records to show", min=1),
    ] = 30,
) -> None:
    """Show recent watering decisions (append-only log from each control cycle)."""
    try:
        print_history(config, limit=limit)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None


@app.command("serve")
def serve(
    config: Annotated[
        Path,
        typer.Option("-c", "--config", help="Path to settings.toml"),
    ] = Path("settings.toml"),
    host: Annotated[
        str | None,
        typer.Option("--host", help="Bind address (default from settings [web].host)"),
    ] = None,
    port: Annotated[
        int | None,
        typer.Option("--port", help="Bind port (default from settings [web].port)", min=1),
    ] = None,
) -> None:
    """Run the read-only mobile web dashboard (GET / and GET /live)."""
    try:
        settings = load_settings(config)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    configure_logging(settings.runtime.log_level)
    bind_host = host if host is not None else settings.web.host
    bind_port = port if port is not None else settings.web.port
    try:
        run_server(config, host=bind_host, port=bind_port)
    except OSError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        raise typer.Exit(0) from None
