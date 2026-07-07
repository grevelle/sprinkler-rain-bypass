from __future__ import annotations

import getpass
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol

import typer

from rain_bypass.config import Location, Settings, load_settings
from rain_bypass.controller import run
from rain_bypass.exceptions import WeatherError
from rain_bypass.logging_setup import configure_logging
from rain_bypass.platform import is_pi_zero, is_raspberry_pi
from rain_bypass.settings_io import load_example_settings, write_settings
from rain_bypass.weather import resolve_location, weather_api_smoke

RunCommand = Callable[..., subprocess.CompletedProcess[bytes]]

SERVICE_NAME = "rain-bypass"
AUTO_UPDATE_SERVICE_NAME = "rain-bypass-auto-update"
app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    pretty_exceptions_show_locals=False,
)


@dataclass(frozen=True, slots=True)
class PromptDefaults:
    zip_code: str = "53029"
    inches_per_cycle: float = 0.3
    check_hour: int = 0
    check_minute: int = 0


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


def autoupdate_service_template_path() -> Path:
    return repo_root() / "deploy" / "rain-bypass-auto-update.service.in"


def autoupdate_timer_template_path() -> Path:
    return repo_root() / "deploy" / "rain-bypass-auto-update.timer.in"


def apt_auto_upgrades_path() -> Path:
    return repo_root() / "deploy" / "apt-20auto-upgrades.conf"


def apt_unattended_local_path() -> Path:
    return repo_root() / "deploy" / "apt-51unattended-upgrades-local.conf"


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


def validate_inches_per_cycle(value: str) -> float:
    text = value.strip()
    try:
        inches = float(text)
    except ValueError as exc:
        raise typer.BadParameter("Inches per cycle must be a number.") from exc
    if inches <= 0:
        raise typer.BadParameter("Inches per cycle must be greater than 0.")
    return inches


def validate_check_time(value: str) -> tuple[int, int]:
    text = value.strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise typer.BadParameter("Check time must be HH:MM in 24-hour format (e.g. 00:00).")
    hour = int(match.group(1))
    minute = int(match.group(2))
    if hour > 23 or minute > 59:
        raise typer.BadParameter("Check time hour must be 0-23 and minute 0-59.")
    return hour, minute


def load_settings_base(settings_path: Path) -> Settings:
    if settings_path.is_file():
        try:
            return load_settings(settings_path)
        except Exception:
            pass
    return load_example_settings()


def load_prompt_defaults(settings_path: Path) -> tuple[str | None, PromptDefaults]:
    if not settings_path.is_file():
        return None, PromptDefaults()
    try:
        existing = load_settings(settings_path)
        return existing.weather.api_key, PromptDefaults(
            zip_code=existing.location.zip_code,
            inches_per_cycle=existing.balance.inches_per_cycle,
            check_hour=existing.watering.check_hour,
            check_minute=existing.watering.check_minute,
        )
    except Exception:
        return None, PromptDefaults()


def load_existing_settings_fields(settings_path: Path) -> tuple[str | None, str]:
    key, profile = load_prompt_defaults(settings_path)
    return key, profile.zip_code


def apply_prompt_overrides(
    base: Settings,
    *,
    location: Location,
    api_key: str,
    inches_per_cycle: float,
    check_hour: int,
    check_minute: int,
    gpio_mock: bool,
) -> Settings:
    return base.model_copy(
        update={
            "location": location,
            "weather": base.weather.model_copy(update={"api_key": api_key}),
            "balance": base.balance.model_copy(update={"inches_per_cycle": inches_per_cycle}),
            "watering": base.watering.model_copy(
                update={"check_hour": check_hour, "check_minute": check_minute}
            ),
            "gpio": base.gpio.model_copy(update={"mock": gpio_mock}),
        }
    )


