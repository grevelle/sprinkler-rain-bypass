from __future__ import annotations

from datetime import date

from conftest import patch_local_today, weather_snapshot

from rain_bypass.config import State
from rain_bypass.logic import evaluate_weather, preview
from rain_bypass.models import Preview


def test_evaluate_weather_allows_when_deficit_meets_threshold(settings, monkeypatch) -> None:
    today = patch_local_today(monkeypatch, date(2024, 7, 15))
    evaluation = evaluate_weather(settings, weather_snapshot(), 0.0, today)
    assert evaluation.watering_required is True
    assert evaluation.balance_ok is True
    assert evaluation.safety_ok is True


def test_evaluate_weather_blocks_in_dormant_month(settings, monkeypatch) -> None:
    today = patch_local_today(monkeypatch, date(2024, 1, 10))
    evaluation = evaluate_weather(settings, weather_snapshot(), 0.0, today)
    assert evaluation.watering_required is False
    assert evaluation.balance_ok is False


def test_evaluate_weather_blocks_on_safety(settings, monkeypatch) -> None:
    today = patch_local_today(monkeypatch, date(2024, 7, 15))
    evaluation = evaluate_weather(
        settings,
        weather_snapshot(max_daily=0.25),
        0.0,
        today,
    )
    assert evaluation.watering_required is False
    assert evaluation.balance_ok is True
    assert evaluation.safety_ok is False


def test_preview_sewer_lockout(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 2, 1))
    result = preview(settings, State(), fetch_live=False)
    assert result.sewer_lockout is True
    assert result.would_water is False


def test_preview_cached_verdict(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    result = preview(settings, State(watering_required=True), fetch_live=False)
    assert result.evaluation is None
    assert result.would_water is True


def test_preview_cached_projects_from_saved_weather(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state = State(
        watering_required=True,
        rainfall_inches=10.0,
        forecast_inches=0.0,
        max_daily_inches=0.0,
        freeze_block=False,
    )
    result = preview(settings, state, fetch_live=False)
    assert result.from_saved_weather is True
    assert result.evaluation is not None
    assert result.would_water is False
    assert result.evaluation.balance_ok is False


def test_preview_cached_safety_unknown_when_not_saved(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state = State(
        watering_required=True,
        rainfall_inches=0.0,
        forecast_inches=0.0,
    )
    result = preview(settings, state, fetch_live=False)
    assert result.safety_known is False
    assert result.would_water is None


def test_preview_cached_safety_unknown_blocks_on_balance(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state = State(
        watering_required=True,
        rainfall_inches=10.0,
        forecast_inches=0.0,
    )
    result = preview(settings, state, fetch_live=False)
    assert result.safety_known is False
    assert result.would_water is False


def test_preview_live_success(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot())
    result = preview(settings, State(), fetch_live=True)
    assert result.live is not None
    assert result.evaluation is not None
    assert result.would_water is True


def test_preview_weather_error(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))

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
        max_daily_inches=0.0,
        freeze_block=False,
    )
    result = Preview(
        effective_state=State(),
        irrigation_mtd=0.0,
        sewer_lockout=False,
        live=None,
        live_error=None,
        evaluation=evaluation,
        cached_verdict=None,
    )
    assert result.would_water is True
