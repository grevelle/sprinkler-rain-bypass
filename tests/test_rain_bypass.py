from __future__ import annotations

from datetime import date

import pytest
from conftest import patch_local_today, weather_snapshot

from rain_bypass.config import (
    ConfigError,
    FailMode,
    State,
    in_sewer_lockout,
    load_settings,
)
from rain_bypass.exceptions import WeatherError
from rain_bypass.logic import decide


def _weather_error(_settings):
    raise WeatherError("x")


def test_load_settings(settings) -> None:
    assert settings.location.zip_code == "53029"
    assert settings.balance.inches_per_cycle == pytest.approx(0.3)
    assert settings.balance.forecast_days == 2
    assert settings.watering.event_lookback_days == 3
    assert settings.watering.event_inches == pytest.approx(0.25)
    assert settings.watering.check_hour == 0
    assert settings.watering.check_minute == 0
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.weather.api_key == "test-key"


def test_missing_config(tmp_path) -> None:
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("mutator",),
    [
        (lambda text: text.replace('zip_code = "53029"', 'zip_code = "bad"'),),
        (lambda text: text.replace("latitude = 43.106", "latitude = not_a_number"),),
        (lambda text: text.replace("inches_per_cycle = 0.3", "inches_per_cycle = 0"),),
        (lambda text: text.replace('api_key = "test-key"', 'api_key = ""'),),
    ],
)
def test_invalid_settings(settings_path, mutator) -> None:
    settings_path.write_text(mutator(settings_path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_invalid_toml(settings_path) -> None:
    settings_path.write_text("not = valid toml [[[", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_decide_allows_when_balance_and_safety_ok(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    decision = decide(settings, State())
    assert decision.watering_required is True
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is True
    assert decision.evaluation.rain_mtd == pytest.approx(0.0)


def test_decide_blocks_when_forecast_fills_deficit(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    target = 5.0 * (15 / 31)
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(0.0, target, 0.0),
    )
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is False


def test_decide_blocks_on_storm_event(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.25)
    )
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is True


def test_decide_blocks_in_dormant_month(settings, monkeypatch) -> None:
    # Dec 1-15: outside Q1 meter lockout, but December balance target is 0.
    patch_local_today(monkeypatch, date(2024, 12, 10))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is False


def test_in_sewer_lockout_same_year_window() -> None:
    from rain_bypass.config import SewerLockout

    sewer = SewerLockout(start_month=6, start_day=1, end_month=8, end_day=31)
    assert in_sewer_lockout(sewer, date(2024, 7, 4)) is True
    assert in_sewer_lockout(sewer, date(2024, 5, 31)) is False
    assert in_sewer_lockout(sewer, date(2024, 9, 1)) is False


def test_decide_blocks_on_freeze(settings, monkeypatch, caplog) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(0.0, 0.0, 0.0, freeze_block=True),
    )
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert "freeze skip active" in caplog.text


def test_state_load_missing(tmp_path) -> None:
    assert State.load(tmp_path / "missing.json") == State()


def test_state_load_strips_legacy_irrigation_fields(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text(
        '{"irrigation_inches_mtd": 0.66, "balance_month": 7, "watering_required": true}\n',
        encoding="utf-8",
    )
    loaded = State.load(path)
    assert loaded.watering_required is True


def test_state_load_strips_legacy_blocked_until(tmp_path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"blocked_until": null, "watering_required": true}\n', encoding="utf-8")
    loaded = State.load(path)
    assert loaded.watering_required is True


def test_state_round_trip(tmp_path) -> None:
    path = tmp_path / "state.json"
    state = State(
        last_weather_update=123.0,
        watering_required=True,
        rainfall_inches=0.25,
        forecast_inches=0.1,
    )
    state.save(path)
    loaded = State.load(path)
    assert loaded.rainfall_inches == pytest.approx(0.25)
    assert loaded.forecast_inches == pytest.approx(0.1)


def test_in_sewer_lockout(settings) -> None:
    sewer = settings.sewer
    assert in_sewer_lockout(sewer, date(2024, 2, 1)) is True
    assert in_sewer_lockout(sewer, date(2024, 1, 15)) is True
    assert in_sewer_lockout(sewer, date(2024, 12, 16)) is True
    assert in_sewer_lockout(sewer, date(2024, 12, 15)) is False
    assert in_sewer_lockout(sewer, date(2024, 3, 16)) is False


def test_decide_sewer_lockout_blocks(settings, monkeypatch, caplog) -> None:
    patch_local_today(monkeypatch, date(2024, 2, 10))

    def _no_api(_settings):
        raise AssertionError("weather API must not run during sewer lockout")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _no_api)
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is None
    assert "sewer lockout" in caplog.text


def test_fail_mode_keep_last_state(settings, monkeypatch) -> None:
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(
        watering_required=True,
        rainfall_inches=0.1,
        forecast_inches=0.05,
    )
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, state)
    assert decision.watering_required is True
    assert decision.error == "x"


def test_fail_safe(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, State())
    assert decision.watering_required is False
