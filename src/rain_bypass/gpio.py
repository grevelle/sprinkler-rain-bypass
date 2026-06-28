from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator, Protocol

from rain_bypass.config import Gpio

logger = logging.getLogger(__name__)


class PinDriver(Protocol):
    def setup(self, gpio: Gpio) -> None: ...
    def apply(self, watering_required: bool) -> None: ...
    def cleanup(self) -> None: ...


class MockPins:
    def setup(self, gpio: Gpio) -> None:
        logger.info("mock gpio relay=%s green=%s red=%s", gpio.relay, gpio.watering_enabled_led, gpio.watering_disabled_led)

    def apply(self, watering_required: bool) -> None:
        state = "enabled" if watering_required else "disabled"
        logger.info("mock gpio watering %s", state)

    def cleanup(self) -> None:
        logger.info("mock gpio cleanup")


class PiPins:
    def setup(self, gpio: Gpio) -> None:
        import RPi.GPIO as GPIO

        self.gpio = GPIO
        self.pins = gpio
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in (gpio.relay, gpio.watering_enabled_led, gpio.watering_disabled_led):
            GPIO.setup(pin, GPIO.OUT)

    def apply(self, watering_required: bool) -> None:
        g, p = self.gpio, self.pins
        on, off = g.HIGH, g.LOW
        if watering_required:
            g.output(p.relay, off)
            g.output(p.watering_enabled_led, on)
            g.output(p.watering_disabled_led, off)
        else:
            g.output(p.relay, on)
            g.output(p.watering_enabled_led, off)
            g.output(p.watering_disabled_led, on)

    def cleanup(self) -> None:
        self.gpio.cleanup()


def pin_driver(gpio: Gpio) -> PinDriver:
    if gpio.mock:
        return MockPins()
    try:
        import RPi  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("install [gpio] extra or set gpio.mock = true") from exc
    return PiPins()


@contextmanager
def watering_pins(gpio: Gpio) -> Iterator[PinDriver]:
    driver = pin_driver(gpio)
    driver.setup(gpio)
    try:
        yield driver
    finally:
        driver.cleanup()
