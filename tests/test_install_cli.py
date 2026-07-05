from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import typer
from typer.testing import CliRunner

from rain_bypass.config import Location, load_settings
from rain_bypass.exceptions import WeatherError
from rain_bypass.install_cli import (
    SERVICE_NAME,
    PromptDefaults,
    TyperPrompter,
    app,
    build_settings,
    ensure_state_writable,
    install_systemd_unit,
    load_existing_settings_fields,
    load_prompt_defaults,
    load_settings_base,
    main,
    prompt_settings,
    prompt_watering_profile,
    render_systemd_unit,
    repo_root,
    resolve_state_path,
    restart_service_if_installed,
    run_api_test,
    run_configure,
    run_install,
    validate_api_key,
    validate_check_time,
    validate_inches_per_cycle,
    validate_zip_code,
    write_and_validate_settings,
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
        return "test-key-001"


def test_load_existing_settings_fields_missing(tmp_path: Path):
    assert load_existing_settings_fields(tmp_path / "settings.toml") == (None, "53029")


def test_load_existing_settings_fields_ok(tmp_path: Path):
    path = tmp_path / "settings.toml"
    write_settings_secure(path, load_example_settings(weather={"api_key": "existing-key1"}))
    key, zip_code = load_existing_settings_fields(path)
    assert key == "existing-key1"
    assert zip_code == "53029"


def test_load_existing_settings_fields_invalid(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text("not valid toml", encoding="utf-8")
    assert load_existing_settings_fields(path) == (None, "53029")


def test_write_and_validate_settings(tmp_path: Path):
    path = tmp_path / "settings.toml"
    settings = load_example_settings(weather={"api_key": "write-test01"})
    write_and_validate_settings(path, settings)
    assert load_settings(path).weather.api_key == "write-test01"


def test_run_api_test_success(tmp_path: Path, monkeypatch):
    path = tmp_path / "settings.toml"
    write_settings_secure(path, load_example_settings(weather={"api_key": "smoke-key01"}))
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    run_api_test(path)


def test_run_api_test_failure(tmp_path: Path, monkeypatch):
    path = tmp_path / "settings.toml"
    write_settings_secure(path, load_example_settings(weather={"api_key": "smoke-key01"}))

    def _boom(_settings):
        raise RuntimeError("api down")

    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", _boom)
    with pytest.raises(typer.Exit) as exc:
        run_api_test(path)
    assert exc.value.exit_code == 1


def test_restart_service_if_installed_skips_without_systemctl(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.shutil.which", lambda _name: None)
    restart_service_if_installed()


def test_restart_service_if_installed_skips_without_unit(monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    restart_service_if_installed()


def test_restart_service_if_installed_restarts(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[bytes]:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    unit = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    original_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == unit:
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    restart_service_if_installed(
        prompter=FakePrompter(confirms=[True]),
        run_command=fake_run,
    )
    assert calls == [["sudo", "systemctl", "restart", SERVICE_NAME]]


def test_restart_service_if_installed_declined(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[bytes]:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    unit = Path(f"/etc/systemd/system/{SERVICE_NAME}.service")
    original_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == unit:
            return True
        return original_is_file(self)

    monkeypatch.setattr(Path, "is_file", is_file)
    restart_service_if_installed(
        prompter=FakePrompter(confirms=[False]),
        run_command=fake_run,
    )
    assert calls == []


def test_run_configure_preserves_other_sections(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    monkeypatch.setattr(
        "rain_bypass.install_cli.restart_service_if_installed",
        lambda **_kwargs: None,
    )
    settings_path = tmp_path / "settings.toml"
    write_settings_secure(
        settings_path,
        load_example_settings(
            weather={"api_key": "existing-key1"},
            sewer={"start_day": 20},
            balance={"inches_per_cycle": 0.33},
        ),
    )
    prompter = FakePrompter(confirms=[True], answers=["53029", "0.33", "05:00"])
    run_configure(tmp_path, prompter=prompter, skip_service_restart=True)
    settings = load_settings(settings_path)
    assert settings.weather.api_key == "existing-key1"
    assert settings.sewer.start_day == 20
    assert settings.balance.inches_per_cycle == pytest.approx(0.33)
    assert settings.watering.check_hour == 5


def test_run_configure_writes_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    monkeypatch.setattr(
        "rain_bypass.install_cli.restart_service_if_installed",
        lambda **_kwargs: None,
    )
    prompter = FakePrompter(secrets=["live-key-001"])
    run_configure(tmp_path, prompter=prompter, skip_service_restart=True)
    settings_path = tmp_path / "settings.toml"
    assert load_settings(settings_path).weather.api_key == "live-key-001"


def test_run_configure_keeps_existing_key(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    monkeypatch.setattr(
        "rain_bypass.install_cli.restart_service_if_installed",
        lambda **_kwargs: None,
    )
    settings_path = tmp_path / "settings.toml"
    write_settings_secure(
        settings_path,
        load_example_settings(weather={"api_key": "existing-key1"}),
    )
    prompter = FakePrompter(confirms=[True])
    run_configure(tmp_path, prompter=prompter, skip_service_restart=True)
    assert load_settings(settings_path).weather.api_key == "existing-key1"


def test_run_configure_skips_api_test(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)

    def _boom(_settings):
        raise RuntimeError("should not run")

    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", _boom)
    monkeypatch.setattr(
        "rain_bypass.install_cli.restart_service_if_installed",
        lambda **_kwargs: None,
    )
    prompter = FakePrompter(secrets=["live-key-001"])
    run_configure(
        tmp_path,
        prompter=prompter,
        skip_api_test=True,
        skip_service_restart=True,
    )


def test_restart_service_if_installed_skip_flag(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[bytes]:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    restart_service_if_installed(run_command=fake_run, skip=True)
    assert calls == []


def test_cli_configure(monkeypatch):
    called: dict[str, bool] = {}

    def fake_configure(**kwargs):
        called.update(kwargs)

    monkeypatch.setattr("rain_bypass.install_cli.run_configure", fake_configure)
    result = CliRunner().invoke(app, ["configure", "--skip-api-test", "--no-restart"])
    assert result.exit_code == 0
    assert called == {"skip_api_test": True, "skip_service_restart": True}


def test_validate_api_key():
    assert validate_api_key("abcdef1234") == "abcdef1234"
    with pytest.raises(typer.BadParameter, match="cannot be empty"):
        validate_api_key("")
    with pytest.raises(typer.BadParameter, match="too short"):
        validate_api_key("abc123")
    with pytest.raises(typer.BadParameter, match="invalid characters"):
        validate_api_key('abcdefghij"key')


def test_validate_zip_code():
    assert validate_zip_code("53029") == "53029"
    assert validate_zip_code(" 53029-1234 ") == "53029"
    with pytest.raises(typer.BadParameter, match="ZIP code"):
        validate_zip_code("bad")


def test_validate_inches_per_cycle():
    assert validate_inches_per_cycle("0.3") == pytest.approx(0.3)
    assert validate_inches_per_cycle(" 0.33 ") == pytest.approx(0.33)
    with pytest.raises(typer.BadParameter, match="number"):
        validate_inches_per_cycle("abc")
    with pytest.raises(typer.BadParameter, match="greater than 0"):
        validate_inches_per_cycle("0")


def test_validate_check_time():
    assert validate_check_time("04:30") == (4, 30)
    assert validate_check_time("5:00") == (5, 0)
    with pytest.raises(typer.BadParameter, match="HH:MM"):
        validate_check_time("430")
    with pytest.raises(typer.BadParameter, match="0-23"):
        validate_check_time("24:00")


def test_load_prompt_defaults_missing(tmp_path: Path):
    key, profile = load_prompt_defaults(tmp_path / "settings.toml")
    assert key is None
    assert profile == PromptDefaults()


def test_load_prompt_defaults_ok(tmp_path: Path):
    path = tmp_path / "settings.toml"
    write_settings_secure(
        path,
        load_example_settings(
            weather={"api_key": "existing-key1"},
            balance={"inches_per_cycle": 0.33},
            watering={"check_hour": 5, "check_minute": 0},
        ),
    )
    key, profile = load_prompt_defaults(path)
    assert key == "existing-key1"
    assert profile.zip_code == "53029"
    assert profile.inches_per_cycle == pytest.approx(0.33)
    assert profile.check_hour == 5
    assert profile.check_minute == 0


def test_load_settings_base_uses_existing(tmp_path: Path):
    path = tmp_path / "settings.toml"
    custom = load_example_settings(sewer={"start_day": 20})
    write_settings_secure(path, custom)
    loaded = load_settings_base(path)
    assert loaded.sewer.start_day == 20


def test_load_settings_base_falls_back_to_example(tmp_path: Path):
    path = tmp_path / "settings.toml"
    path.write_text("not valid", encoding="utf-8")
    loaded = load_settings_base(path)
    assert loaded.balance.inches_per_cycle == pytest.approx(0.3)


def test_resolve_state_path_relative(tmp_path: Path):
    settings = load_example_settings(weather={"api_key": "k"})
    assert resolve_state_path(tmp_path, settings) == tmp_path / "state.json"


def test_resolve_state_path_absolute(tmp_path: Path):
    absolute = tmp_path / "custom-state.json"
    settings = load_example_settings(
        weather={"api_key": "k"},
        runtime={"state_path": str(absolute), "log_level": "INFO"},
    )
    assert resolve_state_path(tmp_path, settings) == absolute


def test_ensure_state_writable_noop_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: True)
    settings = load_example_settings(weather={"api_key": "k"})
    ensure_state_writable(tmp_path, settings, run_command=pytest.fail)


def test_ensure_state_writable_noop_on_non_posix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: False)
    settings = load_example_settings(weather={"api_key": "k"})
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    ensure_state_writable(tmp_path, settings, run_command=pytest.fail)


def test_ensure_state_writable_chowns_when_unwritable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: True)
    monkeypatch.setattr(
        "rain_bypass.install_cli.shutil.which",
        lambda name: "/usr/bin/sudo" if name == "sudo" else None,
    )
    settings = load_example_settings(weather={"api_key": "k"})
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[bytes]:
        calls.append(cmd)
        return CompletedProcess(cmd, 0)

    real_access = os.access

    def deny_write(path: os.PathLike[str] | str, mode: int) -> bool:
        if mode == os.W_OK and Path(path) == state_path:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", deny_write)
    ensure_state_writable(tmp_path, settings, run_command=fake_run)
    assert calls[0][0:2] == ["sudo", "chown"]
    assert str(state_path) in calls[0][-1]


def test_ensure_state_writable_exits_without_sudo(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli._is_posix", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.shutil.which", lambda _name: None)
    settings = load_example_settings(weather={"api_key": "k"})
    state_path = tmp_path / "state.json"
    state_path.write_text("{}", encoding="utf-8")

    def deny_write(_path: os.PathLike[str] | str, mode: int) -> bool:
        return mode != os.W_OK

    monkeypatch.setattr(os, "access", deny_write)
    with pytest.raises(typer.Exit):
        ensure_state_writable(tmp_path, settings)


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
    prompter = FakePrompter(secrets=["prompted-key1"])
    settings = prompt_settings(load_example_settings(), prompter)
    assert settings.weather.api_key == "prompted-key1"
    assert settings.location.zip_code == "53029"
    assert settings.gpio.mock is False
    assert settings.location.latitude == pytest.approx(43.106)
    assert prompter.text_calls == [
        ("ZIP code", "53029"),
        ("Inches per cycle (30 min/zone ≈ 0.3)", "0.3"),
        ("Daily check time before irrigation (HH:MM, 24h)", "00:00"),
    ]


def test_prompt_watering_profile_custom_values():
    profile = PromptDefaults(inches_per_cycle=0.33, check_hour=5, check_minute=15)
    prompter = FakePrompter(answers=["0.4", "06:00"])
    inches, hour, minute = prompt_watering_profile(prompter, profile)
    assert inches == pytest.approx(0.4)
    assert hour == 6
    assert minute == 0


def test_build_settings_preserves_base_sections(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    base = load_example_settings(sewer={"start_day": 20})
    settings = build_settings(
        "my-test-key1",
        "53029",
        base=base,
        inches_per_cycle=0.33,
        check_hour=5,
        check_minute=0,
    )
    assert settings.sewer.start_day == 20
    assert settings.balance.inches_per_cycle == pytest.approx(0.33)
    assert settings.watering.check_hour == 5


def test_prompt_settings_retries_on_bad_api_key(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)

    def fake_resolve(_zip_code: str, api_key: str, **_kwargs) -> Location:
        if api_key == "bad-key-001":
            raise WeatherError("visual crossing unauthorized; check api_key")
        return Location(
            zip_code="53029",
            latitude=43.106,
            longitude=-88.351,
            timezone="America/Chicago",
        )

    monkeypatch.setattr("rain_bypass.install_cli.resolve_location", fake_resolve)
    prompter = FakePrompter(secrets=["bad-key-001", "good-key-001"])
    settings = prompt_settings(load_example_settings(), prompter)
    assert settings.weather.api_key == "good-key-001"


def test_prompt_settings_keeps_existing_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    existing = load_example_settings(weather={"api_key": "existing-key1"})
    prompter = FakePrompter(confirms=[True])
    settings = prompt_settings(
        load_example_settings(),
        prompter,
        existing_key=existing.weather.api_key,
    )
    assert settings.weather.api_key == "existing-key1"
    assert prompter.secrets == []


def test_run_install_keeps_existing_api_key(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")
    settings_path = tmp_path / "settings.toml"
    write_settings_secure(
        settings_path,
        load_example_settings(weather={"api_key": "existing-key1"}),
    )
    prompter = FakePrompter(confirms=[True, True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
    )
    assert load_settings(settings_path).weather.api_key == "existing-key1"


def test_build_settings_uses_example_defaults(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    settings = build_settings(
        "my-test-key1",
        "53029",
        inches_per_cycle=0.3,
        check_hour=0,
        check_minute=0,
    )
    assert settings.weather.api_key == "my-test-key1"
    assert settings.location.zip_code == "53029"
    assert settings.gpio.mock is False
    assert settings.balance.inches_per_cycle == pytest.approx(0.3)
    assert settings.watering.check_hour == 0


def test_build_settings_mock_gpio_off_pi(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: False)
    settings = build_settings("my-test-key1", "53029")
    assert settings.gpio.mock is True


def test_build_settings_maps_weather_error(monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)

    def _fail(*_args, **_kwargs):
        raise WeatherError("visual crossing unauthorized; check api_key")

    monkeypatch.setattr("rain_bypass.install_cli.resolve_location", _fail)
    with pytest.raises(typer.BadParameter, match="unauthorized"):
        build_settings("my-test-key1", "53029")


def test_build_settings_rejects_empty_api_key():
    with pytest.raises(typer.BadParameter, match="cannot be empty"):
        build_settings("")


def test_write_settings_secure(tmp_path):
    path = tmp_path / "settings.toml"
    settings = load_example_settings(weather={"api_key": "secure-key1"})
    write_settings_secure(path, settings)
    assert path.read_text(encoding="utf-8")
    assert load_settings(path).weather.api_key == "secure-key1"


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
    write_settings_secure(path, load_example_settings(weather={"api_key": "chmod-key01"}))
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
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, False, False])
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
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, False, False])
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
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, True, False])
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
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, False, False])
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
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, False])
    run_install(tmp_path, prompter=prompter, skip_once=True)
    assert invoked == [True]


