from __future__ import annotations

import calendar
from datetime import date

from rain_bypass.config import Settings, State


def month_start(today: date) -> date:
    return today.replace(day=1)


def monthly_target(today: date, settings: Settings) -> float:
    return settings.balance.monthly[today.month].target_inches_per_month


def target_to_date(today: date, settings: Settings) -> float:
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    target = monthly_target(today, settings)
    return target * (today.day / days_in_month)


def compute_deficit(
    today: date,
    settings: Settings,
    rain_mtd: float,
    irrigation_mtd: float,
    forecast_inches: float,
) -> float:
    return (
        target_to_date(today, settings)
        - rain_mtd
        - irrigation_mtd
        - forecast_inches
    )


def balance_allows_watering(
    today: date,
    settings: Settings,
    rain_mtd: float,
    irrigation_mtd: float,
    forecast_inches: float,
) -> bool:
    if monthly_target(today, settings) <= 0:
        return False
    deficit = compute_deficit(today, settings, rain_mtd, irrigation_mtd, forecast_inches)
    return deficit >= settings.balance.inches_per_cycle


def ensure_balance_month(state: State, today: date) -> State:
    if state.balance_month == today.month:
        return state
    return state.model_copy(update={"balance_month": today.month, "irrigation_inches_mtd": 0.0})


def refresh_balance_state(
    state: State, today: date, *, switch_on: bool, settings: Settings
) -> State:
    state = ensure_balance_month(state, today)
    if not switch_on:
        return state
    credited = state.irrigation_inches_mtd + settings.balance.inches_per_cycle
    return state.model_copy(update={"irrigation_inches_mtd": credited})
