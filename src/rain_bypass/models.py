from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    rain_mtd: float
    forecast_inches: float
    max_daily_inches: float
    freeze_block: bool


@dataclass(frozen=True, slots=True)
class Evaluation:
    watering_required: bool
    balance_ok: bool
    safety_ok: bool
    deficit: float
    target_to_date: float
    monthly_target: float
    rain_mtd: float
    forecast_inches: float
    max_daily_inches: float
    freeze_block: bool


@dataclass(frozen=True, slots=True)
class Preview:
    irrigation_mtd: float
    sewer_lockout: bool
    live: WeatherSnapshot | None
    live_error: str | None
    evaluation: Evaluation | None
    cached_verdict: bool | None
    from_saved_weather: bool = False
    safety_known: bool = True

    @property
    def would_water(self) -> bool | None:
        match (self.sewer_lockout, self.evaluation, self.safety_known):
            case (True, _, _):
                return False
            case (_, evaluation, False) if evaluation is not None:
                if not evaluation.balance_ok:
                    return False
                return None
            case (_, evaluation, _) if evaluation is not None:
                return evaluation.watering_required
            case _:
                return self.cached_verdict


@dataclass(frozen=True, slots=True)
class Decision:
    watering_required: bool
    evaluation: Evaluation | None
    error: str | None
