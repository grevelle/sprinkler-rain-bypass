from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import (
    ConfigError,
    FailMode,
    Gpio,
    State,
    in_sewer_lockout,
    load_settings,
)
from rain_bypass.controller import run
from rain_bypass.exceptions import WeatherError
from rain_bypass.gpio import MockPins, PiPins, watering_pins
from rain_bypass.logic import (
    allow_watering,
    decide,
    past_ok,
    update_blocked_until,
    watering_required,
)
from rain_bypass.models import WeatherSnapshot
from rain_bypass.weather import (
    fetch_weather,
    freeze_block_for_days,
    max_daily_precip,
    parse_vc_datetime,
    sum_precip,
    sum_precip_hours,
    timeline_params,
    timeline_request_params,
    timeline_url_for,
    weather_api_smoke,
)
from rain_bypass.windows import (
    forecast_window,
    past_window,
    seconds_until_next_check,
    timeline_window,
)


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def _weather_error(_settings):
    raise WeatherError("x")


def _snapshot(
    past: float,
    forecast: float = 0.0,
    max_daily: float = 0.0,
    near_term: float = 0.0,
    freeze_block: bool = False,
) -> WeatherSnapshot:
    return WeatherSnapshot(past, forecast, max_daily, near_term, freeze_block)


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(43.106)
    assert settings.watering.past_days == 3
    assert settings.watering.forecast_days == 2
    assert settings.watering.forecast_inches_max == pytest.approx(0.5)
    assert settings.watering.event_inches == pytest.approx(0.25)
    assert settings.watering.rain_delay_days == 2
    assert settings.watering.near_term_hours == 0
    assert settings.watering.freeze_skip is True
    assert settings.watering.check_hour == 4
    assert settings.watering.check_minute == 30
    assert settings.watering.interval_seconds == pytest.approx(86400 / 2)
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.runtime.weather_timeout_seconds == 15
    assert settings.weather.api_key == "test-key"


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("mutator",),
    [
        (lambda text: text.replace("latitude = 43.106", "latitude = not_a_number"),),
        (lambda text: text.replace("past_days = 3", "past_days = 0"),),
        (lambda text: text.replace("forecast_days = 2", "forecast_days = -1"),),
        (lambda text: text.replace("rain_delay_days = 2", "rain_delay_days = -1"),),
        (lambda text: text.replace('api_key = "test-key"', 'api_key = ""'),),
    ],
)
def test_invalid_settings(settings_path, mutator):
    settings_path.write_text(mutator(settings_path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_invalid_toml(settings_path):
    settings_path.write_text("not = valid toml [[[", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_past_window(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)
    start, end = past_window(settings)
    assert end == fixed
    assert start == fixed - timedelta(days=settings.watering.past_days - 1)
    assert past_window(settings) == (start, end)


def test_forecast_and_timeline_windows(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)
    assert forecast_window(settings) == (date(2024, 6, 11), date(2024, 6, 12))
    assert timeline_window(settings) == (date(2024, 6, 8), date(2024, 6, 12))

    no_forecast = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"forecast_days": 0})}
    )
    assert forecast_window(no_forecast) is None
    assert timeline_window(no_forecast) == past_window(no_forecast)


def test_allow_watering(settings):
    assert allow_watering(_snapshot(1.5, 0.5, 0.0), settings) is True
    assert allow_watering(_snapshot(1.5001, 0.0, 0.0), settings) is False
    assert allow_watering(_snapshot(0.0, 0.5001, 0.0), settings) is False
    assert allow_watering(_snapshot(1.5, 0.5001, 0.0), settings) is False
    assert allow_watering(_snapshot(0.5, 0.0, 0.25), settings) is False
    near = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"near_term_hours": 24})}
    )
    assert allow_watering(_snapshot(0.0, 0.0, 0.0, near_term=0.26), near) is False
    assert allow_watering(_snapshot(0.0, 0.0, 0.0, freeze_block=True), settings) is False


def test_timeline_params_respects_flags(settings):
    assert "tempmin" in timeline_params(settings)["elements"]
    assert "hours" not in timeline_params(settings)["include"]
    minimal = settings.model_copy(
        update={
            "watering": settings.watering.model_copy(
                update={"freeze_skip": False, "near_term_hours": 24}
            )
        }
    )
    params = timeline_params(minimal)
    assert params["elements"] == "datetime,precip"
    assert params["include"] == "days,hours"


