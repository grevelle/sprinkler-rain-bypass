from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from rain_bypass.deploy import (
    AUTO_UPDATE_SERVICE_NAME,
    DASHBOARD_SERVICE_NAME,
    ensure_mdns,
    install_autoupdate,
    install_dashboard_unit,
    install_systemd_unit,
    install_unattended_upgrades,
    render_autoupdate_service,
    render_autoupdate_timer,
    render_dashboard_unit,
    render_systemd_unit,
    system_hostname,
    verify_mdns,
)
from rain_bypass.prompting import detect_service_user


@dataclass
class FakePrompter:
    answers: list[str] = field(default_factory=list)
    confirms: list[bool] = field(default_factory=list)
    secrets: list[str] = field(default_factory=list)
    text_calls: list[tuple[str, str]] = field(default_factory=list)
    confirm_calls: list[tuple[str, bool]] = field(default_factory=list)

    def text(self, label: str, *, default: str = "") -> str:
        self.text_calls.append((label, default))
        if self.answers:
            return self.answers.pop(0)
        return default

    def confirm(self, label: str, *, default: bool = False) -> bool:
        self.confirm_calls.append((label, default))
        if self.confirms:
            return self.confirms.pop(0)
        return default

    def secret(self, label: str) -> str:
        if self.secrets:
            return self.secrets.pop(0)
        return "test-key-001"


def test_detect_service_user_returns_root_without_pi(monkeypatch):
    monkeypatch.setattr("rain_bypass.prompting.shutil.which", lambda _name: None)
    assert detect_service_user() == "root"


def test_detect_service_user_returns_pi_when_present(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.prompting.shutil.which", lambda name: "/usr/bin/id" if name == "id" else None
    )

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0)

    assert detect_service_user(run_command=fake_run) == "pi"


def test_detect_service_user_prompts_when_pi_present(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.prompting.shutil.which", lambda name: "/usr/bin/id" if name == "id" else None
    )

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0)

    prompter = FakePrompter(answers=["root"])
    assert detect_service_user(run_command=fake_run, prompter=prompter) == "root"
    assert prompter.text_calls == [
        ("Service user (needs GPIO access on Pi; root is safest)", "root"),
    ]


def test_render_systemd_unit():
    text = render_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        "root",
    )
    assert "WorkingDirectory=/opt/app" in text
    assert "User=root" in text
    assert "MemoryMax=256M" in text
    assert "@ROOT@" not in text


def test_render_systemd_unit_missing_template(monkeypatch, tmp_path):
    monkeypatch.setattr("rain_bypass.deploy.DEPLOY_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="template not found"):
        render_systemd_unit(
            Path("/opt/app"),
            Path("/opt/app/.venv/bin/python"),
            Path("/opt/app/settings.toml"),
            "root",
        )


def test_install_systemd_unit_skips_without_systemctl(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    install_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        prompter=FakePrompter(confirms=[True]),
    )


def test_install_systemd_unit_declined(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    install_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        prompter=FakePrompter(confirms=[False]),
    )


def test_install_systemd_unit_installs(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    install_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
    )
    assert calls[0][:2] == ["sudo", "tee"]


def test_install_systemd_unit_prompts_for_pi_user(monkeypatch):
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["sudo", "tee"]:
            captured["unit"] = kwargs["input"].decode()
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"systemctl", "id"} else None,
    )
    prompter = FakePrompter(confirms=[True], answers=["pi"])
    install_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        prompter=prompter,
        run_command=fake_run,
    )
    assert "User=pi" in captured["unit"]


def test_render_autoupdate_service():
    text = render_autoupdate_service(Path("/opt/sprinkler-rain-bypass"), "pi")
    assert "/opt/sprinkler-rain-bypass/scripts/auto-update.sh" in text
    assert "User=pi" in text
    assert "@ROOT@" not in text
    assert "@USER@" not in text


