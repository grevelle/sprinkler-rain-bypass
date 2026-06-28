from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from rain_bypass.models import GpioSettings

logger = logging.getLogger(__name__)


class GpioController(ABC):
    @abstractmethod
    def setup(self, settings: GpioSettings) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply(self, watering_required: bool) -> None:
        raise NotImplementedError

    @abstractmethod
    def cleanup(self) -> None:
        raise NotImplementedError


class MockGpioController(GpioController):
    def setup(self, settings: GpioSettings) -> None:
        self._settings = settings
        logger.info(
            "Mock GPIO initialized (relay=%s, green=%s, red=%s)",
            settings.relay,
            settings.watering_enabled_led,
            settings.watering_disabled_led,
        )

    def apply(self, watering_required: bool) -> None:
        if watering_required:
            logger.info("Mock GPIO: relay OFF, green ON, red OFF (watering enabled)")
        else:
            logger.info("Mock GPIO: relay ON, green OFF, red ON (watering disabled)")

    def cleanup(self) -> None:
        logger.info("Mock GPIO cleanup")


class RaspberryPiGpioController(GpioController):
    def setup(self, settings: GpioSettings) -> None:
        import RPi.GPIO as GPIO

        self._gpio = GPIO
        self._settings = settings
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(settings.relay, GPIO.OUT)
        GPIO.setup(settings.watering_enabled_led, GPIO.OUT)
        GPIO.setup(settings.watering_disabled_led, GPIO.OUT)
        logger.info("GPIO pins configured")

    def apply(self, watering_required: bool) -> None:
        gpio = self._gpio
        settings = self._settings
        if watering_required:
            gpio.output(settings.relay, gpio.LOW)
            gpio.output(settings.watering_enabled_led, gpio.HIGH)
            gpio.output(settings.watering_disabled_led, gpio.LOW)
        else:
            gpio.output(settings.relay, gpio.HIGH)
            gpio.output(settings.watering_enabled_led, gpio.LOW)
            gpio.output(settings.watering_disabled_led, gpio.HIGH)

    def cleanup(self) -> None:
        self._gpio.cleanup()
        logger.info("GPIO cleanup complete")


def build_gpio_controller(settings: GpioSettings) -> GpioController:
    if settings.mock:
        return MockGpioController()

    try:
        import RPi  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "RPi.GPIO is not installed. Install with `pip install sprinkler-rain-bypass[gpio]` "
            "or set gpio.mock = true for development."
        ) from exc

    return RaspberryPiGpioController()
