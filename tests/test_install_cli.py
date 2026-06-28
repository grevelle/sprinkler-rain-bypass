from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import typer
from typer.testing import CliRunner

from rain_bypass.config import Location, load_settings
from rain_bypass.install_cli import (
    SERVICE_NAME,
    TyperPrompter,
    app,
    build_settings,
    install_systemd_unit,
    main,
    prompt_settings,
    render_systemd_unit,
    repo_root,
    run_install,
    validate_api_key,
    validate_zip_code,
    write_settings_secure,
)
from rain_bypass.settings_io import load_example_settings


@pytest.fixture(autouse=True)
def _stub_resolve_location(monkeypatch):
    def fake_resolve(zip_code: str, api_key: str, *, timeout: int = 45) -> Location:
        return Location(
            zip_code=zip_code,
            latitude=43.106,
            longitude=-88.351,
            timezone="America/Chicago",
        )

    monkeypatch.setattr("rain_bypass.install_cli.resolve_location", fake_resolve)


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
        return "test-key"


def test_validate_api_key():
    assert validate_api_key("abc123") == "abc123"
    with pytest.raises(typer.BadParameter, match="cannot be empty"):
        validate_api_key("")
    with pytest.raises(typer.BadParameter, match="invalid characters"):
        validate_api_key('bad"key')


def test_validate_zip_code():
    assert validate_zip_code("53029") == "53029"
    assert validate_zip_code(" 53029-1234 ") == "53029"
    with pytest.raises(typer.BadParameter, match="ZIP code"):
        validate_zip_code("bad")


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
    monkeypatch.setattr(
        "rain_bypass.install_cli.systemd_template_path",
        lambda: tmp_path / "missing.service.in",
    )
    with pytest.raises(FileNotFoundError, match="systemd template not found"):
        render_systemd_unit(
            Path("/opt/app"),
            Path("/opt/app/.venv/bin/python"),
            Path("/opt/app/settings.toml"),
            "root",
        )


