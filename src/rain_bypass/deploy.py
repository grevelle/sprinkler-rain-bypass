from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import typer

from rain_bypass.paths import repo_root
from rain_bypass.prompting import Prompter, RunCommand, detect_service_user

SERVICE_NAME = "rain-bypass"
DASHBOARD_SERVICE_NAME = "rain-bypass-dashboard"
AUTO_UPDATE_SERVICE_NAME = "rain-bypass-auto-update"
DEPLOY_DIR = repo_root() / "deploy"


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


def render_dashboard_unit(root: Path, python: Path, settings: Path, service_user: str) -> str:
    return _render_template(
        "rain-bypass-dashboard.service.in",
        {
            "@ROOT@": root.as_posix(),
            "@PYTHON@": python.as_posix(),
            "@SETTINGS@": settings.as_posix(),
            "@USER@": service_user,
        },
    )


def system_hostname() -> str:
    return socket.gethostname().lower()


def verify_mdns(hostname: str, *, retries: int = 3, delay_seconds: float = 0.5) -> bool:
    """Return True if hostname.local resolves on this machine."""
    fqdn = f"{hostname.lower()}.local"
    resolver = shutil.which("avahi-resolve-host-name")
    if resolver is not None:
        result = subprocess.run(
            [resolver, fqdn],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return True
    for attempt in range(retries):
        try:
            socket.getaddrinfo(fqdn, 80, type=socket.SOCK_STREAM)
            return True
        except OSError:
            if attempt + 1 >= retries:
                return False
            time.sleep(delay_seconds)
    return False


def ensure_mdns(*, run_command: RunCommand | None = None) -> bool:
    """Install and enable Avahi so hostname.local resolves. Returns verification result."""
    if os.name != "posix":
        return False
    runner: RunCommand = run_command or subprocess.run
    if shutil.which("apt-get") is None:
        typer.secho(
            "warning: apt-get not found; skipping mDNS setup.",
            fg=typer.colors.YELLOW,
        )
        return False

    hostname = system_hostname()
    typer.echo("==> Ensuring mDNS (Avahi) for LAN dashboard access")
    runner(
        ["sudo", "apt-get", "install", "-y", "-qq", "avahi-daemon", "avahi-utils"],
        check=True,
    )
    if shutil.which("systemctl") is not None:
        runner(["sudo", "systemctl", "enable", "--now", "avahi-daemon"], check=True)

    if verify_mdns(hostname):
        typer.echo(f"==> mDNS OK — open http://{hostname}.local/ on your phone")
        return True

    typer.secho(
        f"warning: could not verify mDNS for {hostname}.local. "
        "Check: systemctl status avahi-daemon; same Wi-Fi/VLAN; or use http://<pi-ip>/",
        fg=typer.colors.YELLOW,
    )
    return False


def _write_systemd_unit(
    unit_path: Path,
    unit_name: str,
    unit_content: str,
    *,
    runner: RunCommand,
    enable: bool = True,
    restart: bool = True,
) -> None:
    typer.echo(f"==> Installing {unit_path} (requires sudo)")
    runner(
        ["sudo", "tee", str(unit_path)],
        input=unit_content.encode(),
        check=True,
    )
    runner(["sudo", "systemctl", "daemon-reload"], check=True)
    if enable:
        runner(["sudo", "systemctl", "enable", unit_name], check=True)
    if restart:
        runner(["sudo", "systemctl", "restart", unit_name], check=True)


def _install_unit_if_confirmed(
    *,
    unit_name: str,
    unit_filename: str,
    render: Callable[[str], str],
    confirm_prompt: str,
    success_message: str,
    skip_warning: str,
    prompter: Prompter,
    runner: RunCommand,
    post_install: Callable[[], None] | None = None,
    enable: bool = True,
    restart: bool = True,
) -> None:
    if shutil.which("systemctl") is None:
        typer.secho(skip_warning, fg=typer.colors.YELLOW)
        return
    if not prompter.confirm(confirm_prompt, default=True):
        return
    service_user = detect_service_user(run_command=runner, prompter=prompter)
    unit_path = Path(f"/etc/systemd/system/{unit_filename}")
    _write_systemd_unit(
        unit_path,
        unit_name,
        render(service_user),
        runner=runner,
        enable=enable,
        restart=restart,
    )
    typer.echo(success_message)
    if post_install is not None:
        post_install()


def install_dashboard_unit(
    root: Path,
    python: Path,
    settings: Path,
    *,
    prompter: Prompter,
    run_command: RunCommand | None = None,
) -> None:
    runner: RunCommand = run_command or subprocess.run

    def _post_mdns() -> None:
        ensure_mdns(run_command=runner)

    hostname = system_hostname()
    _install_unit_if_confirmed(
        unit_name=DASHBOARD_SERVICE_NAME,
        unit_filename=f"{DASHBOARD_SERVICE_NAME}.service",
        render=lambda user: render_dashboard_unit(root, python, settings, user),
        confirm_prompt=(f"Install dashboard + mDNS (phone status at http://{hostname}.local/)?"),
        success_message=(
            f"==> Dashboard enabled. Status: sudo systemctl status {DASHBOARD_SERVICE_NAME}"
        ),
        skip_warning="warning: systemctl not found; skipping dashboard service install.",
        prompter=prompter,
        runner=runner,
        post_install=_post_mdns,
    )


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

    service_user = detect_service_user(run_command=runner)

    service_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.service")
    timer_path = Path(f"/etc/systemd/system/{AUTO_UPDATE_SERVICE_NAME}.timer")
    typer.echo(f"==> Installing {service_path} and {timer_path} (requires sudo)")
    _write_systemd_unit(
        service_path,
        AUTO_UPDATE_SERVICE_NAME,
        render_autoupdate_service(root, service_user),
        runner=runner,
        restart=False,
    )
    _write_systemd_unit(
        timer_path,
        f"{AUTO_UPDATE_SERVICE_NAME}.timer",
        render_autoupdate_timer(),
        runner=runner,
        enable=False,
        restart=False,
    )
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
    _install_unit_if_confirmed(
        unit_name=SERVICE_NAME,
        unit_filename=f"{SERVICE_NAME}.service",
        render=lambda user: render_systemd_unit(root, python, settings, user),
        confirm_prompt=f"Install and enable systemd service ({SERVICE_NAME})?",
        success_message=f"==> Service enabled. Status: sudo systemctl status {SERVICE_NAME}",
        skip_warning="warning: systemctl not found; skipping service install.",
        prompter=prompter,
        runner=runner,
    )