def test_past_ok_event_and_cumulative(settings):
    assert past_ok(_snapshot(1.5, 0, 0.24), settings) is True
    assert past_ok(_snapshot(1.5001, 0, 0.0), settings) is False
    assert past_ok(_snapshot(0.5, 0, 0.25), settings) is False

    no_event = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"event_inches": 0})}
    )
    assert past_ok(_snapshot(0.5, 0, 0.5), no_event) is True


def test_freeze_block(settings):
    today = date(2024, 6, 10)
    days = [
        {"datetime": "2024-06-10", "tempmin": 40},
        {"datetime": "2024-06-11", "tempmin": 28},
    ]
    assert freeze_block_for_days(days, settings, today) is True
    days[1]["tempmin"] = 35
    assert freeze_block_for_days(days, settings, today) is False
    no_freeze = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"freeze_skip": False})}
    )
    assert freeze_block_for_days(days, no_freeze, today) is False
    assert freeze_block_for_days([{"tempmin": 20}], settings, today) is False


def test_rain_delay_helpers(settings):
    today = date(2024, 6, 10)
    assert update_blocked_until(
        today, past_ok_flag=False, rain_delay_days=1, blocked_until=None
    ) == date(2024, 6, 11)
    assert update_blocked_until(
        today, past_ok_flag=False, rain_delay_days=1, blocked_until=date(2024, 6, 12)
    ) == date(2024, 6, 12)
    assert (
        update_blocked_until(
            date(2024, 6, 13), past_ok_flag=True, rain_delay_days=1, blocked_until=date(2024, 6, 12)
        )
        is None
    )
    assert (
        update_blocked_until(
            today, past_ok_flag=True, rain_delay_days=0, blocked_until=date(2024, 6, 12)
        )
        is None
    )
    assert watering_required(today, True, date(2024, 6, 11)) is False
    assert watering_required(date(2024, 6, 12), True, date(2024, 6, 11)) is True


def test_seconds_until_next_check(settings):
    scheduled = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"updates_per_day": 1})}
    )
    tz = ZoneInfo("America/Chicago")
    before = datetime(2024, 6, 10, 3, 0, tzinfo=tz)
    assert seconds_until_next_check(scheduled, now=before) == pytest.approx(90 * 60)
    after = datetime(2024, 6, 10, 5, 0, tzinfo=tz)
    assert seconds_until_next_check(scheduled, now=after) == pytest.approx(23.5 * 3600)
    interval = settings.watering.interval_seconds
    assert seconds_until_next_check(settings, now=before) == pytest.approx(interval)
    naive = datetime(2024, 6, 10, 3, 0)
    assert seconds_until_next_check(scheduled, now=naive) == pytest.approx(90 * 60)


def test_max_daily_precip():
    days = [
        {"datetime": "2024-06-01", "precip": 0.1},
        {"datetime": "2024-06-02", "precip": 0.4},
        {"precip": 9.0},
    ]
    assert max_daily_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.4)


def test_sum_precip_hours():
    tz = "America/Chicago"
    start = datetime(2024, 6, 10, 4, 30, tzinfo=ZoneInfo(tz))
    end = start + timedelta(hours=3)
    hours = [
        {"datetime": "2024-06-10T04:30:00", "precip": 0.1},
        {"datetime": "2024-06-10T05:30:00", "precip": 0.2},
        {"datetime": "2024-06-10T07:30:00", "precip": 9.0},
    ]
    assert sum_precip_hours(hours, start, end, tz) == pytest.approx(0.3)


def test_sum_precip_hours_errors_and_warnings(caplog):
    tz = "America/Chicago"
    start = datetime(2024, 6, 10, 4, 30, tzinfo=ZoneInfo(tz))
    end = start + timedelta(hours=2)
    with pytest.raises(WeatherError, match="no hourly rows"):
        sum_precip_hours([], start, end, tz)
    with pytest.raises(WeatherError, match="missing datetime"):
        sum_precip_hours([{"precip": 0.1}], start, end, tz)
    with caplog.at_level("WARNING"):
        total = sum_precip_hours(
            [{"datetime": "2024-06-10T08:00:00", "precip": 0.2}],
            start,
            end,
            tz,
        )
    assert total == pytest.approx(0.0)
    assert "no hourly rows between" in caplog.text


def test_parse_vc_datetime_variants():
    naive = parse_vc_datetime("2024-06-10", "America/Chicago")
    assert naive.hour == 0
    aware = parse_vc_datetime("2024-06-10T12:00:00-05:00", "America/Chicago")
    assert aware.tzinfo is not None


