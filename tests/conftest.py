from pathlib import Path

import pytest

from rain_bypass.config import load_settings


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