def test_prompt_settings_builds_toml(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    prompter = FakePrompter(secrets=["prompted-key"])
    settings = prompt_settings(load_example_settings(), prompter)
    assert settings.weather.api_key == "prompted-key"
    assert settings.location.zip_code == "53029"
    assert settings.gpio.mock is False
    assert settings.location.latitude == pytest.approx(43.106)
    assert prompter.text_calls == [("ZIP code", "53029")]


def test_build_settings_uses_example_defaults(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    settings = build_settings("my-key", "53029")
    assert settings.weather.api_key == "my-key"
    assert settings.location.zip_code == "53029"
    assert settings.gpio.mock is False
    assert settings.watering.check_hour == 4


def test_build_settings_mock_gpio_off_pi(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: False)
    settings = build_settings("my-key", "53029")
    assert settings.gpio.mock is True


def test_build_settings_rejects_empty_api_key():
    with pytest.raises(typer.BadParameter, match="cannot be empty"):
        build_settings("")


def test_write_settings_secure(tmp_path):
    path = tmp_path / "settings.toml"
    settings = load_example_settings(weather={"api_key": "secure-key"})
    write_settings_secure(path, settings)
    assert path.read_text(encoding="utf-8")
    assert load_settings(path).weather.api_key == "secure-key"


def test_install_systemd_unit_skips_without_systemctl(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.shutil.which", lambda _name: None)
    install_systemd_unit(
        Path("/opt/app"),
        Path("/opt/app/.venv/bin/python"),
        Path("/opt/app/settings.toml"),
        prompter=FakePrompter(confirms=[True]),
    )


def test_install_systemd_unit_declined(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
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
        "rain_bypass.install_cli.shutil.which",
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
        "rain_bypass.install_cli.shutil.which",
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


def test_write_settings_secure_chmod(tmp_path, monkeypatch):
    chmod_calls: list[tuple[Path, int]] = []

    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: True)
    monkeypatch.setattr(
        "rain_bypass.install_cli.os.chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    path = tmp_path / "settings.toml"
    write_settings_secure(path, load_example_settings(weather={"api_key": "chmod-key"}))
    assert chmod_calls == [(path, 0o600)]


def test_write_settings_secure_skips_chmod_on_non_posix(tmp_path, monkeypatch):
    chmod_calls: list[tuple[Path, int]] = []

    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: False)
    monkeypatch.setattr(
        "rain_bypass.install_cli.os.chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    path = tmp_path / "settings.toml"
    write_settings_secure(path, load_example_settings(weather={"api_key": "skip-chmod"}))
    assert chmod_calls == []


def test_run_install_skip_api_test(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
        skip_api_test=True,
    )


def test_run_install_pi_zero_notice(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("rain_bypass.install_cli.is_pi_zero", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
        skip_api_test=True,
    )
    assert "Pi Zero detected" in capsys.readouterr().out


def test_run_install_runs_once_successfully(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    monkeypatch.setattr("rain_bypass.install_cli.run", lambda *_args, **_kwargs: None)
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, True, False])
    run_install(tmp_path, prompter=prompter, skip_systemd=True)


def test_run_install_shows_service_status(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    unit = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    original_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == unit:
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
    )


def test_run_install_invokes_systemd(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    invoked: list[bool] = []
    monkeypatch.setattr(
        "rain_bypass.install_cli.install_systemd_unit",
        lambda *args, **kwargs: invoked.append(True),
    )
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, False])
    run_install(tmp_path, prompter=prompter, skip_once=True)
    assert invoked == [True]


def test_run_install_writes_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr(
        "rain_bypass.install_cli.weather_api_smoke",
        lambda _settings: "API OK",
    )
    prompter = FakePrompter(secrets=["live-key"], confirms=[True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
    )
    settings_path = tmp_path / "settings.toml"
    assert settings_path.is_file()
    assert load_settings(settings_path).weather.api_key == "live-key"


def test_run_install_aborts_on_existing_settings(tmp_path):
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text("existing = true\n", encoding="utf-8")
    prompter = FakePrompter(secrets=["key"], confirms=[False])
    with pytest.raises(typer.Exit) as exc:
        run_install(
            tmp_path,
            prompter=prompter,
            skip_systemd=True,
            skip_api_test=True,
            skip_once=True,
        )
    assert exc.value.exit_code == 1


def test_run_install_api_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)

    def _boom(_settings):
        raise RuntimeError("api down")

    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", _boom)
    prompter = FakePrompter(secrets=["key"], confirms=[True])
    with pytest.raises(typer.Exit) as exc:
        run_install(tmp_path, prompter=prompter, skip_systemd=True, skip_once=True)
    assert exc.value.exit_code == 1


def test_run_install_once_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")

    def _fail(*_args, **_kwargs):
        raise RuntimeError("cycle failed")

    monkeypatch.setattr("rain_bypass.install_cli.run", _fail)
    prompter = FakePrompter(secrets=["key"], confirms=[True, True, False])
    with pytest.raises(typer.Exit) as exc:
        run_install(tmp_path, prompter=prompter, skip_systemd=True)
    assert exc.value.exit_code == 1


def test_repo_root():
    assert repo_root().name == "sprinkler-rain-bypass"


def test_cli_re_raises_exit(monkeypatch):
    def _exit(**kwargs):
        raise typer.Exit(2)

    monkeypatch.setattr("rain_bypass.install_cli.run_install", _exit)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 2


def test_main_invokes_app(monkeypatch):
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr("rain_bypass.install_cli.app", fake_app)
    main()
    assert called is True


def test_cli_bad_parameter(monkeypatch):
    def _bad(**kwargs):
        raise typer.BadParameter("bad")

    monkeypatch.setattr("rain_bypass.install_cli.run_install", _bad)
    result = CliRunner().invoke(app, [])
    assert result.exit_code != 0


def test_cli_install(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.run_install", lambda **kwargs: None)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 0


def test_cli_keyboard_interrupt(monkeypatch):
    def _interrupt(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.install_cli.run_install", _interrupt)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 130


def test_typer_prompter_delegates(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.typer.prompt", lambda label, **kwargs: "value")
    monkeypatch.setattr("rain_bypass.install_cli.typer.confirm", lambda label, **kwargs: True)
    prompter = TyperPrompter()
    assert prompter.text("label", default="d") == "value"
    assert prompter.secret("label") == "value"
    assert prompter.confirm("label", default=False) is True
