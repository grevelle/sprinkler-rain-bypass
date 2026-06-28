from __future__ import annotations

import logging

from rain_bypass import balance, config
from rain_bypass.config import FailMode, Settings, State, in_sewer_lockout
from rain_bypass.exceptions import WeatherError
from rain_bypass.models import Decision, WeatherSnapshot
from rain_bypass.weather import fetch_weather

logger = logging.getLogger(__name__)


def safety_allows_watering(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    if snapshot.freeze_block:
        return False
    w = settings.watering
    if w.event_inches > 0 and snapshot.max_daily_inches >= w.event_inches:
        return False
    return True


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
        return Decision(
            False,
            None,
            None,
            None,
            None,
            None,
            state.balance_month,
            state.irrigation_inches_mtd,
            None,
        )

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
            None,
            None,
            None,
            state.balance_month,
            state.irrigation_inches_mtd,
            str(exc),
        )

    state = balance.ensure_balance_month(state, today)
    target = balance.target_to_date(today, settings)
    deficit = balance.compute_deficit(
        today,
        settings,
        snapshot.rain_mtd,
        state.irrigation_inches_mtd,
        snapshot.forecast_inches,
    )
    balance_ok = balance.balance_allows_watering(
        today,
        settings,
        snapshot.rain_mtd,
        state.irrigation_inches_mtd,
        snapshot.forecast_inches,
    )
    safety_ok = safety_allows_watering(snapshot, settings)
    required = balance_ok and safety_ok

    if snapshot.freeze_block:
        logger.info(
            "freeze skip active (tempmin below %.1f F today or tomorrow)",
            settings.watering.freeze_temp_f,
        )

    logger.info(
        "balance: target_to_date=%.2f rain_mtd=%.2f irr_mtd=%.2f forecast=%.2f "
        "deficit=%.2f watering_required=%s",
        target,
        snapshot.rain_mtd,
        state.irrigation_inches_mtd,
        snapshot.forecast_inches,
        deficit,
        required,
    )

    updated_state = balance.refresh_balance_state(
        state, today, switch_on=required, settings=settings
    )

    return Decision(
        required,
        snapshot.rain_mtd,
        snapshot.forecast_inches,
        balance_ok,
        deficit,
        target,
        updated_state.balance_month,
        updated_state.irrigation_inches_mtd,
        None,
    )
