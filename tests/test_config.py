import pytest

from rain_bypass.config import ConfigError, FailMode, load_settings


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.watering.interval_seconds == pytest.approx(86400 / 2)
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.runtime.weather_timeout_seconds == 15


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


def test_invalid_toml(settings_path):
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            "latitude = 41.8781", "latitude = not_a_number"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_invalid_settings(settings_path):
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace("past_days = 7", "past_days = 0"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_state_load_missing(tmp_path):
    from rain_bypass.config import State

    assert State.load(tmp_path / "missing.json") == State()
