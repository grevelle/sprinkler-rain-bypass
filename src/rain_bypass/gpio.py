from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from types import ModuleType
from typing import Protocol, override

from rain_bypass.config import Gpio

logger = logging.getLogger(__name__)


class PinDriver(Protocol):
    def apply(self, watering_required: bool) -> None: ...


type PinFactory = Callable[[Gpio], AbstractContextManager[PinDriver]]


class MockPins(PinDriver):
    @override
    def apply(self, watering_required: bool) -> None:
        logger.info("mock gpio watering %s", "enabled" if watering_required else "disabled")


def _import_gpiozero() -> ModuleType:
    return importlib.import_module("gpiozero")


class PiPins(PinDriver):
    def __init__(self, gpio: Gpio) -> None:
        output_device = _import_gpiozero().OutputDevice
        self._relay = output_device(gpio.relay, initial_value=True)
        self._watering_enabled_led = output_device(gpio.watering_enabled_led, initial_value=False)
        self._watering_disabled_led = output_device(gpio.watering_disabled_led, initial_value=True)
        # Fail-safe before the first weather fetch (Pi boot can take seconds on slow Wi-Fi).
        self.apply(False)

    @override
    def apply(self, watering_required: bool) -> None:
        if watering_required:
            self._relay.off()
            self._watering_enabled_led.on()
            self._watering_disabled_led.off()
        else:
            self._relay.on()
            self._watering_enabled_led.off()
            self._watering_disabled_led.on()

    def cleanup(self) -> None:
        self._relay.close()
        self._watering_enabled_led.close()
        self._watering_disabled_led.close()


@contextmanager
def watering_pins(gpio: Gpio) -> Generator[PinDriver]:
    if gpio.mock:
        driver: PinDriver = MockPins()
        cleanup = None
    else:
        try:
            _import_gpiozero()
        except ImportError as exc:
            raise RuntimeError("install [gpio] extra or set gpio.mock = true") from exc
        driver = PiPins(gpio)
        cleanup = driver.cleanup
    try:
        yield driver
    finally:
        if cleanup:
            cleanup()
