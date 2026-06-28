from __future__ import annotations

import logging
from datetime import date, timedelta

from rain_bypass import config
from rain_bypass.config import FailMode, Settings, State, in_sewer_lockout
from rain_bypass.exceptions import WeatherError
from rain_bypass.models import Decision, WeatherSnapshot
from rain_bypass.weather import fetch_weather

logger = logging.getLogger(__name__)


def past_ok(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    w = settings.watering
    if snapshot.past_inches > w.inches_required:
        return False
    if w.event_inches > 0 and snapshot.max_daily_inches >= w.event_inches:
        return False
    return True


def allow_watering(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    w = settings.watering
    if snapshot.freeze_block:
        return False
    if w.near_term_hours > 0 and snapshot.near_term_inches > w.near_term_inches_max:
        return False
    if snapshot.forecast_inches > w.forecast_inches_max:
        return False
    return past_ok(snapshot, settings)


def update_blocked_until(
    today: date,
    *,
    past_ok_flag: bool,
    rain_delay_days: int,
    blocked_until: date | None,
) -> date | None:
    if rain_delay_days <= 0:
        return None
    if not past_ok_flag:
        candidate = today + timedelta(days=rain_delay_days)
        return candidate if blocked_until is None else max(blocked_until, candidate)
    if blocked_until and today > blocked_until:
        return None
    return blocked_until


def watering_required(
    today: date,
    weather_allow: bool,
    blocked_until: date | None,
) -> bool:
    if blocked_until and today <= blocked_until:
        return False
    return weather_allow


def decide(settings: Settings, state: State) -> Decision:
    today = config.local_today(settings.location)

    if in_sewer_lockout(settings.sewer, today):
        sewer = settings.sewer
        logger.info(
            "sewer lockout (%02d/%02d-%02d/%02d); watering blocked to protect annual sewer cap",
            sewer.start_month,
            sewer.start_day,
            sewer.end_month,
            sewer.end_day,
        )
        return Decision(False, None, None, state.blocked_until, None)

    blocked_until = state.blocked_until

    try:
        snapshot = fetch_weather(settings)
    except WeatherError as exc:
        logger.warning("weather failed; fail_mode=%s", settings.runtime.fail_mode)
        keep = (
            settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE
            and state.watering_required is not None
        )
        watering = False if not keep else bool(state.watering_required)
        return Decision(
            watering,
            state.rainfall_inches,
            state.forecast_inches,
            blocked_until,
            str(exc),
        )

    weather_allow = allow_watering(snapshot, settings)
    blocked_until = update_blocked_until(
        today,
        past_ok_flag=past_ok(snapshot, settings),
        rain_delay_days=settings.watering.rain_delay_days,
        blocked_until=blocked_until,
    )
    required = watering_required(today, weather_allow, blocked_until)
    if snapshot.freeze_block:
        logger.info(
            "freeze skip active (tempmin below %.1f F today or tomorrow)",
            settings.watering.freeze_temp_f,
        )
    if blocked_until and today <= blocked_until and weather_allow:
        logger.info("rain delay active through %s; watering blocked", blocked_until)
    return Decision(
        required,
        snapshot.past_inches,
        snapshot.forecast_inches,
        blocked_until,
        None,
    )
