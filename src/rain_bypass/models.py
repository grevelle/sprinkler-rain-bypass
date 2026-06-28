from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    past_inches: float
    forecast_inches: float
    max_daily_inches: float
    near_term_inches: float
    freeze_block: bool


@dataclass(frozen=True, slots=True)
class Decision:
    watering_required: bool
    past_inches: float | None
    forecast_inches: float | None
    blocked_until: date | None
    error: str | None