def test_render_autoupdate_service_missing_template(monkeypatch, tmp_path):
    monkeypatch.setattr("rain_bypass.deploy.DEPLOY_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="template not found"):
        render_autoupdate_service(tmp_path, "pi")


def test_render_autoupdate_timer():
    text = render_autoupdate_timer()
    assert "OnCalendar=*-*-* 12:00:00" in text


def test_render_autoupdate_timer_missing_template(monkeypatch, tmp_path):
    monkeypatch.setattr("rain_bypass.deploy.DEPLOY_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="template not found"):
        render_autoupdate_timer()


def test_install_autoupdate_skips_without_systemctl(monkeypatch, tmp_path):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    install_autoupdate(tmp_path, prompter=FakePrompter(confirms=[True]))


def test_install_autoupdate_declined(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    install_autoupdate(tmp_path, prompter=FakePrompter(confirms=[False]))


def test_install_autoupdate_missing_script(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    install_autoupdate(tmp_path, prompter=FakePrompter(confirms=[True]))


def test_install_autoupdate_installs(monkeypatch, tmp_path):
    script = tmp_path / "scripts"
    script.mkdir()
    (script / "auto-update.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    chmod_calls: list[tuple[Path, int]] = []
    calls: list[list[str]] = []
    tee_inputs: list[bytes] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["sudo", "tee"] and str(cmd[2]).endswith(".service"):
            tee_inputs.append(kwargs.get("input", b""))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        "rain_bypass.deploy.os.chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: {
            "systemctl": "/usr/bin/systemctl",
            "id": "/usr/bin/id",
        }.get(name),
    )
    monkeypatch.setattr(
        "rain_bypass.deploy.install_unattended_upgrades",
        lambda **kwargs: None,
    )
    install_autoupdate(
        tmp_path,
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
        skip_confirm=True,
    )
    assert chmod_calls == [(script / "auto-update.sh", 0o755)]
    tee_calls = [c for c in calls if c[:2] == ["sudo", "tee"] and str(c[2]).endswith(".service")]
    assert len(tee_calls) == 1
    assert tee_inputs == [render_autoupdate_service(tmp_path, "pi").encode()]
    assert calls[-1] == [
        "sudo",
        "systemctl",
        "enable",
        "--now",
        f"{AUTO_UPDATE_SERVICE_NAME}.timer",
    ]


def test_install_autoupdate_service_user_root_without_pi(monkeypatch, tmp_path):
    script = tmp_path / "scripts"
    script.mkdir()
    (script / "auto-update.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    tee_inputs: list[bytes] = []

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["sudo", "tee"] and str(cmd[2]).endswith(".service"):
            tee_inputs.append(kwargs.get("input", b""))
        if cmd[:3] == ["id", "-u", "pi"]:
            return CompletedProcess(cmd, 1)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: {
            "systemctl": "/usr/bin/systemctl",
            "id": "/usr/bin/id",
        }.get(name),
    )
    monkeypatch.setattr(
        "rain_bypass.deploy.install_unattended_upgrades",
        lambda **kwargs: None,
    )
    install_autoupdate(
        tmp_path,
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
        skip_confirm=True,
    )
    assert tee_inputs == [render_autoupdate_service(tmp_path, "root").encode()]


def test_install_autoupdate_skips_chmod_off_posix(monkeypatch, tmp_path):
    script = tmp_path / "scripts"
    script.mkdir()
    (script / "auto-update.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    chmod_calls: list[tuple[Path, int]] = []

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(
        "rain_bypass.deploy.os.chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    install_autoupdate(
        tmp_path,
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
        skip_confirm=True,
    )
    assert chmod_calls == []


def test_install_unattended_upgrades_skips_without_apt(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    install_unattended_upgrades()


def test_install_unattended_upgrades_missing_templates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    monkeypatch.setattr("rain_bypass.deploy.DEPLOY_DIR", tmp_path)
    install_unattended_upgrades()


def test_install_unattended_upgrades_without_systemctl(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    install_unattended_upgrades(run_command=fake_run)
    assert not any(cmd[:3] == ["sudo", "systemctl", "enable"] for cmd in calls)


def test_install_unattended_upgrades_installs(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"apt-get", "systemctl"} else None,
    )
    install_unattended_upgrades(run_command=fake_run)
    assert calls[0] == ["sudo", "apt-get", "update", "-qq"]
    assert calls[1][:4] == ["sudo", "apt-get", "install", "-y"]
    assert calls[2][:3] == ["sudo", "tee", "/etc/apt/apt.conf.d/20auto-upgrades"]
    assert calls[3][:3] == [
        "sudo",
        "tee",
        "/etc/apt/apt.conf.d/51unattended-upgrades-rain-bypass",
    ]


def _avahi_resolver(name: str) -> str | None:
    return "/usr/bin/avahi-resolve-host-name" if name == "avahi-resolve-host-name" else None


def test_verify_mdns_avahi_resolve_failure(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", _avahi_resolver)

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 1, stdout="")

    monkeypatch.setattr("rain_bypass.deploy.subprocess.run", fake_run)
    monkeypatch.setattr(
        "rain_bypass.deploy.socket.getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 80))],
    )
    assert verify_mdns("sprinkler", retries=1) is True


def test_verify_mdns_avahi_empty_stdout(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", _avahi_resolver)

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0, stdout="   \n")

    monkeypatch.setattr("rain_bypass.deploy.subprocess.run", fake_run)
    monkeypatch.setattr(
        "rain_bypass.deploy.socket.getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 80))],
    )
    assert verify_mdns("sprinkler", retries=1) is True


def test_ensure_mdns_without_systemctl(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.os.name", "posix")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
    )
    monkeypatch.setattr("rain_bypass.deploy.verify_mdns", lambda _host: True)
    monkeypatch.setattr("rain_bypass.deploy.system_hostname", lambda: "sprinkler")
    assert ensure_mdns(run_command=fake_run) is True
    assert len(calls) == 1


def test_verify_mdns_retries_before_success(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    attempts = {"count": 0}

    def getaddrinfo(*_args, **_kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("temporary mdns failure")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 80))]

    monkeypatch.setattr("rain_bypass.deploy.socket.getaddrinfo", getaddrinfo)
    monkeypatch.setattr("rain_bypass.deploy.time.sleep", lambda _seconds: None)
    assert verify_mdns("sprinkler", retries=2, delay_seconds=0) is True


def test_verify_mdns_zero_retries(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    assert verify_mdns("sprinkler", retries=0) is False


def test_render_dashboard_unit():
    text = render_dashboard_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        "pi",
    )
    assert "rain_bypass serve" in text
    assert "CAP_NET_BIND_SERVICE" in text
    assert "User=pi" in text


def test_system_hostname(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.socket.gethostname", lambda: "Sprinkler")
    assert system_hostname() == "sprinkler"


def test_verify_mdns_with_avahi_resolve(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: (
            "/usr/bin/avahi-resolve-host-name" if name == "avahi-resolve-host-name" else None
        ),
    )

    def fake_run(cmd, **kwargs):
        return CompletedProcess(cmd, 0, stdout="sprinkler.local\t192.168.1.10\n")

    monkeypatch.setattr("rain_bypass.deploy.subprocess.run", fake_run)
    assert verify_mdns("sprinkler") is True


def test_verify_mdns_socket_fallback(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "rain_bypass.deploy.socket.getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", 80))],
    )
    assert verify_mdns("sprinkler", retries=1) is True


def test_verify_mdns_failure(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "rain_bypass.deploy.socket.getaddrinfo",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no mdns")),
    )
    assert verify_mdns("sprinkler", retries=1, delay_seconds=0) is False


def test_ensure_mdns_skips_non_posix(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.os.name", "nt")
    assert ensure_mdns() is False


def test_ensure_mdns_skips_without_apt(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.os.name", "posix")
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    assert ensure_mdns() is False


def test_ensure_mdns_installs_and_verifies(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.os.name", "posix")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: (
            f"/usr/bin/{name}"
            if name in {"apt-get", "systemctl", "avahi-resolve-host-name"}
            else None
        ),
    )
    monkeypatch.setattr("rain_bypass.deploy.subprocess.run", fake_run)
    monkeypatch.setattr("rain_bypass.deploy.verify_mdns", lambda _host: True)
    monkeypatch.setattr("rain_bypass.deploy.system_hostname", lambda: "sprinkler")
    assert ensure_mdns(run_command=fake_run) is True
    assert calls[0][:4] == ["sudo", "apt-get", "install", "-y"]
    assert "avahi-daemon" in calls[0]
    assert calls[1] == ["sudo", "systemctl", "enable", "--now", "avahi-daemon"]


def test_ensure_mdns_verify_warning(monkeypatch):
    monkeypatch.setattr("rain_bypass.deploy.os.name", "posix")
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"apt-get", "systemctl"} else None,
    )
    monkeypatch.setattr("rain_bypass.deploy.verify_mdns", lambda _host: False)
    monkeypatch.setattr("rain_bypass.deploy.system_hostname", lambda: "sprinkler")
    assert ensure_mdns(run_command=fake_run) is False


def test_install_dashboard_unit_skips_without_systemctl(monkeypatch, tmp_path):
    monkeypatch.setattr("rain_bypass.deploy.shutil.which", lambda _name: None)
    install_dashboard_unit(
        tmp_path,
        tmp_path / ".venv/bin/python",
        tmp_path / "settings.toml",
        prompter=FakePrompter(confirms=[True]),
    )


def test_install_dashboard_unit_declined(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: f"/usr/bin/{name}" if name == "systemctl" else None,
    )
    install_dashboard_unit(
        tmp_path,
        tmp_path / ".venv/bin/python",
        tmp_path / "settings.toml",
        prompter=FakePrompter(confirms=[False]),
    )


def test_install_dashboard_unit_installs(monkeypatch, tmp_path):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return CompletedProcess(cmd, 0, stdout=b"")

    monkeypatch.setattr(
        "rain_bypass.deploy.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"systemctl", "id"} else None,
    )
    monkeypatch.setattr("rain_bypass.deploy.ensure_mdns", lambda **_k: True)
    monkeypatch.setattr("rain_bypass.deploy.system_hostname", lambda: "sprinkler")
    install_dashboard_unit(
        tmp_path,
        tmp_path / ".venv/bin/python",
        tmp_path / "settings.toml",
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
    )
    assert any(
        len(cmd) >= 3
        and cmd[0] == "sudo"
        and cmd[1] == "tee"
        and cmd[2].endswith(f"{DASHBOARD_SERVICE_NAME}.service")
        for cmd in calls
    )
    assert ["sudo", "systemctl", "enable", DASHBOARD_SERVICE_NAME] in calls
