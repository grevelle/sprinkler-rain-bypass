from __future__ import annotations

import logging
import time
from collections.abc import Callable

from rain_bypass.config import Settings, State
from rain_bypass.gpio import PinFactory, watering_pins
from rain_bypass.history import append_watering_history, migrate_legacy_irrigation
from rain_bypass.logic import decide
from rain_bypass.models import Decision, Evaluation

logger = logging.getLogger(__name__)


def _weather_fields(
    decision: Decision,
    state: State,
) -> tuple[float | None, float | None, float | None, bool | None]:
    match decision.evaluation:
        case Evaluation() as evaluation:
            return (
                evaluation.rain_mtd,
                evaluation.forecast_inches,
                evaluation.max_daily_inches,
                evaluation.freeze_block,
            )
        case _ if decision.error is not None:
            return (
                state.rainfall_inches,
                state.forecast_inches,
                state.max_daily_inches,
                state.freeze_block,
            )
        case _:
            return None, None, None, None


def tick(settings: Settings, state: State, apply: Callable[[bool], None]) -> State:
    decision = decide(settings, state)
    apply(decision.watering_required)
    append_watering_history(settings, state, decision)
    rainfall_inches, forecast_inches, max_daily_inches, freeze_block = _weather_fields(
        decision, state
    )
    updated = state.model_copy(
        update={
            "last_weather_update": time.time(),
            "watering_required": decision.watering_required,
            "rainfall_inches": rainfall_inches,
            "forecast_inches": forecast_inches,
            "max_daily_inches": max_daily_inches,
            "freeze_block": freeze_block,
            "last_error": decision.error,
        }
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
    migrate_legacy_irrigation(settings)
    state = State.load(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        initial = state.watering_required if state.watering_required is not None else False
        driver.apply(initial)
        if once:
            tick(settings, state, driver.apply)
            return

        while True:
            sleep(wait(settings))
            state = tick(settings, state, driver.apply)
