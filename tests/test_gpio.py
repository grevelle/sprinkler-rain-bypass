from __future__ import annotations

from unittest.mock import patch

import pytest

from rain_bypass.config import Gpio
from rain_bypass.gpio import MockPins, PiPins, watering_pins


def test_mock_pins() -> None:
    MockPins().apply(True)
    MockPins().apply(False)


def test_watering_pins_mock() -> None:
    gpio = Gpio(relay=1, watering_enabled_led=2, watering_disabled_led=3, mock=True)
    with watering_pins(gpio) as driver:
        driver.apply(True)


def test_pi_pins(fake_output_device, pi_gpio) -> None:
    driver = PiPins(pi_gpio)
    assert len(fake_output_device) == 3
    relay, green, red = fake_output_device
    assert relay.pin == 25
    assert green.pin == 4
    assert red.pin == 27
    # __init__ calls apply(False): block watering
    assert relay.value is True
    assert green.value is False
    assert red.value is True
    driver.apply(True)
    assert relay.value is False
    assert green.value is True
    assert red.value is False
    driver.apply(False)
    assert relay.value is True
    driver.cleanup()
    assert all(device.closed for device in fake_output_device)


def test_watering_pins_requires_gpio_extra(pi_gpio) -> None:
    with (
        patch(
            "rain_bypass.gpio._import_gpiozero",
            side_effect=ImportError("gpiozero not installed"),
        ),
        pytest.raises(RuntimeError, match=r"gpio\.mock"),
        watering_pins(pi_gpio),
    ):
        pass


def test_watering_pins_hold_state_without_cleanup(fake_output_device, pi_gpio) -> None:
    with watering_pins(pi_gpio) as driver:
        driver.apply(False)
    assert all(not device.closed for device in fake_output_device)


def test_pipins_cleanup_closes_devices(fake_output_device, pi_gpio) -> None:
    driver = PiPins(pi_gpio)
    driver.cleanup()
    assert all(device.closed for device in fake_output_device)
