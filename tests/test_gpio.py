from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from rain_bypass.config import Gpio
from rain_bypass.gpio import MockPins, PiPins, _driver_for, watering_pins


def test_mock_pins():
    MockPins().apply(True)
    MockPins().apply(False)


def test_watering_pins_mock():
    gpio = Gpio(relay=1, watering_enabled_led=2, watering_disabled_led=3, mock=True)
    with watering_pins(gpio) as driver:
        driver.apply(True)


def test_pi_pins():
    gpio = Gpio(relay=25, watering_enabled_led=4, watering_disabled_led=27, mock=False)
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

    with patch.dict("sys.modules", {"RPi": fake_rpi, "RPi.GPIO": fake_gpio}):
        driver = PiPins(gpio)
        driver.apply(True)
        driver.apply(False)
        driver.cleanup()
        assert fake_gpio.setup.call_count == 3
        assert fake_gpio.output.call_count == 6


def test_driver_for_requires_gpio_extra():
    gpio = Gpio(relay=1, watering_enabled_led=2, watering_disabled_led=3, mock=False)
    with pytest.raises(RuntimeError, match="gpio.mock"):
        _driver_for(gpio)


def test_watering_pins_pi_cleanup():
    gpio = Gpio(relay=25, watering_enabled_led=4, watering_disabled_led=27, mock=False)
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

    with patch.dict("sys.modules", {"RPi": fake_rpi, "RPi.GPIO": fake_gpio}):
        with watering_pins(gpio) as driver:
            driver.apply(False)
        fake_gpio.cleanup.assert_called_once()
