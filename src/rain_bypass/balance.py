from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date

from rain_bypass.config import Settings
from rain_bypass.models import Evaluation


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


@dataclass(frozen=True, slots=True)
class BalanceDisplay:
    progress_pct: int
    deficit_class: str
    deficit_note: str
    formula: str
    target: float
    rain: float
    irrigation: float
    received: float
    forecast: float
    deficit: float


def balance_display(
    evaluation: Evaluation,
    irrigation_mtd: float,
    *,
    inches_per_cycle: float,
) -> BalanceDisplay:
    rain = evaluation.rain_mtd
    forecast = evaluation.forecast_inches
    target = evaluation.target_to_date
    deficit = evaluation.deficit
    received = rain + irrigation_mtd
    effective = received + forecast
    progress_pct = min(100, round(100 * effective / target)) if target > 0 else 0
    if deficit > 0:
        deficit_class = "need"
        deficit_note = (
            f"{deficit:.2f} in gap before balance allows a cycle (need ≥ {inches_per_cycle:.2f} in)"
        )
    elif deficit < 0:
        deficit_class = "surplus"
        deficit_note = f"{abs(deficit):.2f} in over the monthly target pace"
    else:
        deficit_class = "even"
        deficit_note = "On target — no watering needed for balance"
    formula = (
        f"{target:.2f} - {rain:.2f} - {irrigation_mtd:.2f} - {forecast:.2f} = "
        f"{deficit:.2f} in (need ≥ {inches_per_cycle:.2f} to allow)"
    )
    return BalanceDisplay(
        progress_pct=progress_pct,
        deficit_class=deficit_class,
        deficit_note=deficit_note,
        formula=formula,
        target=target,
        rain=rain,
        irrigation=irrigation_mtd,
        received=received,
        forecast=forecast,
        deficit=deficit,
    )
