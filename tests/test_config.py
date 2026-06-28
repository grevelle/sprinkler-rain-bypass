import pytest

from rain_bypass.config import ConfigError, FailMode, Provider, load_settings


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.weather.provider is Provider.OPEN_METEO
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


def test_visual_crossing_requires_key(settings_path):
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'provider = "open_meteo"', 'provider = "visual_crossing"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(settings_path)