def test_sum_precip_sums_inches():
    days = [{"datetime": "2024-06-01", "precip": 1.0}, {"datetime": "2024-06-02", "precip": 0.5}]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(1.5)


def test_sum_precip_treats_null_as_zero():
    days = [{"datetime": "2024-06-01", "precip": None}, {"datetime": "2024-06-02", "precip": 0.25}]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.25)


def test_sum_precip_excludes_outside_window():
    days = [
        {"datetime": "2024-06-01", "precip": 1.0},
        {"datetime": "2024-06-02", "precip": 0.5},
        {"datetime": "2024-06-03", "precip": 9.0},
    ]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(1.5)


def test_sum_precip_empty_days_raises():
    with pytest.raises(WeatherError, match="no daily rows"):
        sum_precip([], date(2024, 6, 1), date(2024, 6, 2))


def test_sum_precip_missing_datetime_raises():
    days = [{"precip": 1.0}]
    with pytest.raises(WeatherError, match="missing datetime"):
        sum_precip(days, date(2024, 6, 1), date(2024, 6, 1))


def test_sum_precip_day_count_mismatch_logs_warning(settings, caplog):
    days = [{"datetime": "2024-06-01", "precip": 0.5}]
    with caplog.at_level("WARNING"):
        total = sum_precip(days, date(2024, 6, 1), date(2024, 6, 7))
    assert total == pytest.approx(0.5)
    assert "expected 7" in caplog.text


def test_decide_at_threshold(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(1.5, 0.5, 0.0))
    decision = decide(settings, State())
    assert decision.past_inches == pytest.approx(1.5)
    assert decision.forecast_inches == pytest.approx(0.5)
    assert decision.blocked_until is None
    assert decision.watering_required is True


def test_decide_just_over_threshold(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(1.5001, 0.0, 0.0))
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.blocked_until == date(2024, 6, 12)


def test_decide_blocked_by_event_only(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.5, 0.0, 0.25))
    decision = decide(settings, State())
    assert decision.past_inches == pytest.approx(0.5)
    assert decision.watering_required is False
    assert decision.blocked_until is not None


def test_decide_rain_delay_overrides_dry_weather(settings, monkeypatch, caplog):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0, 0.0, 0.0))
    state = State(blocked_until=date(2024, 6, 12))
    with caplog.at_level("INFO"):
        decision = decide(settings, state)
    assert decision.watering_required is False
    assert decision.blocked_until == date(2024, 6, 12)
    assert "rain delay active" in caplog.text


def test_decide_blocked_by_forecast_only(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0, 0.51, 0.0))
    decision = decide(settings, State())
    assert decision.past_inches == pytest.approx(0.0)
    assert decision.forecast_inches == pytest.approx(0.51)
    assert decision.watering_required is False
    assert decision.blocked_until is None


def test_state_load_missing(tmp_path):
    assert State.load(tmp_path / "missing.json") == State()


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(
        last_weather_update=123.0,
        watering_required=True,
        rainfall_inches=0.25,
        forecast_inches=0.1,
        blocked_until=date(2024, 6, 15),
    )
    state.save(path)
    loaded = State.load(path)
    assert loaded.rainfall_inches == pytest.approx(0.25)
    assert loaded.forecast_inches == pytest.approx(0.1)
    assert loaded.blocked_until == date(2024, 6, 15)


def test_in_sewer_lockout(settings):
    sewer = settings.sewer
    assert in_sewer_lockout(sewer, date(2024, 2, 1)) is True
    assert in_sewer_lockout(sewer, date(2024, 1, 15)) is False
    assert in_sewer_lockout(sewer, date(2024, 3, 15)) is True
    assert in_sewer_lockout(sewer, date(2024, 3, 16)) is False


def test_decide_sewer_lockout_blocks(settings, monkeypatch, caplog):
    fixed = date(2024, 2, 10)
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)

    def _no_api(_settings):
        raise AssertionError("weather API must not run during sewer lockout")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _no_api)
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.past_inches is None
    assert decision.error is None
    assert "sewer lockout" in caplog.text


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(watering_required=True, rainfall_inches=0.1, forecast_inches=0.05)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, state)
    assert decision.watering_required is True
    assert decision.forecast_inches == pytest.approx(0.05)
    assert decision.blocked_until is None


def test_fail_safe(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, State(watering_required=True, rainfall_inches=0.1))
    assert decision.watering_required is False
    assert decision.blocked_until is None
    assert decision.error == "x"


