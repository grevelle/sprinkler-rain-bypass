from __future__ import annotations

import logging
import time
from collections.abc import Callable

from rain_bypass.config import Settings, State
from rain_bypass.gpio import PinFactory, watering_pins
from rain_bypass.logic import decide

logger = logging.getLogger(__name__)


def tick(settings: Settings, state: State, apply: Callable[[bool], None]) -> State:
    decision = decide(settings, state)
    apply(decision.watering_required)
    updated = State(
        last_weather_update=time.time(),
        watering_required=decision.watering_required,
        rainfall_inches=decision.past_inches,
        forecast_inches=decision.forecast_inches,
        blocked_until=decision.blocked_until,
        last_error=decision.error,
    )
    updated.save(settings.runtime.state_path)
    return updated


def run(
    settings: Settings,
    *,
    once: bool = False,
    pin_factory: PinFactory = watering_pins,
    sleep: Callable[[float], None] = time.sleep,
    seconds_until_check: Callable[[Settings], float] | None = None,
) -> None:
    from rain_bypass.windows import seconds_until_next_check

    wait = seconds_until_check or seconds_until_next_check
    state = State.load(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        initial = state.watering_required if state.watering_required is not None else False
        driver.apply(initial)
        if once:
            tick(settings, state, driver.apply)
            return

        while True:
            state = tick(settings, state, driver.apply)
            sleep(wait(settings))
