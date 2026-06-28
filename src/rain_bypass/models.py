from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    rain_mtd: float
    forecast_inches: float
    max_daily_inches: float
    freeze_block: bool


@dataclass(frozen=True, slots=True)
class Decision:
    watering_required: bool
    rain_mtd: float | None
    forecast_inches: float | None
    balance_ok: bool | None
    balance_deficit: float | None
    target_to_date: float | None
    balance_month: int | None
    irrigation_inches_mtd: float | None
    error: str | None