def build_settings(
    api_key: str,
    zip_code: str = "53029",
    *,
    base: Settings | None = None,
    inches_per_cycle: float = 0.3,
    check_hour: int = 0,
    check_minute: int = 0,
    gpio_mock: bool | None = None,
) -> Settings:
    key = validate_api_key(api_key)
    zip5 = validate_zip_code(zip_code)
    mock = not is_raspberry_pi() if gpio_mock is None else gpio_mock
    try:
        location = resolve_location(zip5, key)
    except WeatherError as exc:
        raise typer.BadParameter(str(exc)) from None
    settings_base = base or load_example_settings()
    return apply_prompt_overrides(
        settings_base,
        location=location,
        api_key=key,
        inches_per_cycle=inches_per_cycle,
        check_hour=check_hour,
        check_minute=check_minute,
        gpio_mock=mock,
    )


def prompt_watering_profile(prompter: Prompter, defaults: PromptDefaults) -> tuple[float, int, int]:
    typer.echo(
        "\n==> Sprinkler program — one daily run; calibrate inches per cycle with catch cups later if needed."
    )
    inches = validate_inches_per_cycle(
        prompter.text(
            "Inches per cycle (30 min/zone ≈ 0.3)",
            default=f"{defaults.inches_per_cycle:g}",
        )
    )
    check_hour, check_minute = validate_check_time(
        prompter.text(
            "Daily check time before irrigation (HH:MM, 24h)",
            default=f"{defaults.check_hour:02d}:{defaults.check_minute:02d}",
        )
    )
    return inches, check_hour, check_minute


