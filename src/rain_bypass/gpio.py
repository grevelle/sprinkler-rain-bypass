from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from types import ModuleType
from typing import Protocol

from rain_bypass.config import Gpio

logger = logging.getLogger(__name__)


class PinDriver(Protocol):
    def apply(self, watering_required: bool) -> None: ...


PinFactory = Callable[[Gpio], AbstractContextManager[PinDriver]]


class MockPins:
    def apply(self, watering_required: bool) -> None:
        logger.info("mock gpio watering %s", "enabled" if watering_required else "disabled")


def _import_rpi_gpio() -> ModuleType:
    return importlib.import_module("RPi.GPIO")


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


@contextmanager
def watering_pins(gpio: Gpio) -> Generator[PinDriver, None, None]:
    if gpio.mock:
        driver: PinDriver = MockPins()
        cleanup = None
    else:
        try:
            _import_rpi_gpio()
        except ImportError as exc:
            raise RuntimeError("install [gpio] extra or set gpio.mock = true") from exc
        driver = PiPins(gpio)
        cleanup = driver.cleanup
    try:
        yield driver
    finally:
        if cleanup:
            cleanup()
