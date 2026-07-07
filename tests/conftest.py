import sys
from pathlib import Path
from types import ModuleType

import pytest

from rain_bypass.config import Gpio, load_example_settings, load_settings, write_settings

TEST_SETTINGS_OVERRIDES = {
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
def fake_output_device(monkeypatch):
    devices: list[object] = []

    class FakeDevice:
        def __init__(self, pin: int, initial_value: bool = False) -> None:
            self.pin = pin
            self.value = initial_value
            self.on_calls = 0
            self.off_calls = 0
            self.closed = False
            devices.append(self)

        def on(self) -> None:
            self.on_calls += 1
            self.value = True

        def off(self) -> None:
            self.off_calls += 1
            self.value = False

        def close(self) -> None:
            self.closed = True

    fake_gpiozero = ModuleType("gpiozero")
    fake_gpiozero.OutputDevice = FakeDevice
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)
    return devices


@pytest.fixture
def pi_gpio():
    return Gpio(relay=25, watering_enabled_led=4, watering_disabled_led=27, mock=False)
