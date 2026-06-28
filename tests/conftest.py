from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from rain_bypass.config import Gpio, load_settings
from rain_bypass.settings_io import load_example_settings, write_settings

TEST_SETTINGS_OVERRIDES = {
    "watering": {"near_term_hours": 0, "updates_per_day": 2},
    "weather": {"api_key": "test-key"},
    "gpio": {"mock": True},
    "runtime": {"weather_timeout_seconds": 15},
}


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "settings.toml"
    write_settings(path, load_example_settings(**TEST_SETTINGS_OVERRIDES))
    return path


@pytest.fixture
def settings(settings_path: Path):
    return load_settings(settings_path)


@pytest.fixture
def fake_rpi():
    fake_gpio = ModuleType("RPi.GPIO")
    fake_gpio.BCM = "BCM"
    fake_gpio.OUT = "OUT"
    fake_gpio.HIGH = 1
    fake_gpio.LOW = 0
    fake_gpio.setmode = MagicMock()
    fake_gpio.setwarnings = MagicMock()
    fake_gpio.setup = MagicMock()
    fake_gpio.output = MagicMock()
    fake_gpio.cleanup = MagicMock()
    fake_rpi = ModuleType("RPi")
    fake_rpi.GPIO = fake_gpio
    return fake_rpi, fake_gpio


@pytest.fixture
def pi_gpio():
    return Gpio(relay=25, watering_enabled_led=4, watering_disabled_led=27, mock=False)
