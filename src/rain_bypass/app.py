from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from rain_bypass.config import FailMode, Season, Settings
from rain_bypass.gpio import watering_pins
from rain_bypass.weather import WeatherError, fetch_precip

logger = logging.getLogger(__name__)


@dataclass
class State:
    last_weather_update: float | None = None
    watering_required: bool | None = None
    rainfall_inches: float | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class Decision:
    watering_required: bool
    rainfall_inches: float | None
    in_season: bool
    source: str
    error: str | None = None


def load_state(path: Path) -> State:
    if not path.is_file():
        return State()
    data = json.loads(path.read_text(encoding="utf-8"))
    return State(**{field: data.get(field) for field in State.__dataclass_fields__})


def save_state(path: Path, state: State) -> None:
    path.write_text(json.dumps(asdict(state), indent=2) + "\n", encoding="utf-8")


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def decide(settings: Settings, state: State) -> Decision:
    today = datetime.now(ZoneInfo(settings.location.timezone)).date()
    if not in_season(settings.season, today):
        logger.info("outside watering season (%s)", today)
        return Decision(False, None, False, "season")

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
    return Decision(required, rainfall, True, settings.weather.provider.value)


def _fallback(settings: Settings, state: State, message: str) -> Decision:
    if settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE and state.watering_required is not None:
        return Decision(state.watering_required, state.rainfall_inches, True, "last_state", message)
    return Decision(False, state.rainfall_inches, True, "fail_safe", message)


def _tick(settings: Settings, state: State, apply) -> State:
    decision = decide(settings, state)
    apply(decision.watering_required)
    state = State(time.time(), decision.watering_required, decision.rainfall_inches, decision.error)
    save_state(settings.runtime.state_path, state)
    return state


def run(settings: Settings, *, once: bool = False, pin_factory=watering_pins) -> None:
    state = load_state(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        if once:
            _tick(settings, state, driver.apply)
            return

        logger.info("starting loop (interval %.0fs)", settings.watering.interval_seconds)
        while True:
            state = _tick(settings, state, driver.apply)
            time.sleep(settings.watering.interval_seconds)
