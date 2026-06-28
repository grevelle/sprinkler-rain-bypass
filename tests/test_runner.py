from __future__ import annotations

from pathlib import Path

import pytest

from rain_bypass.config import load_settings
from rain_bypass.gpio import MockGpioController
from rain_bypass.models import RuntimeState
from rain_bypass.runner import RainBypassRunner
from rain_bypass.state import load_state, save_state


@pytest.fixture
def example_config(tmp_path: Path) -> Path:
    config = tmp_path / "settings.toml"
    state = tmp_path / "state.json"
    config.write_text(
        f"""
[location]
latitude = 41.8781
longitude = -87.6298
timezone = "UTC"

[watering]
inches_required = 0.6
past_days = 3
updates_per_day = 1

[season]
start_month = 12
start_day = 1
end_month = 12
end_day = 31

[weather]
provider = "open_meteo"

[gpio]
relay = 25
watering_enabled_led = 4
watering_disabled_led = 27
mock = true

[runtime]
state_path = "{state.as_posix()}"
fail_mode = "disable_watering"
log_level = "INFO"
""".strip(),
        encoding="utf-8",
    )
    return config


def test_runner_persists_state(example_config: Path) -> None:
    settings = load_settings(example_config)
    runner = RainBypassRunner(settings, gpio=MockGpioController())
    runner.run_once()

    state = load_state(settings.runtime.state_path)
    assert state.watering_required is False
    assert state.last_weather_update is not None


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = RuntimeState(
        last_weather_update=123.456,
        watering_required=True,
        rainfall_inches=0.25,
        last_error=None,
    )
    save_state(path, original)
    loaded = load_state(path)
    assert loaded.watering_required is True
    assert loaded.rainfall_inches == pytest.approx(0.25)
