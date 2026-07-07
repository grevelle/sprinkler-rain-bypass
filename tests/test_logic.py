from __future__ import annotations

from datetime import date

from rain_bypass.config import State
from rain_bypass.logic import evaluate_weather, preview
from rain_bypass.models import Preview, WeatherSnapshot


def _snapshot(
    rain_mtd: float = 0.0,
    forecast: float = 0.0,
    max_daily: float = 0.0,
    freeze_block: bool = False,
) -> WeatherSnapshot:
    return WeatherSnapshot(rain_mtd, forecast, max_daily, freeze_block)


def test_evaluate_weather_allows_when_deficit_meets_threshold(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    evaluation = evaluate_weather(settings, _snapshot(), 0.0, date(2024, 7, 15))
    assert evaluation.watering_required is True
    assert evaluation.balance_ok is True
    assert evaluation.safety_ok is True


def test_evaluate_weather_blocks_in_dormant_month(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 1, 10))
    evaluation = evaluate_weather(settings, _snapshot(), 0.0, date(2024, 1, 10))
    assert evaluation.watering_required is False
    assert evaluation.balance_ok is False


def test_evaluate_weather_blocks_on_safety(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    evaluation = evaluate_weather(
        settings,
        _snapshot(max_daily=0.25),
        0.0,
        date(2024, 7, 15),
    )
    assert evaluation.watering_required is False
    assert evaluation.balance_ok is True
    assert evaluation.safety_ok is False


def test_preview_sewer_lockout(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 2, 1))
    result = preview(settings, State(), fetch_live=False)
    assert result.sewer_lockout is True
    assert result.would_water is False


def test_preview_cached_verdict(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    result = preview(settings, State(watering_required=True), fetch_live=False)
    assert result.evaluation is None
    assert result.would_water is True


def test_preview_live_success(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot())
    result = preview(settings, State(), fetch_live=True)
    assert result.live is not None
    assert result.evaluation is not None
    assert result.would_water is True


def test_preview_weather_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))

    def _fail(_settings):
        from rain_bypass.exceptions import WeatherError

        raise WeatherError("api down")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _fail)
    result = preview(settings, State(), fetch_live=True)
    assert result.live_error == "api down"
    assert result.would_water is None


def test_preview_would_water_from_evaluation() -> None:
    from rain_bypass.models import Evaluation

    evaluation = Evaluation(
        watering_required=True,
        balance_ok=True,
        safety_ok=True,
        deficit=0.5,
        target_to_date=1.0,
        monthly_target=5.0,
        rain_mtd=0.0,
        forecast_inches=0.0,
    )
    result = Preview(
        effective_state=State(),
        sewer_lockout=False,
        live=None,
        live_error=None,
        evaluation=evaluation,
        cached_verdict=None,
    )
    assert result.would_water is True
