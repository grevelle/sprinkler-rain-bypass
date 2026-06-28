from __future__ import annotations

import logging
import time
from datetime import date

from rain_bypass.config import Decision, FailMode, Season, Settings, State, local_today
from rain_bypass.gpio import PinFactory, watering_pins
from rain_bypass.weather import WeatherError, fetch_precip

logger = logging.getLogger(__name__)


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def decide(settings: Settings, state: State) -> Decision:
    if not in_season(settings.season, local_today(settings.location)):
        logger.info("outside watering season")
        return Decision(watering_required=False, rainfall_inches=None, in_season=False)

    try:
        rainfall = fetch_precip(settings)
    except WeatherError as exc:
        logger.warning("weather failed; fail_mode=%s", settings.runtime.fail_mode)
        return _fallback(settings, state, str(exc))

    required = rainfall <= settings.watering.inches_required
    logger.info(
        "rainfall %.2f in (threshold %.2f in) -> watering %s",
        rainfall,
        settings.watering.inches_required,
        "required" if required else "blocked",
    )
    return Decision(watering_required=required, rainfall_inches=rainfall, in_season=True)


def _fallback(settings: Settings, state: State, message: str) -> Decision:
    keep_last = settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE
    if keep_last and state.watering_required is not None:
        return Decision(
            watering_required=state.watering_required,
            rainfall_inches=state.rainfall_inches,
            in_season=True,
            error=message,
        )
    return Decision(
        watering_required=False,
        rainfall_inches=state.rainfall_inches,
        in_season=True,
        error=message,
    )


def _tick(settings: Settings, state: State, apply) -> State:
    decision = decide(settings, state)
    apply(decision.watering_required)
    state = State(
        last_weather_update=time.time(),
        watering_required=decision.watering_required,
        rainfall_inches=decision.rainfall_inches,
        last_error=decision.error,
    )
    state.save(settings.runtime.state_path)
    return state


def run(settings: Settings, *, once: bool = False, pin_factory: PinFactory = watering_pins) -> None:
    state = State.load(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        if once:
            _tick(settings, state, driver.apply)
            return

        logger.info("starting loop (interval %.0fs)", settings.watering.interval_seconds)
        while True:
            state = _tick(settings, state, driver.apply)
            time.sleep(settings.watering.interval_seconds)
