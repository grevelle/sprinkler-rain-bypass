from __future__ import annotations

import calendar
from datetime import date

from rain_bypass.config import Settings


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
    return target_to_date(today, settings) - rain_mtd - irrigation_mtd - forecast_inches


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
