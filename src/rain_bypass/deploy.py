from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import typer

from rain_bypass.paths import repo_root

RunCommand = Callable[..., subprocess.CompletedProcess[bytes]]

SERVICE_NAME = "rain-bypass"
AUTO_UPDATE_SERVICE_NAME = "rain-bypass-auto-update"
DEPLOY_DIR = repo_root() / "deploy"


class Prompter(Protocol):
    def text(self, label: str, *, default: str = "") -> str: ...

    def confirm(self, label: str, *, default: bool = False) -> bool: ...


def _render_template(filename: str, replacements: dict[str, str]) -> str:
    path = DEPLOY_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"template not found: {path}")
    text = path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text


def render_systemd_unit(root: Path, python: Path, settings: Path, service_user: str) -> str:
    return _render_template(
        "rain-bypass.service.in",
        {
            "@ROOT@": root.as_posix(),
            "@PYTHON@": python.as_posix(),
            "@SETTINGS@": settings.as_posix(),
            "@USER@": service_user,
        },
    )


def render_autoupdate_service(root: Path, service_user: str) -> str:
    return _render_template(
        "rain-bypass-auto-update.service.in",
        {
            "@ROOT@": root.as_posix(),
            "@USER@": service_user,
        },
    )


def render_autoupdate_timer() -> str:
    return _render_template("rain-bypass-auto-update.timer.in", {})


def install_unattended_upgrades(*, run_command: RunCommand | None = None) -> None:
    """Enable Debian/Raspbian unattended-upgrades (apt-daily / apt-daily-upgrade timers)."""
    runner: RunCommand = run_command or subprocess.run
    if shutil.which("apt-get") is None:
        typer.secho(
            "warning: apt-get not found; skipping unattended-upgrades.",
            fg=typer.colors.YELLOW,
        )
        return

    auto_conf = DEPLOY_DIR / "apt-20auto-upgrades.conf"
    local_conf = DEPLOY_DIR / "apt-51unattended-upgrades-local.conf"
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
        "==> OS auto-update enabled "
        "(apt-daily.timer + apt-daily-upgrade.timer / unattended-upgrades)"
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
        "Enable daily automatic OS and application updates (12:00)?",
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
    if os.name == "posix":
        os.chmod(auto_script, 0o755)

    install_unattended_upgrades(run_command=runner)

    service_user = "root"
    if shutil.which("id") and runner(["id", "-u", "pi"], check=False).returncode == 0:
        service_user = "pi"

    service_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.service")
    timer_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.timer")
    typer.echo(f"==> Installing {service_path} and {timer_path} (requires sudo)")
    runner(
        ["sudo", "tee", str(service_path)],
        input=render_autoupdate_service(root, service_user).encode(),
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
        f"==> Auto-update enabled. Status: sudo systemctl list-timers "
        f"{AUTO_UPDATE_SERVICE_NAME}.timer"
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
