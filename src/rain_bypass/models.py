from __future__ import annotations

from dataclasses import dataclass

from rain_bypass.config import State


@dataclass(frozen=True, slots=True)
class WeatherSnapshot:
    rain_mtd: float
    forecast_inches: float
    max_daily_inches: float
    freeze_block: bool

    @classmethod
    def from_state(cls, state: State) -> WeatherSnapshot | None:
        if state.rainfall_inches is None or state.forecast_inches is None:
            return None
        return cls(
            rain_mtd=state.rainfall_inches,
            forecast_inches=state.forecast_inches,
            max_daily_inches=state.max_daily_inches or 0.0,
            freeze_block=bool(state.freeze_block),
        )


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


def persisted_weather(
    decision: Decision,
    state: State,
) -> tuple[float | None, float | None, float | None, bool | None]:
    match decision.evaluation:
        case Evaluation() as evaluation:
            return (
                evaluation.rain_mtd,
                evaluation.forecast_inches,
                evaluation.max_daily_inches,
                evaluation.freeze_block,
            )
        case _ if decision.error is not None:
            return (
                state.rainfall_inches,
                state.forecast_inches,
                state.max_daily_inches,
                state.freeze_block,
            )
        case _:
            return None, None, None, None
