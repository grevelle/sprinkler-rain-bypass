from __future__ import annotations

import getpass
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
import typer
from pydantic import ValidationError

from rain_bypass.config import (
    ConfigError,
    Settings,
    load_example_settings,
    load_settings,
    write_settings,
)
from rain_bypass.controller import run
from rain_bypass.deploy import (
    DASHBOARD_SERVICE_NAME,
    SERVICE_NAME,
    ensure_wifi_reliability,
    install_autoupdate,
    install_dashboard_unit,
    install_systemd_unit,
    system_hostname,
)
from rain_bypass.exceptions import WeatherError
from rain_bypass.install_prompts import (
    load_prompt_defaults,
    load_settings_base,
    prompt_settings,
)
from rain_bypass.logging_setup import configure_logging
from rain_bypass.paths import repo_root
from rain_bypass.platform import is_pi_zero, is_raspberry_pi
from rain_bypass.prompting import Prompter, RunCommand, TyperPrompter
from rain_bypass.weather import weather_api_smoke

type CliHandler = Callable[[], None]


def _is_posix() -> bool:
    return os.name == "posix"


def resolve_state_path(install_root: Path, settings: Settings) -> Path:
    state_path = settings.runtime.state_path
    if state_path.is_absolute():
        return state_path
    return install_root / state_path