def test_run_install_writes_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr(
        "rain_bypass.install_cli.weather_api_smoke",
        lambda _settings: "API OK",
    )
    prompter = FakePrompter(secrets=["live-key-001"], confirms=[True, False, False])
    run_install(
        tmp_path,
        prompter=prompter,
        skip_systemd=True,
        skip_once=True,
    )
    settings_path = tmp_path / "settings.toml"
    assert settings_path.is_file()
    assert load_settings(settings_path).weather.api_key == "live-key-001"


def test_run_install_aborts_on_existing_settings(tmp_path):
    settings_path = tmp_path / "settings.toml"
    settings_path.write_text("existing = true\n", encoding="utf-8")
    prompter = FakePrompter(secrets=["valid-key01"], confirms=[False])
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
    prompter = FakePrompter(secrets=["valid-key01"], confirms=[True])
    with pytest.raises(typer.Exit) as exc:
        run_install(tmp_path, prompter=prompter, skip_systemd=True, skip_once=True)
    assert exc.value.exit_code == 1


def test_run_install_once_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.install_cli.is_raspberry_pi", lambda: True)
    monkeypatch.setattr("rain_bypass.install_cli.weather_api_smoke", lambda _s: "API OK")

    def _fail(*_args, **_kwargs):
        raise RuntimeError("cycle failed")

    monkeypatch.setattr("rain_bypass.install_cli.run", _fail)
    prompter = FakePrompter(secrets=["valid-key01"], confirms=[True, True, False])
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
    assert result.exit_code == 1


def test_cli_weather_error(monkeypatch):
    def _weather(**kwargs):
        raise WeatherError("visual crossing unauthorized; check api_key")

    monkeypatch.setattr("rain_bypass.install_cli.run_install", _weather)
    result = CliRunner().invoke(app, [])
    assert result.exit_code == 1
    assert "unauthorized" in result.output


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
