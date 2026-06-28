import pytest

from rain_bypass.config import ConfigError, FailMode, Provider, load_settings

SETTINGS = """
[location]
latitude = 41.8781
longitude = -87.6298
timezone = "UTC"

[watering]
inches_required = 0.6
past_days = 7
updates_per_day = 2

[season]
start_month = 3
start_day = 19
end_month = 9
end_day = 12

[weather]
provider = "open_meteo"

[gpio]
relay = 25
watering_enabled_led = 4
watering_disabled_led = 27
mock = true

[runtime]
state_path = "state.json"
fail_mode = "disable_watering"
log_level = "INFO"
"""


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.weather.provider is Provider.OPEN_METEO
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


def test_visual_crossing_requires_key(tmp_path):
    path = tmp_path / "settings.toml"
    path.write_text(
        SETTINGS.replace('provider = "open_meteo"', 'provider = "visual_crossing"').strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(path)