def ensure_state_writable(
    install_root: Path,
    settings: Settings,
    *,
    run_command: RunCommand | None = None,
) -> None:
    if not _is_posix():
        return
    state_path = resolve_state_path(install_root, settings)
    if not state_path.exists() or os.access(state_path, os.W_OK):
        return
    runner = run_command or subprocess.run
    user = getpass.getuser()
    typer.echo(
        f"==> Fixing permissions on {state_path.name} "
        "(systemd created it as root; installer runs as your user)"
    )
    if shutil.which("sudo") is None:
        typer.secho(
            f"Cannot write {state_path}. Run: sudo chown {user} {state_path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    runner(["sudo", "chown", f"{user}:{user}", str(state_path)], check=True)


def write_and_validate_settings(settings_path: Path, settings: Settings) -> None:
    write_settings(settings_path, settings)
    typer.echo(f"==> Wrote {settings_path}")
    typer.echo("==> Validating settings.toml")
    load_settings(settings_path)


def run_api_test(settings_path: Path) -> None:
    typer.echo("==> Testing Visual Crossing API (one fetch)")
    try:
        message = weather_api_smoke(load_settings(settings_path))
    except (WeatherError, ConfigError, ValidationError, httpx.HTTPError) as exc:
        typer.secho(f"API test failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    typer.echo(message)


def restart_service_if_installed(
    *,
    prompter: Prompter | None = None,
    run_command: RunCommand | None = None,
    skip: bool = False,
) -> None:
    if skip or shutil.which("systemctl") is None:
        return
    unit_path = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    if not unit_path.is_file():
        return
    prompts = prompter or TyperPrompter()
    if not prompts.confirm(f"Restart {SERVICE_NAME} service to apply changes?", default=True):
        return
    runner = run_command or subprocess.run
    typer.echo(f"==> Restarting {SERVICE_NAME} (requires sudo)")
    runner(["sudo", "systemctl", "restart", SERVICE_NAME], check=True)
    typer.echo(f"==> Service restarted. Status: sudo systemctl status {SERVICE_NAME}")


def run_configure(
    root: Path | None = None,
    *,
    prompter: Prompter | None = None,
    skip_api_test: bool = False,
    skip_service_restart: bool = False,
    run_command: RunCommand | None = None,
) -> None:
    install_root = root or repo_root()
    settings_path = install_root / "settings.toml"
    prompts = prompter or TyperPrompter()

    typer.echo("Sprinkler Rain Bypass — configure")
    typer.echo(f"Install directory: {install_root}")
    typer.echo(
        "Updates prompted fields in settings.toml (no pip install). "
        "Other options stay as-is — edit settings.toml directly for sewer, GPIO, etc.\n"
    )

    base = load_settings_base(settings_path)
    existing_key, profile = load_prompt_defaults(settings_path)
    settings = prompt_settings(
        base,
        prompts,
        existing_key=existing_key,
        defaults=profile,
    )
    write_and_validate_settings(settings_path, settings)

    if not skip_api_test:
        run_api_test(settings_path)

    restart_service_if_installed(
        prompter=prompts,
        run_command=run_command,
        skip=skip_service_restart,
    )

    typer.echo("\n==> Done.")
    typer.echo(f"  Config:  {settings_path}")
    typer.echo("  Other settings: nano settings.toml (then restart the service)")


def run_install(
    root: Path | None = None,
    *,
    prompter: Prompter | None = None,
    skip_systemd: bool = False,
    skip_once: bool = False,
    skip_api_test: bool = False,
    run_command: RunCommand | None = None,
) -> None:
    install_root = root or repo_root()
    settings_path = install_root / "settings.toml"
    python = install_root / ".venv" / "bin" / "python"
    prompts = prompter or TyperPrompter()
    example = load_example_settings()

    typer.echo("Sprinkler Rain Bypass — installer")
    typer.echo(f"Install directory: {install_root}")
    typer.echo("Press Enter for defaults from settings.example.toml except your API key.\n")
    if is_pi_zero():
        typer.secho(
            "Pi Zero detected — first pip install can take 10–20 minutes on a slow SD card.",
            fg=typer.colors.YELLOW,
        )

    existing_key, profile = load_prompt_defaults(settings_path)

    settings = prompt_settings(
        example,
        prompts,
        existing_key=existing_key,
        defaults=profile,
    )

    if settings_path.is_file() and not prompts.confirm(
        "settings.toml already exists. Overwrite?", default=True
    ):
        typer.secho("Aborted; existing settings.toml kept.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    write_and_validate_settings(settings_path, settings)

    if not skip_api_test:
        run_api_test(settings_path)

    if not skip_once and prompts.confirm("Run a live --once cycle now?", default=True):
        typer.echo("==> Running one control cycle (--once)")
        try:
            once_settings = load_settings(settings_path)
            configure_logging(once_settings.runtime.log_level)
            ensure_state_writable(install_root, once_settings, run_command=run_command)
            run(once_settings, once=True)
        except (OSError, ConfigError, WeatherError, RuntimeError) as exc:
            typer.secho(f"Control cycle failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None

    if not skip_systemd:
        install_systemd_unit(
            install_root, python, settings_path, prompter=prompts, run_command=run_command
        )
        install_dashboard_unit(
            install_root, python, settings_path, prompter=prompts, run_command=run_command
        )
        if is_raspberry_pi():
            ensure_wifi_reliability(run_command=run_command)
            install_autoupdate(install_root, prompter=prompts, run_command=run_command)

    typer.echo("\n==> Done.")
    typer.echo(
        f"  Balance: {settings.balance.inches_per_cycle:g} in/cycle; "
        f"check {settings.watering.check_hour:02d}:{settings.watering.check_minute:02d} "
        f"{settings.location.timezone}"
    )
    typer.echo(f"  Config:  {settings_path}")
    typer.echo(f"  Reconfigure: {install_root / 'configure.sh'}")
    typer.echo(f"  Manual:  {python} -m rain_bypass --once")
    typer.echo(f"  Loop:    {python} -m rain_bypass")
    unit_path = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    if unit_path.is_file():
        typer.echo(f"  Service: sudo systemctl status {SERVICE_NAME}")
    dashboard_path = Path(f"/etc/systemd/system/{DASHBOARD_SERVICE_NAME}.service")
    if dashboard_path.is_file():
        host = system_hostname()
        typer.echo(
            f"  Dashboard: http://{host}.local/  (sudo systemctl status {DASHBOARD_SERVICE_NAME})"
        )


def handle_cli_errors(fn: CliHandler) -> None:
    try:
        fn()
    except typer.Exit:
        raise
    except typer.BadParameter as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except WeatherError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    except KeyboardInterrupt:
        typer.echo("\nAborted.", err=True)
        raise typer.Exit(130) from None