def prompt_settings(
    base: Settings,
    prompter: Prompter,
    *,
    existing_key: str | None = None,
    defaults: PromptDefaults | None = None,
) -> Settings:
    profile = defaults or PromptDefaults()
    typer.echo(
        "\n==> Press Enter for defaults from your existing settings or settings.example.toml.\n"
    )
    typer.echo("==> Visual Crossing API (free key: https://www.visualcrossing.com/weather-api)")
    while True:
        if existing_key and prompter.confirm(
            "Keep existing Visual Crossing API key?", default=True
        ):
            api_key = existing_key
        else:
            api_key = prompter.secret("Visual Crossing API key")
        zip_code = prompter.text("ZIP code", default=profile.zip_code)
        if not is_raspberry_pi():
            typer.secho(
                "warning: Raspberry Pi not detected; gpio.mock=true for this machine.",
                fg=typer.colors.YELLOW,
            )
        try:
            inches, check_hour, check_minute = prompt_watering_profile(prompter, profile)
            return build_settings(
                api_key,
                zip_code,
                base=base,
                inches_per_cycle=inches,
                check_hour=check_hour,
                check_minute=check_minute,
            )
        except typer.BadParameter as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            typer.echo("Try again.\n")
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
        typer.secho(
            f"Cannot write {state_path}. Run: sudo chown {user} {state_path}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
    runner(["sudo", "chown", f"{user}:{user}", str(state_path)], check=True)


def write_and_validate_settings(settings_path: Path, settings: Settings) -> None:
    write_settings_secure(settings_path, settings)
    typer.echo(f"==> Wrote {settings_path}")
    typer.echo("==> Validating settings.toml")
    load_settings(settings_path)


def run_api_test(settings_path: Path) -> None:
    typer.echo("==> Testing Visual Crossing API (one fetch)")
    try:
        message = weather_api_smoke(load_settings(settings_path))
    except Exception as exc:
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


def render_autoupdate_service(root: Path) -> str:
    template = autoupdate_service_template_path()
    if not template.is_file():
        raise FileNotFoundError(f"auto-update service template not found: {template}")
    return template.read_text(encoding="utf-8").replace("@ROOT@", root.as_posix())


def render_autoupdate_timer() -> str:
    template = autoupdate_timer_template_path()
    if not template.is_file():
        raise FileNotFoundError(f"auto-update timer template not found: {template}")
    return template.read_text(encoding="utf-8")


def install_unattended_upgrades(*, run_command: RunCommand | None = None) -> None:
    """Enable Debian/Raspbian unattended-upgrades (apt-daily / apt-daily-upgrade timers)."""
    runner: RunCommand = run_command or subprocess.run
    if shutil.which("apt-get") is None:
        typer.secho(
            "warning: apt-get not found; skipping unattended-upgrades.",
            fg=typer.colors.YELLOW,
        )
        return

    auto_conf = apt_auto_upgrades_path()
    local_conf = apt_unattended_local_path()
    if not auto_conf.is_file() or not local_conf.is_file():
        typer.secho(
            "warning: unattended-upgrades config templates missing; skipping OS auto-update.",
            fg=typer.colors.YELLOW,
        )
        return

    typer.echo("==> Installing unattended-upgrades (automatic OS package updates)")
    runner(["sudo", "apt-get", "update", "-qq"], check=True)
    runner(
        ["sudo", "apt-get", "install", "-y", "-qq", "unattended-upgrades"],
        check=True,
    )
    runner(
        ["sudo", "tee", "/etc/apt/apt.conf.d/20auto-upgrades"],
        input=auto_conf.read_bytes(),
        check=True,
    )
    runner(
        ["sudo", "tee", "/etc/apt/apt.conf.d/51unattended-upgrades-rain-bypass"],
        input=local_conf.read_bytes(),
        check=True,
    )
    if shutil.which("systemctl") is not None:
        runner(["sudo", "systemctl", "enable", "--now", "apt-daily.timer"], check=False)
        runner(["sudo", "systemctl", "enable", "--now", "apt-daily-upgrade.timer"], check=False)
    typer.echo(
        "==> OS auto-update enabled (apt-daily.timer + apt-daily-upgrade.timer / unattended-upgrades)"
    )


def install_autoupdate(
    root: Path,
    *,
    prompter: Prompter,
    run_command: RunCommand | None = None,
    skip_confirm: bool = False,
) -> None:
    runner: RunCommand = run_command or subprocess.run
    if shutil.which("systemctl") is None:
        typer.secho(
            "warning: systemctl not found; skipping auto-update timer.", fg=typer.colors.YELLOW
        )
        return
    if not skip_confirm and not prompter.confirm(
        "Enable daily automatic OS and application updates (04:00)?",
        default=True,
    ):
        return

    auto_script = root / "scripts" / "auto-update.sh"
    if not auto_script.is_file():
        typer.secho(
            f"warning: {auto_script} not found; skipping auto-update timer.",
            fg=typer.colors.YELLOW,
        )
        return
    if _is_posix():
        os.chmod(auto_script, 0o755)

    install_unattended_upgrades(run_command=runner)

    service_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.service")
    timer_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.timer")
    typer.echo(f"==> Installing {service_path} and {timer_path} (requires sudo)")
    runner(
        ["sudo", "tee", str(service_path)],
        input=render_autoupdate_service(root).encode(),
        check=True,
    )
    runner(
        ["sudo", "tee", str(timer_path)],
        input=render_autoupdate_timer().encode(),
        check=True,
    )
    runner(["sudo", "systemctl", "daemon-reload"], check=True)
    runner(
        ["sudo", "systemctl", "enable", "--now", f"{AUTO_UPDATE_SERVICE_NAME}.timer"], check=True
    )
    typer.echo(
        f"==> Auto-update enabled. Status: sudo systemctl list-timers {AUTO_UPDATE_SERVICE_NAME}.timer"
    )


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

    typer.echo("Sprinkler Rain Bypass — installer")
    typer.echo(f"Install directory: {install_root}")
    typer.echo(
        "Press Enter for defaults except your API key (ZIP 53029, 0.3 in/cycle, midnight check).\n"
    )
    if is_pi_zero():
        typer.secho(
            "Pi Zero detected — first pip install can take 10–20 minutes on a slow SD card.",
            fg=typer.colors.YELLOW,
        )

    existing_key, profile = load_prompt_defaults(settings_path)

    settings = prompt_settings(
        load_example_settings(),
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
        except Exception as exc:
            typer.secho(f"Control cycle failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from None

    if not skip_systemd:
        install_systemd_unit(
            install_root, python, settings_path, prompter=prompts, run_command=run_command
        )
        if is_raspberry_pi():
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


def _handle_cli_errors(fn: Callable[[], None]) -> None:
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


@app.callback(invoke_without_command=True)
def install(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _handle_cli_errors(run_install)


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
    _handle_cli_errors(
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
    _handle_cli_errors(
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
