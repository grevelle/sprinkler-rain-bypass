from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import typer

from rain_bypass.config import Settings, load_settings
from rain_bypass.controller import run
from rain_bypass.exceptions import WeatherError
from rain_bypass.logging_setup import configure_logging
from rain_bypass.platform import is_pi_zero, is_raspberry_pi
from rain_bypass.settings_io import load_example_settings, write_settings
from rain_bypass.weather import resolve_location, weather_api_smoke

RunCommand = Callable[..., subprocess.CompletedProcess[bytes]]

SERVICE_NAME = "rain-bypass"
app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)


class Prompter(Protocol):
    def text(self, label: str, *, default: str = "") -> str: ...

    def confirm(self, label: str, *, default: bool = False) -> bool: ...

    def secret(self, label: str) -> str: ...


@dataclass(frozen=True)
class TyperPrompter:
    def text(self, label: str, *, default: str = "") -> str:
        return typer.prompt(label, default=default)

    def confirm(self, label: str, *, default: bool = False) -> bool:
        return typer.confirm(label, default=default)

    def secret(self, label: str) -> str:
        return typer.prompt(label, hide_input=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def systemd_template_path() -> Path:
    return repo_root() / "deploy" / "rain-bypass.service.in"


def render_systemd_unit(root: Path, python: Path, settings: Path, service_user: str) -> str:
    template = systemd_template_path()
    if not template.is_file():
        raise FileNotFoundError(f"systemd template not found: {template}")
    text = template.read_text(encoding="utf-8")
    return (
        text.replace("@ROOT@", root.as_posix())
        .replace("@PYTHON@", python.as_posix())
        .replace("@SETTINGS@", settings.as_posix())
        .replace("@USER@", service_user)
    )


def validate_api_key(key: str) -> str:
    text = key.strip()
    if not text:
        raise typer.BadParameter("API key cannot be empty.")
    if len(text) < 10:
        raise typer.BadParameter(
            "API key looks too short; paste the full key from Visual Crossing."
        )
    if '"' in text or "\\" in text:
        raise typer.BadParameter("API key contains invalid characters for settings.toml.")
    return text


def validate_zip_code(value: str) -> str:
    text = value.strip()
    if not re.fullmatch(r"\d{5}(?:-\d{4})?", text):
        raise typer.BadParameter("ZIP code must be 5 digits or ZIP+4.")
    return text[:5]


def build_settings(
    api_key: str,
    zip_code: str = "53029",
    *,
    gpio_mock: bool | None = None,
) -> Settings:
    key = validate_api_key(api_key)
    zip5 = validate_zip_code(zip_code)
    mock = not is_raspberry_pi() if gpio_mock is None else gpio_mock
    try:
        location = resolve_location(zip5, key)
    except WeatherError as exc:
        raise typer.BadParameter(str(exc)) from None
    return load_example_settings(
        location={
            "zip_code": location.zip_code,
            "latitude": location.latitude,
            "longitude": location.longitude,
            "timezone": location.timezone,
        },
        weather={"api_key": key},
        gpio={"mock": mock},
    )


def prompt_settings(
    _base: Settings,
    prompter: Prompter,
    *,
    existing_key: str | None = None,
) -> Settings:
    typer.echo(
        "\n==> Defaults from settings.example.toml "
        "(seasonal balance ON, 4:30 AM check; edit settings.toml to customize)\n"
    )
    typer.echo("==> Visual Crossing API (free key: https://www.visualcrossing.com/weather-api)")
    while True:
        if existing_key and prompter.confirm(
            "Keep existing Visual Crossing API key?", default=True
        ):
            api_key = existing_key
        else:
            api_key = prompter.secret("Visual Crossing API key")
        zip_code = prompter.text("ZIP code", default="53029")
        if not is_raspberry_pi():
            typer.secho(
                "warning: Raspberry Pi not detected; gpio.mock=true for this machine.",
                fg=typer.colors.YELLOW,
            )
        try:
            return build_settings(api_key, zip_code)
        except typer.BadParameter as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            typer.echo("Try again with a valid Visual Crossing API key.\n")
            existing_key = None


def _is_posix() -> bool:
    return os.name == "posix"


def write_settings_secure(path: Path, settings: Settings) -> None:
    write_settings(path, settings)
    if _is_posix():
        os.chmod(path, 0o600)


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
        raise typer.Exit(f"Cannot write {state_path}. Run: sudo chown {user} {state_path}")
    runner(["sudo", "chown", f"{user}:{user}", str(state_path)], check=True)


def install_systemd_unit(
    root: Path,
    python: Path,
    settings: Path,
    *,
    prompter: Prompter,
    run_command: RunCommand | None = None,
) -> None:
    runner: RunCommand = run_command or subprocess.run
    if shutil.which("systemctl") is None:
        typer.secho(
            "warning: systemctl not found; skipping service install.", fg=typer.colors.YELLOW
        )
        return
    if not prompter.confirm(f"Install and enable systemd service ({SERVICE_NAME})?", default=True):
        return

    service_user = "root"
    if shutil.which("id") and runner(["id", "-u", "pi"], check=False).returncode == 0:
        service_user = prompter.text(
            "Service user (needs GPIO access on Pi; root is safest)",
            default="root",
        )

    unit_path = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    typer.echo(f"==> Installing {unit_path} (requires sudo)")
    runner(
        ["sudo", "tee", str(unit_path)],
        input=render_systemd_unit(root, python, settings, service_user).encode(),
        check=True,
    )
    runner(["sudo", "systemctl", "daemon-reload"], check=True)
    runner(["sudo", "systemctl", "enable", SERVICE_NAME], check=True)
    runner(["sudo", "systemctl", "restart", SERVICE_NAME], check=True)
    typer.echo(f"==> Service enabled. Status: sudo systemctl status {SERVICE_NAME}")


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
    defaults = load_example_settings()

    typer.echo("Sprinkler Rain Bypass — installer")
    typer.echo(f"Install directory: {install_root}")
    typer.echo(
        "Press Enter for defaults (ZIP 53029, seasonal balance, 4:30 AM check) "
        "except your API key.\n"
    )
    if is_pi_zero():
        typer.secho(
            "Pi Zero detected — first pip install can take 10–20 minutes on a slow SD card.",
            fg=typer.colors.YELLOW,
        )

    existing_key: str | None = None
    if settings_path.is_file():
        try:
            existing_key = load_settings(settings_path).weather.api_key
        except Exception:
            existing_key = None

    settings = prompt_settings(defaults, prompts, existing_key=existing_key)

    if settings_path.is_file() and not prompts.confirm(
        "settings.toml already exists. Overwrite?", default=True
    ):
        typer.secho("Aborted; existing settings.toml kept.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    write_settings_secure(settings_path, settings)

    typer.echo(f"==> Wrote {settings_path}")
    typer.echo("==> Validating settings.toml")
    load_settings(settings_path)

    if not skip_api_test:
        typer.echo("==> Testing Visual Crossing API (one fetch)")
        try:
            message = weather_api_smoke(load_settings(settings_path))
        except Exception as exc:
            typer.secho(f"API test failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None
        typer.echo(message)

    if not skip_once and prompts.confirm("Run a live --once cycle now?", default=True):
        typer.echo("==> Running one control cycle (--once)")
        try:
            once_settings = load_settings(settings_path)
            configure_logging(once_settings.runtime.log_level)
            ensure_state_writable(install_root, once_settings, run_command=run_command)
            run(once_settings, once=True)
        except Exception as exc:
            typer.secho(f"Control cycle failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None

    if not skip_systemd:
        install_systemd_unit(
            install_root, python, settings_path, prompter=prompts, run_command=run_command
        )

    typer.echo("\n==> Done.")
    typer.echo(
        "  Balance: seasonal ON, 0.33 in/cycle — calibrate "
        "[balance].inches_per_cycle after a catch-cup test."
    )
    typer.echo(f"  Config:  {settings_path}")
    typer.echo(f"  Manual:  {python} -m rain_bypass --once")
    typer.echo(f"  Loop:    {python} -m rain_bypass")
    unit_path = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    if unit_path.is_file():
        typer.echo(f"  Service: sudo systemctl status {SERVICE_NAME}")


@app.callback(invoke_without_command=True)
def install() -> None:
    try:
        run_install()
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()  # pragma: no cover
