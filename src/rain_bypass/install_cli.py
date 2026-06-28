from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import typer

from rain_bypass.config import FailMode, Settings, load_settings
from rain_bypass.controller import run
from rain_bypass.settings_io import load_example_settings, write_settings
from rain_bypass.weather import weather_api_smoke

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


def is_raspberry_pi() -> bool:
    model = Path("/proc/device-tree/model")
    if not model.is_file():
        return False
    return "raspberry" in model.read_text(encoding="utf-8", errors="ignore").lower()


def validate_api_key(key: str) -> str:
    if not key:
        raise typer.BadParameter("API key cannot be empty.")
    if '"' in key or "\\" in key:
        raise typer.BadParameter("API key contains invalid characters for settings.toml.")
    return key


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def prompt_settings(base: Settings, prompter: Prompter) -> Settings:
    loc = base.location
    w = base.watering
    sewer = base.sewer
    rt = base.runtime
    gpio = base.gpio

    typer.echo("\n==> Configuration (Enter accepts the default in brackets)\n")
    typer.echo(
        "==> Location (53029 Hartland, WI defaults — change if your controller is elsewhere)"
    )
    latitude = float(prompter.text("Latitude (decimal degrees)", default=str(loc.latitude)))
    longitude = float(prompter.text("Longitude (decimal degrees)", default=str(loc.longitude)))
    timezone = prompter.text("Timezone (IANA, e.g. America/Chicago)", default=loc.timezone)

    typer.echo("\n==> Watering thresholds (defaults match settings.example.toml)")
    inches_required = float(
        prompter.text(
            "Past window: block if total rain exceeds (inches)", default=str(w.inches_required)
        )
    )
    past_days = int(
        prompter.text(
            "Past window: sum rain over this many days (through today)", default=str(w.past_days)
        )
    )
    forecast_days = int(
        prompter.text(
            "Forecast window: look ahead this many days (0 to disable)",
            default=str(w.forecast_days),
        )
    )
    forecast_inches_max = float(
        prompter.text(
            "Forecast window: block if total forecast rain exceeds (inches)",
            default=str(w.forecast_inches_max),
        )
    )
    event_inches = float(
        prompter.text(
            "Past window: block if any single day reaches (inches; 0 = cumulative only)",
            default=str(w.event_inches),
        )
    )
    rain_delay_days = int(
        prompter.text(
            "After a past-window block, stay off this many days (Rain Bird uses 2)",
            default=str(w.rain_delay_days),
        )
    )
    near_term_hours = int(
        prompter.text(
            "Block if rain exceeds threshold within this many hours (0 = off)",
            default=str(w.near_term_hours),
        )
    )
    near_term_inches_max = float(
        prompter.text(
            "Near-term window: block if total rain exceeds (inches)",
            default=str(w.near_term_inches_max),
        )
    )
    freeze_skip = _parse_bool(
        prompter.text(
            "Block when forecast low is below freeze temp (true/false)",
            default=str(w.freeze_skip).lower(),
        )
    )
    freeze_temp_f = float(
        prompter.text("Freeze skip threshold (Fahrenheit)", default=str(w.freeze_temp_f))
    )
    check_hour = int(
        prompter.text(
            "Daily check hour, local time 0-23 (used when checks/day = 1)",
            default=str(w.check_hour),
        )
    )
    check_minute = int(prompter.text("Daily check minute 0-59", default=str(w.check_minute)))
    updates_per_day = int(
        prompter.text(
            "Weather checks per day (1 aligns to check hour above)", default=str(w.updates_per_day)
        )
    )

    typer.echo(
        "\n==> Sewer lockout (blocks ALL watering in this window — adjust only if your city differs)"
    )
    typer.echo("  City sewer charge is based on water use Jan 16–Mar 15 (April utility bill).")
    sewer_start_month = int(
        prompter.text("Sewer lockout start month (1-12)", default=str(sewer.start_month))
    )
    sewer_start_day = int(
        prompter.text("Sewer lockout start day (1-31)", default=str(sewer.start_day))
    )
    sewer_end_month = int(
        prompter.text("Sewer lockout end month (1-12)", default=str(sewer.end_month))
    )
    sewer_end_day = int(prompter.text("Sewer lockout end day (1-31)", default=str(sewer.end_day)))

    typer.echo("\n==> Visual Crossing API (free key: https://www.visualcrossing.com/weather-api)")
    api_key = validate_api_key(prompter.secret("Visual Crossing API key"))

    typer.echo("\n==> GPIO pins (BCM numbering)")
    gpio_relay = int(prompter.text("Relay pin", default=str(gpio.relay)))
    gpio_enabled = int(
        prompter.text("Green LED pin (watering allowed)", default=str(gpio.watering_enabled_led))
    )
    gpio_disabled = int(
        prompter.text("Red LED pin (watering blocked)", default=str(gpio.watering_disabled_led))
    )
    mock_default = not is_raspberry_pi()
    if mock_default:
        typer.secho(
            "warning: Raspberry Pi not detected; defaulting gpio.mock=true for this machine.",
            fg=typer.colors.YELLOW,
        )
    gpio_mock = _parse_bool(
        prompter.text("Use mock GPIO (no hardware)", default=str(mock_default).lower())
    )

    typer.echo("\n==> Runtime")
    fail_mode = prompter.text(
        "Fail mode (disable_watering | keep_last_state)",
        default=rt.fail_mode.value,
    )
    if fail_mode not in {mode.value for mode in FailMode}:
        raise typer.BadParameter("fail_mode must be disable_watering or keep_last_state")
    log_level = prompter.text("Log level (DEBUG | INFO | WARNING)", default=rt.log_level)
    weather_timeout_seconds = int(
        prompter.text("Weather API timeout (seconds)", default=str(rt.weather_timeout_seconds))
    )

    return load_example_settings(
        location={"latitude": latitude, "longitude": longitude, "timezone": timezone},
        watering={
            "inches_required": inches_required,
            "past_days": past_days,
            "forecast_days": forecast_days,
            "forecast_inches_max": forecast_inches_max,
            "event_inches": event_inches,
            "rain_delay_days": rain_delay_days,
            "near_term_hours": near_term_hours,
            "near_term_inches_max": near_term_inches_max,
            "freeze_skip": freeze_skip,
            "freeze_temp_f": freeze_temp_f,
            "check_hour": check_hour,
            "check_minute": check_minute,
            "updates_per_day": updates_per_day,
        },
        sewer={
            "start_month": sewer_start_month,
            "start_day": sewer_start_day,
            "end_month": sewer_end_month,
            "end_day": sewer_end_day,
        },
        weather={"api_key": api_key},
        gpio={
            "relay": gpio_relay,
            "watering_enabled_led": gpio_enabled,
            "watering_disabled_led": gpio_disabled,
            "mock": gpio_mock,
        },
        runtime={
            "state_path": "state.json",
            "fail_mode": fail_mode,
            "log_level": log_level,
            "weather_timeout_seconds": weather_timeout_seconds,
        },
    )