def test_watering_required(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.1))
    decision = decide(settings, State())
    assert decision.watering_required is True
    assert decision.past_inches == pytest.approx(0.1)
    assert decision.blocked_until is None
    assert decision.error is None


def test_watering_blocked(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(2.0, 0.0, 2.0))
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.blocked_until is not None


def test_decide_blocked_by_freeze(settings, monkeypatch, caplog):
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0, 0.0, 0.0, freeze_block=True)
    )
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.blocked_until is None
    assert "freeze skip active" in caplog.text


def _timeline_days(
    settings, today: date, *, past: list[float], forecast: list[float]
) -> list[dict]:
    past_start = today - timedelta(days=settings.watering.past_days - 1)
    days: list[dict] = []
    cursor = past_start
    for amount in past:
        days.append({"datetime": cursor.isoformat(), "precip": amount, "tempmin": 40})
        cursor += timedelta(days=1)
    cursor = today + timedelta(days=1)
    for amount in forecast:
        days.append({"datetime": cursor.isoformat(), "precip": amount, "tempmin": 40})
        cursor += timedelta(days=1)
    return days


@respx.mock
def test_fetch_weather(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    past_start, past_end = past_window(settings)
    forecast_start, forecast_end = forecast_window(settings)
    assert forecast_start is not None and forecast_end is not None
    payload = {
        "queryCost": 7,
        "timezone": settings.location.timezone,
        "days": _timeline_days(settings, today, past=[0.25, 0.0, 0.35], forecast=[0.1, 0.05]),
    }
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))
    totals = fetch_weather(settings)
    assert totals.past_inches == pytest.approx(sum_precip(payload["days"], past_start, past_end))
    assert totals.forecast_inches == pytest.approx(
        sum_precip(payload["days"], forecast_start, forecast_end)
    )
    assert totals.past_inches == pytest.approx(0.6)
    assert totals.forecast_inches == pytest.approx(0.15)
    assert totals.max_daily_inches == pytest.approx(0.35)
    params = respx.calls.last.request.url.params
    expected = timeline_params(settings)
    assert params["key"] == settings.weather.api_key
    assert params["unitGroup"] == expected["unitGroup"]
    assert params["elements"] == expected["elements"]
    assert params["include"] == expected["include"]


@respx.mock
def test_fetch_weather_logs_query_cost_at_debug(settings, monkeypatch, caplog):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "queryCost": 7,
                "timezone": settings.location.timezone,
                "days": [{"datetime": today.isoformat(), "precip": 0.0}],
            },
        )
    )
    with caplog.at_level("DEBUG"):
        fetch_weather(settings)
    assert "queryCost=7" in caplog.text


@respx.mock
def test_fetch_weather_timezone_mismatch_warns(settings, monkeypatch, caplog):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "timezone": "America/New_York",
                "days": [{"datetime": today.isoformat(), "precip": 0.0, "tempmin": 40}],
            },
        )
    )
    with caplog.at_level("WARNING"):
        fetch_weather(settings)
    assert "differs from visual crossing" in caplog.text


@respx.mock
def test_fetch_weather_missing_days(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json={"days": "bad"}))
    with pytest.raises(WeatherError, match="missing daily data"):
        fetch_weather(settings)


@respx.mock
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "unauthorized"),
        (429, "rate limit"),
        (500, "HTTP 500"),
    ],
)
def test_fetch_weather_http_errors(settings, monkeypatch, status, message):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(status))
    with pytest.raises(WeatherError, match=message):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_invalid_json(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_non_object_json(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_no_forecast_window(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    no_forecast = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"forecast_days": 0})}
    )
    past_start, past_end = past_window(no_forecast)
    api_start, api_end = timeline_window(no_forecast)
    assert api_start == past_start and api_end == past_end
    payload = {
        "timezone": settings.location.timezone,
        "days": _timeline_days(no_forecast, today, past=[0.2, 0.1, 0.3], forecast=[]),
    }
    url = timeline_url_for(no_forecast, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))
    totals = fetch_weather(no_forecast)
    assert totals.past_inches == pytest.approx(0.6)
    assert totals.forecast_inches == pytest.approx(0.0)


