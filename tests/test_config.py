from __future__ import annotations

from pathlib import Path

import pytest

from rain_bypass.config import ConfigError, load_settings
from rain_bypass.models import FailMode, WeatherProviderName


@pytest.fixture
def example_config(tmp_path: Path) -> Path:
    config = tmp_path / "settings.toml"
    config.write_text(
        """
[location]
latitude = 41.8781
longitude = -87.6298
timezone = "America/Chicago"

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
request_timeout_seconds = 15

[gpio]
relay = 25
watering_enabled_led = 4
watering_disabled_led = 27
mock = true

[runtime]
state_path = "state.json"
fail_mode = "disable_watering"
log_level = "INFO"
""".strip(),
        encoding="utf-8",
    )
    return config


def test_load_settings(example_config: Path) -> None:
    settings = load_settings(example_config)
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.weather.provider is WeatherProviderName.OPEN_METEO
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.gpio.mock is True


def test_missing_config_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Config file not found"):
        load_settings(tmp_path / "missing.toml")


def test_visual_crossing_requires_api_key(tmp_path: Path) -> None:
    config = tmp_path / "settings.toml"
    config.write_text(
        """
[location]
latitude = 1
longitude = 2
timezone = "UTC"

[watering]
inches_required = 0.5
past_days = 3
updates_per_day = 1

[season]
start_month = 1
start_day = 1
end_month = 12
end_day = 31

[weather]
provider = "visual_crossing"

[gpio]
relay = 1
watering_enabled_led = 2
watering_disabled_led = 3
mock = true

[runtime]
state_path = "state.json"
fail_mode = "disable_watering"
log_level = "INFO"
""".strip(),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="visual_crossing_api_key"):
        load_settings(config)
