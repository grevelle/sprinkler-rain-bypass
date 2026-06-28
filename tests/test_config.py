import pytest

from rain_bypass.config import ConfigError, FailMode, load_settings


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.runtime.weather_timeout_seconds == 15


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")