@respx.mock
def test_fetch_weather_near_term_hours(settings, monkeypatch):
    fixed = datetime(2024, 6, 10, 4, 30, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed.date())
    monkeypatch.setattr(
        "rain_bypass.windows.local_now",
        lambda _settings, now=None: fixed if now is None else now,
    )
    near = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"near_term_hours": 24})}
    )
    today = fixed.date()
    api_start, api_end = timeline_window(near)
    payload = {
        "timezone": near.location.timezone,
        "days": _timeline_days(near, today, past=[0.0, 0.0, 0.0], forecast=[0.0, 0.0]),
        "hours": [
            {"datetime": "2024-06-10T04:30:00", "precip": 0.1},
            {"datetime": "2024-06-10T12:00:00", "precip": 0.05},
            {"datetime": "2024-06-11T03:00:00", "precip": 0.2},
        ],
    }
    url = timeline_url_for(near, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))
    snapshot = fetch_weather(near)
    assert snapshot.near_term_inches == pytest.approx(0.35)


@respx.mock
def test_fetch_weather_missing_hours_when_near_term_enabled(settings, monkeypatch):
    fixed = datetime(2024, 6, 10, 4, 30, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed.date())
    monkeypatch.setattr(
        "rain_bypass.windows.local_now",
        lambda _settings, now=None: fixed if now is None else now,
    )
    near = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"near_term_hours": 24})}
    )
    api_start, api_end = timeline_window(near)
    url = timeline_url_for(near, api_start, api_end)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "timezone": near.location.timezone,
                "days": _timeline_days(
                    near, fixed.date(), past=[0.0, 0.0, 0.0], forecast=[0.0, 0.0]
                ),
            },
        )
    )
    with pytest.raises(WeatherError, match="missing hourly data"):
        fetch_weather(near)


@respx.mock
def test_fetch_weather_connection_error(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WeatherError, match="down"):
        fetch_weather(settings)


def test_weather_api_smoke(settings, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.weather.fetch_weather",
        lambda _s: _snapshot(0.5, 0.1, 0.2),
    )
    message = weather_api_smoke(settings)
    assert message.startswith("API OK:")
    assert "0.50 in" in message
    assert "forecast 0.10 in" in message


def test_timeline_request_params(settings):
    params = timeline_request_params(settings)
    assert params["key"] == settings.weather.api_key
    assert params["unitGroup"] == "us"
    assert "elements" in params


def test_run_once(tmp_path, settings_path, monkeypatch):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 2, 1))
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0))

    def _stop(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, sleep=_stop)


def test_run_loop_uses_scheduled_check(settings, monkeypatch):
    scheduled = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"updates_per_day": 1})}
    )
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0))

    def _stop(seconds: float) -> None:
        assert seconds == pytest.approx(42.0)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(
            scheduled,
            pin_factory=_noop_pins,
            seconds_until_check=lambda _s: 42.0,
            sleep=_stop,
        )


def test_mock_pins():
    MockPins().apply(True)
    MockPins().apply(False)


def test_watering_pins_mock():
    gpio = Gpio(relay=1, watering_enabled_led=2, watering_disabled_led=3, mock=True)
    with watering_pins(gpio) as driver:
        driver.apply(True)


def test_pi_pins(fake_rpi, pi_gpio):
    fake_rpi_mod, fake_gpio = fake_rpi
    with patch.dict("sys.modules", {"RPi": fake_rpi_mod, "RPi.GPIO": fake_gpio}):
        driver = PiPins(pi_gpio)
        driver.apply(True)
        driver.apply(False)
        driver.cleanup()
        assert fake_gpio.setup.call_count == 3
        assert fake_gpio.output.call_count == 6


def test_watering_pins_requires_gpio_extra(pi_gpio):
    with pytest.raises(RuntimeError, match="gpio.mock"):
        with watering_pins(pi_gpio):
            pass


def test_watering_pins_pi_cleanup(fake_rpi, pi_gpio):
    fake_rpi_mod, fake_gpio = fake_rpi
    with patch.dict("sys.modules", {"RPi": fake_rpi_mod, "RPi.GPIO": fake_gpio}):
        with watering_pins(pi_gpio) as driver:
            driver.apply(False)
        fake_gpio.cleanup.assert_called_once()


def test_main_once(settings_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: _snapshot(0.0))
    result = CliRunner().invoke(app, ["--config", str(settings_path), "--once"])
    assert result.exit_code == 0


def test_main_keyboard_interrupt(settings_path):
    with patch("rain_bypass.cli.run", side_effect=KeyboardInterrupt):
        result = CliRunner().invoke(app, ["--config", str(settings_path)])
        assert result.exit_code == 0


def test_main_fatal_error(settings_path):
    with patch("rain_bypass.cli.run", side_effect=RuntimeError("boom")):
        result = CliRunner().invoke(app, ["--config", str(settings_path)])
        assert result.exit_code == 1
