from __future__ import annotations

from typing import Annotated

import typer

from rain_bypass.deploy import install_autoupdate
from rain_bypass.install_flow import (
    handle_cli_errors,
    run_configure,
    run_install,
)
from rain_bypass.paths import repo_root
from rain_bypass.prompting import TyperPrompter

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)


@app.callback(invoke_without_command=True)
def install(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        handle_cli_errors(run_install)


@app.command("configure")
def configure(
    skip_api_test: Annotated[
        bool,
        typer.Option("--skip-api-test", help="Skip the Visual Crossing smoke test."),
    ] = False,
    skip_service_restart: Annotated[
        bool,
        typer.Option("--no-restart", help="Do not restart the systemd service."),
    ] = False,
) -> None:
    """Update API key, location, inches per cycle, and check time."""
    handle_cli_errors(
        lambda: run_configure(
            skip_api_test=skip_api_test,
            skip_service_restart=skip_service_restart,
        )
    )


@app.command("setup-autoupdate")
def setup_autoupdate(
    yes: Annotated[
        bool,
        typer.Option("-y", "--yes", help="Install without prompting."),
    ] = False,
) -> None:
    """Install and enable the daily OS + app auto-update timer."""
    handle_cli_errors(
        lambda: install_autoupdate(
            repo_root(),
            prompter=TyperPrompter(),
            skip_confirm=yes,
        )
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()  # pragma: no cover
