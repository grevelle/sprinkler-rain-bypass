from pathlib import Path

import pytest

from rain_bypass.config import ConfigError, FailMode, Provider, load_settings

SETTINGS = """
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
"""


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "settings.toml"
    path.write_text(SETTINGS.strip(), encoding="utf-8")
    return path


@pytest.fixture
def settings(settings_path: Path):
    return load_settings(settings_path)
