from pathlib import Path

import pytest

from rain_bypass.config import load_settings
from rain_bypass.settings_io import (
    EXAMPLE_SETTINGS_PATH,
    load_example_settings,
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
    assert settings.location.latitude == pytest.approx(43.106)


def test_write_settings_round_trip(tmp_path: Path):
    source = load_example_settings(weather={"api_key": "round-trip"})
    path = tmp_path / "settings.toml"
    write_settings(path, source)
    loaded = load_settings(path)
    assert loaded.weather.api_key == "round-trip"
    assert loaded.watering.past_days == source.watering.past_days


def test_load_example_settings_missing_file(tmp_path: Path, monkeypatch):
    missing = tmp_path / "missing.example.toml"
    monkeypatch.setattr("rain_bypass.settings_io.EXAMPLE_SETTINGS_PATH", missing)
    with pytest.raises(FileNotFoundError, match="Example settings not found"):
        load_example_settings()
