from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from rain_bypass.deploy import (
    AUTO_UPDATE_SERVICE_NAME,
    install_autoupdate,
    install_systemd_unit,
    install_unattended_upgrades,
    render_autoupdate_service,
    render_autoupdate_timer,
    render_systemd_unit,
)


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
