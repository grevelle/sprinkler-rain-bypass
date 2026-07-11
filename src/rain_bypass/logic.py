from __future__ import annotations

import logging
from datetime import date

from rain_bypass import balance, config
from rain_bypass.config import FailMode, Settings, State, in_sewer_lockout
from rain_bypass.exceptions import WeatherError
from rain_bypass.models import Decision, Evaluation, Preview, WeatherSnapshot
from rain_bypass.weather import fetch_weather

logger = logging.getLogger(__name__)


def safety_allows_watering(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    if snapshot.freeze_block:
        return False
    w = settings.watering
    return not (w.event_inches > 0 and snapshot.max_daily_inches >= w.event_inches)


def evaluate_weather(
    settings: Settings,
    snapshot: WeatherSnapshot,
    irrigation_mtd: float,
    today: date,
) -> Evaluation:
    month_target = balance.monthly_target(today, settings)
    target = balance.target_to_date(today, settings)
    deficit = balance.compute_deficit(
        today,
        settings,
        snapshot.rain_mtd,
        irrigation_mtd,
        snapshot.forecast_inches,
    )
    balance_ok = balance.balance_allows_watering(
        today,
        settings,
        snapshot.rain_mtd,
        irrigation_mtd,
        snapshot.forecast_inches,
    )
    safety_ok = safety_allows_watering(snapshot, settings)
    required = balance_ok and safety_ok
    return Evaluation(
        watering_required=required,
        balance_ok=balance_ok,
        safety_ok=safety_ok,
        deficit=deficit,
        target_to_date=target,
        monthly_target=month_target,
        rain_mtd=snapshot.rain_mtd,
        forecast_inches=snapshot.forecast_inches,
        max_daily_inches=snapshot.max_daily_inches,
        freeze_block=snapshot.freeze_block,
    )


def _snapshot_from_state(state: State) -> WeatherSnapshot | None:
    if state.rainfall_inches is None or state.forecast_inches is None:
        return None
    return WeatherSnapshot(
        rain_mtd=state.rainfall_inches,
        forecast_inches=state.forecast_inches,
        max_daily_inches=state.max_daily_inches or 0.0,
        freeze_block=bool(state.freeze_block),
    )


def _safety_fields_saved(state: State) -> bool:
    return state.max_daily_inches is not None and state.freeze_block is not None


def _build_preview(
    effective_state: State,
    *,
    sewer_lockout: bool = False,
    live: WeatherSnapshot | None = None,
    live_error: str | None = None,
    evaluation: Evaluation | None = None,
    cached_verdict: bool | None = None,
    from_saved_weather: bool = False,
    safety_known: bool = True,
) -> Preview:
    return Preview(
        effective_state=effective_state,
        sewer_lockout=sewer_lockout,
        live=live,
        live_error=live_error,
        evaluation=evaluation,
        cached_verdict=cached_verdict,
        from_saved_weather=from_saved_weather,
        safety_known=safety_known,
    )


def preview(settings: Settings, state: State, *, fetch_live: bool = True) -> Preview:
    today = config.local_today(settings.location)
    sewer = in_sewer_lockout(settings.sewer, today)
    effective = balance.ensure_balance_month(state, today)

    if sewer:
        return _build_preview(effective, sewer_lockout=True)

    if not fetch_live:
        cached = state.watering_required if state.watering_required is not None else None
        saved = _snapshot_from_state(effective)
        if saved is None:
            return _build_preview(effective, cached_verdict=cached)
        safety_known = _safety_fields_saved(state)
        evaluation = evaluate_weather(settings, saved, effective.irrigation_inches_mtd, today)
        return _build_preview(
            effective,
            evaluation=evaluation,
            cached_verdict=cached,
            from_saved_weather=True,
            safety_known=safety_known,
        )

    try:
        live = fetch_weather(settings)
    except WeatherError as exc:
        return _build_preview(effective, live_error=str(exc))

    evaluation = evaluate_weather(settings, live, effective.irrigation_inches_mtd, today)
    return _build_preview(effective, live=live, evaluation=evaluation)


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
            watering_required=False,
            evaluation=None,
            balance_month=state.balance_month,
            irrigation_inches_mtd=state.irrigation_inches_mtd,
            error=None,
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
            watering_required=watering,
            evaluation=None,
            balance_month=state.balance_month,
            irrigation_inches_mtd=state.irrigation_inches_mtd,
            error=str(exc),
        )

    state = balance.ensure_balance_month(state, today)
    evaluation = evaluate_weather(settings, snapshot, state.irrigation_inches_mtd, today)

    if snapshot.freeze_block:
        logger.info(
            "freeze skip active (tempmin below %.1f F today or tomorrow)",
            settings.watering.freeze_temp_f,
        )

    logger.info(
        "balance: target_to_date=%.2f rain_mtd=%.2f irr_mtd=%.2f forecast=%.2f "
        "deficit=%.2f watering_required=%s",
        evaluation.target_to_date,
        evaluation.rain_mtd,
        state.irrigation_inches_mtd,
        evaluation.forecast_inches,
        evaluation.deficit,
        evaluation.watering_required,
    )

    updated_state = balance.refresh_balance_state(
        state, today, switch_on=evaluation.watering_required, settings=settings
    )

    return Decision(
        watering_required=evaluation.watering_required,
        evaluation=evaluation,
        balance_month=updated_state.balance_month,
        irrigation_inches_mtd=updated_state.irrigation_inches_mtd,
        error=None,
    )
