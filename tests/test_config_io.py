from pathlib import Path

import pytest
from pydantic import ValidationError

from rain_bypass.config import (
    EXAMPLE_SETTINGS_PATH,
    Balance,
    BalanceMonth,
    ConfigError,
    Location,
    State,
    Watering,
    load_example_settings,
    load_settings,
    validate_model,
    write_settings,
)


def test_example_settings_path():
    assert EXAMPLE_SETTINGS_PATH.name == "settings.example.toml"
    assert EXAMPLE_SETTINGS_PATH.is_file()


def test_load_example_settings_applies_overrides():
    settings = load_example_settings(
        weather={"api_key": "override-key"},
        gpio={"mock": True},
    )
    assert settings.weather.api_key == "override-key"
    assert settings.gpio.mock is True
    assert settings.location.zip_code == "53029"
    assert settings.location.latitude == pytest.approx(43.106)


def test_location_rejects_invalid_zip():
    with pytest.raises(ValidationError):
        Location(
            zip_code="abcde",
            latitude=43.106,
            longitude=-88.351,
            timezone="America/Chicago",
        )


def test_validate_model_zip_rejects_invalid():
    with pytest.raises(ConfigError):
        validate_model(
            Location,
            {"zip_code": "bad", "latitude": 0, "longitude": 0, "timezone": "UTC"},
        )


def test_validate_model_zip_accepts_valid():
    location = validate_model(
        Location,
        {"zip_code": "53029", "latitude": 0, "longitude": 0, "timezone": "UTC"},
    )
    assert location.zip_code == "53029"


def test_validate_model_inches_per_cycle():
    balance = validate_model(Balance, {"inches_per_cycle": "0.3"})
    assert balance.inches_per_cycle == pytest.approx(0.3)


def test_validate_model_check_time():
    watering = validate_model(Watering, {"check_hour": 4, "check_minute": 30})
    assert watering.check_hour == 4
    assert watering.check_minute == 30


def test_location_normalizes_zip_plus_four():
    location = Location(
        zip_code="53029-1234",
        latitude=43.106,
        longitude=-88.351,
        timezone="America/Chicago",
    )
    assert location.zip_code == "53029"


def test_balance_monthly_validator_branches():
    empty = Balance.model_validate({"inches_per_cycle": 0.33, "monthly": {}})
    assert empty.monthly[6].target_inches_per_month == pytest.approx(6.5)
    non_dict = Balance.model_validate({"inches_per_cycle": 0.33, "monthly": "ignored"})
    assert non_dict.monthly[7].target_inches_per_month == pytest.approx(5.0)
    preset = Balance(
        inches_per_cycle=0.33,
        monthly={5: BalanceMonth(target_inches_per_month=9.0)},
    )
    assert preset.monthly[5].target_inches_per_month == pytest.approx(9.0)


def test_state_load_rejects_non_object(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError):
        State.load(path)


def test_load_example_settings_monthly_override():
    settings = load_example_settings(
        weather={"api_key": "override-key"},
        balance={"monthly": {"6": {"target_inches_per_month": 8.0}}},
    )
    assert settings.balance.monthly[6].target_inches_per_month == pytest.approx(8.0)
    assert settings.balance.monthly[7].target_inches_per_month == pytest.approx(5.0)


def test_write_settings_round_trip(tmp_path: Path):
    source = load_example_settings(weather={"api_key": "round-trip"})
    path = tmp_path / "settings.toml"
    write_settings(path, source)
    loaded = load_settings(path)
    assert loaded.weather.api_key == "round-trip"
    assert loaded.balance.inches_per_cycle == source.balance.inches_per_cycle
    assert loaded.watering.event_lookback_days == source.watering.event_lookback_days


def test_write_settings_history_path(tmp_path: Path):
    custom = tmp_path / "logs" / "water.jsonl"
    source = load_example_settings(
        weather={"api_key": "history-path"},
        runtime={"history_path": custom},
    )
    path = tmp_path / "settings.toml"
    write_settings(path, source)
    loaded = load_settings(path)
    assert loaded.runtime.history_path == custom


def test_web_defaults():
    settings = load_example_settings(weather={"api_key": "web-test"})
    assert settings.web.host == "0.0.0.0"
    assert settings.web.port == 80


def test_load_example_settings_missing_file(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing.example.toml"
    monkeypatch.setattr("rain_bypass.config.EXAMPLE_SETTINGS_PATH", missing)
    with pytest.raises(FileNotFoundError, match="Example settings not found"):
        load_example_settings()


def test_write_settings_chmod_on_posix(tmp_path: Path, monkeypatch):
    import os

    import rain_bypass.config as config_module

    chmod_calls: list[tuple[object, int]] = []
    monkeypatch.setattr(config_module.os, "name", "posix")
    monkeypatch.setattr(
        config_module.os,
        "chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    path = tmp_path / "settings.toml"
    write_settings(path, load_example_settings(weather={"api_key": "chmod-key01"}))
    assert len(chmod_calls) == 1
    assert os.path.normcase(os.fspath(chmod_calls[0][0])) == os.path.normcase(os.fspath(path))
    assert chmod_calls[0][1] == 0o600


def test_write_settings_skips_chmod_off_posix(tmp_path: Path, monkeypatch):
    import rain_bypass.config as config_module

    chmod_calls: list[tuple[object, int]] = []
    monkeypatch.setattr(config_module.os, "name", "nt")
    monkeypatch.setattr(
        config_module.os,
        "chmod",
        lambda path, mode: chmod_calls.append((path, mode)),
    )
    path = tmp_path / "settings.toml"
    write_settings(path, load_example_settings(weather={"api_key": "skip-chmod-key"}))
    assert chmod_calls == []
