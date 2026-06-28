from __future__ import annotations

import logging
import time

from rain_bypass.gpio import GpioController, build_gpio_controller
from rain_bypass.logic import evaluate_watering
from rain_bypass.models import RuntimeState, Settings, WateringDecision
from rain_bypass.state import load_state, save_state

logger = logging.getLogger(__name__)


class RainBypassRunner:
    def __init__(self, settings: Settings, gpio: GpioController | None = None) -> None:
        self._settings = settings
        self._gpio = gpio or build_gpio_controller(settings.gpio)
        self._state = load_state(settings.runtime.state_path)

    def run_once(self) -> None:
        self._gpio.setup(self._settings.gpio)
        try:
            decision = evaluate_watering(self._settings, self._state)
            self._gpio.apply(decision.watering_required)
            self._persist_state(decision)
        finally:
            self._gpio.cleanup()

    def run_forever(self) -> None:
        interval_seconds = 86400 / self._settings.watering.updates_per_day
        logger.info("Starting rain bypass loop (interval %.0f seconds)", interval_seconds)
        self._gpio.setup(self._settings.gpio)
        try:
            while True:
                decision = evaluate_watering(self._settings, self._state)
                self._gpio.apply(decision.watering_required)
                self._persist_state(decision)
                time.sleep(interval_seconds)
        finally:
            self._gpio.cleanup()

    def _persist_state(self, decision: WateringDecision) -> None:
        self._state = RuntimeState(
            last_weather_update=time.time(),
            watering_required=decision.watering_required,
            rainfall_inches=decision.rainfall_inches,
            last_error=decision.error,
        )
        save_state(self._settings.runtime.state_path, self._state)
