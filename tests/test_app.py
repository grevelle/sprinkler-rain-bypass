from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import responses

from rain_bypass.app import (
    ARCHIVE_URL,
    FORECAST_URL,
    Decision,
    WeatherError,
    decide,
    fetch_precip,
    in_season,
    precip_window,
    run,
)
from rain_bypass.config import FailMode, Season, State, load_settings


def _weather_error(_settings):
    raise WeatherError("x")


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def test_precip_window(settings):
    start, end = precip_window(settings)
    assert (end - start).days == settings.watering.past_days - 1


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2024, 6, 1), True), (date(2024, 2, 1), False), (date(2024, 3, 19), True)],
)
def test_in_season(today, expected):
    season = Season(start_month=3, start_day=19, end_month=9, end_day=12)
    assert in_season(season, today) is expected


def test_out_of_season(settings):
    winter = settings.model_copy(
        update={"season": Season(start_month=12, start_day=1, end_month=12, end_day=31)}
    )
    assert decide(winter, State()) == Decision(
        watering_required=False, rainfall_inches=None, in_season=False
    )


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(watering_required=True, rainfall_inches=0.1)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    assert decide(settings, state).watering_required is True


def test_fail_safe(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    decision = decide(settings, State(watering_required=True, rainfall_inches=0.1))
    assert decision.watering_required is False
    assert decision.error == "x"


def test_watering_required(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.1)
    decision = decide(settings, State())
    assert decision.watering_required is True
    assert decision.rainfall_inches == pytest.approx(0.1)


def test_watering_blocked(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 2.0)
    assert decide(settings, State()).watering_required is False


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(last_weather_update=123.0, watering_required=True, rainfall_inches=0.25)
    state.save(path)
    assert State.load(path).rainfall_inches == pytest.approx(0.25)


@responses.activate
def test_fetch_precip_forecast(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr(
        "rain_bypass.app.precip_window",
        lambda _s: (today - timedelta(days=2), today),
    )
    payload = {
        "daily": {
            "time": [(today - timedelta(days=2)).isoformat(), today.isoformat()],
            "precipitation_sum": [2.0, 3.0],
        }
    }
    responses.add(responses.GET, FORECAST_URL, json=payload, status=200)
    assert fetch_precip(settings) == pytest.approx(5 / 25.4)


@responses.activate
def test_fetch_precip_forecast_falls_back_to_archive(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr(
        "rain_bypass.app.precip_window",
        lambda _s: (today - timedelta(days=2), today),
    )
    archive_payload = {
        "daily": {
            "time": [(today - timedelta(days=2)).isoformat(), today.isoformat()],
            "precipitation_sum": [1.0, 2.0],
        }
    }
    responses.add(responses.GET, FORECAST_URL, json={"daily": {}}, status=200)
    responses.add(responses.GET, ARCHIVE_URL, json=archive_payload, status=200)
    assert fetch_precip(settings) == pytest.approx(3 / 25.4)


@responses.activate
def test_fetch_precip_archive(settings, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.app.precip_window",
        lambda _s: (date(2020, 1, 1), date(2020, 1, 3)),
    )
    payload = {"daily": {"time": ["2020-01-01", "2020-01-02"], "precipitation_sum": [1.0, 2.0]}}
    responses.add(responses.GET, ARCHIVE_URL, json=payload, status=200)
    assert fetch_precip(settings) == pytest.approx(3 / 25.4)


@responses.activate
def test_fetch_precip_http_error(settings, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.app.precip_window",
        lambda _s: (date(2020, 1, 1), date(2020, 1, 3)),
    )
    responses.add(responses.GET, ARCHIVE_URL, status=500)
    with pytest.raises(WeatherError):
        fetch_precip(settings)


def test_run_once(tmp_path, settings_path):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8")
        .replace('state_path = "state.json"', f'state_path = "{state_path.as_posix()}"')
        .replace("start_month = 3", "start_month = 12"),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.0)

    def _stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.app.time.sleep", _stop)
    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins)