def write_settings_secure(path: Path, settings: Settings) -> None:
    write_settings(path, settings)
    if os.name == "posix":
        os.chmod(path, 0o600)


def systemd_unit(root: Path, python: Path, settings: Path, service_user: str) -> str:
    root_s = root.as_posix()
    python_s = python.as_posix()
    settings_s = settings.as_posix()
    return f"""[Unit]
Description=Sprinkler rain bypass controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User={service_user}
WorkingDirectory={root_s}
Environment=PYTHONUNBUFFERED=1
ExecStart={python_s} -m rain_bypass --config {settings_s}
Restart=on-failure
RestartSec=300

[Install]
WantedBy=multi-user.target
"""


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
        input=systemd_unit(root, python, settings, service_user).encode(),
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
    typer.echo(f"Install directory: {install_root}\n")

    settings = prompt_settings(defaults, prompts)

    if settings_path.is_file() and not prompts.confirm(
        "settings.toml already exists. Overwrite?", default=False
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
            raise typer.Exit(1) from exc
        typer.echo(message)

    if not skip_once and prompts.confirm("Run a live --once cycle now?", default=True):
        typer.echo("==> Running one control cycle (--once)")
        try:
            run(load_settings(settings_path), once=True)
        except Exception as exc:
            typer.secho(f"Control cycle failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    if not skip_systemd:
        install_systemd_unit(
            install_root, python, settings_path, prompter=prompts, run_command=run_command
        )

    typer.echo("\n==> Done.")
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
    except typer.BadParameter:
        raise
    except KeyboardInterrupt:
        typer.echo("\nAborted.", err=True)
        raise typer.Exit(130) from None


def main() -> None:
    app()


if __name__ == "__main__":
    main()  # pragma: no cover
