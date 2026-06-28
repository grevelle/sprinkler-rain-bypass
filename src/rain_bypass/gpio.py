from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from rain_bypass.config import Gpio

logger = logging.getLogger(__name__)


class PinDriver(Protocol):
    def apply(self, watering_required: bool) -> None: ...


PinFactory = Callable[[Gpio], AbstractContextManager[PinDriver]]


class MockPins:
    def apply(self, watering_required: bool) -> None:
        logger.info("mock gpio watering %s", "enabled" if watering_required else "disabled")


class PiPins:
    def __init__(self, gpio: Gpio) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self._pins = gpio
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        for pin in (gpio.relay, gpio.watering_enabled_led, gpio.watering_disabled_led):
            GPIO.setup(pin, GPIO.OUT)

    def apply(self, watering_required: bool) -> None:
        g, p = self._gpio, self._pins
        on, off = g.HIGH, g.LOW
        levels = (off, on, off) if watering_required else (on, off, on)
        g.output(p.relay, levels[0])
        g.output(p.watering_enabled_led, levels[1])
        g.output(p.watering_disabled_led, levels[2])

    def cleanup(self) -> None:
        self._gpio.cleanup()


def _driver_for(gpio: Gpio) -> PinDriver:
    if gpio.mock:
        return MockPins()
    try:
        import RPi  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("install [gpio] extra or set gpio.mock = true") from exc
    return PiPins(gpio)


@contextmanager
def watering_pins(gpio: Gpio) -> Iterator[PinDriver]:
    driver = _driver_for(gpio)
    cleanup = getattr(driver, "cleanup", None)
    try:
        yield driver
    finally:
        if cleanup:
            cleanup()
