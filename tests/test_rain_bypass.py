from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from conftest import patch_local_today, timeline_day, weather_snapshot
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
from rain_bypass.controller import run, tick
from rain_bypass.exceptions import WeatherError
from rain_bypass.gpio import MockPins, PiPins, watering_pins
from rain_bypass.logic import decide, safety_allows_watering
from rain_bypass.weather import (
    TimelineDay,
    _require_daily_rows,
    fetch_weather,
    freeze_block_for_days,
    max_daily_precip,
    parse_vc_datetime,
    resolve_location,
    sum_precip,
    timeline_params,
    timeline_request_params,
    timeline_url_for,
    weather_api_smoke,
)
from rain_bypass.windows import (
    event_lookback_window,
    forecast_window,
    month_start,
    seconds_until_next_check,
    timeline_window,
)


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def _weather_error(_settings):
    raise WeatherError("x")


def test_load_settings(settings):
    assert settings.location.zip_code == "53029"
    assert settings.balance.inches_per_cycle == pytest.approx(0.3)
    assert settings.balance.forecast_days == 2
    assert settings.watering.event_lookback_days == 3
    assert settings.watering.event_inches == pytest.approx(0.25)
    assert settings.watering.check_hour == 0
    assert settings.watering.check_minute == 0
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.weather.api_key == "test-key"


def test_missing_config(tmp_path):
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
def test_invalid_settings(settings_path, mutator):
    settings_path.write_text(mutator(settings_path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_invalid_toml(settings_path):
    settings_path.write_text("not = valid toml [[[", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_event_lookback_window(settings, monkeypatch):
    fixed = patch_local_today(monkeypatch, date(2024, 6, 10))
    start, end = event_lookback_window(settings)
    assert end == fixed
    assert start == fixed - timedelta(days=settings.watering.event_lookback_days - 1)


def test_forecast_and_timeline_windows(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 6, 10))
    assert forecast_window(settings) == (date(2024, 6, 11), date(2024, 6, 12))
    assert timeline_window(settings) == (date(2024, 6, 1), date(2024, 6, 12))

    no_forecast = settings.model_copy(
        update={"balance": settings.balance.model_copy(update={"forecast_days": 0})}
    )
    assert forecast_window(no_forecast) is None
    assert timeline_window(no_forecast) == (date(2024, 6, 1), date(2024, 6, 10))


def test_month_start_helper():
    assert month_start(date(2024, 6, 10)) == date(2024, 6, 1)


def test_safety_allows_watering(settings):
    assert safety_allows_watering(weather_snapshot(0.0, 0.0, 0.24), settings) is True
    assert safety_allows_watering(weather_snapshot(0.0, 0.0, 0.25), settings) is False
    assert (
        safety_allows_watering(weather_snapshot(0.0, 0.0, 0.0, freeze_block=True), settings)
        is False
    )
    no_event = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"event_inches": 0})}
    )
    assert safety_allows_watering(weather_snapshot(0.0, 0.0, 0.5), no_event) is True


def test_timeline_params(settings):
    params = timeline_params(settings)
    assert params["elements"] == "datetime,precip,tempmin"
    assert params["include"] == "days"


def test_freeze_block(settings):
    today = date(2024, 6, 10)
    days = [
        timeline_day(datetime="2024-06-10", tempmin=40),
        timeline_day(datetime="2024-06-11", tempmin=28),
    ]
    assert freeze_block_for_days(days, settings, today) is True
    days = [
        timeline_day(datetime="2024-06-10", tempmin=40),
        timeline_day(datetime="2024-06-11", tempmin=35),
    ]
    assert freeze_block_for_days(days, settings, today) is False
    assert freeze_block_for_days([timeline_day(tempmin=20)], settings, today) is False


def test_seconds_until_next_check(settings):
    tz = ZoneInfo("America/Chicago")
    late_night = datetime(2024, 6, 9, 23, 0, tzinfo=tz)
    assert seconds_until_next_check(settings, now=late_night) == pytest.approx(3600)
    before = datetime(2024, 6, 10, 3, 0, tzinfo=tz)
    assert seconds_until_next_check(settings, now=before) == pytest.approx(21 * 3600)
    after = datetime(2024, 6, 10, 5, 0, tzinfo=tz)
    assert seconds_until_next_check(settings, now=after) == pytest.approx(19 * 3600)
    naive = datetime(2024, 6, 10, 3, 0)
    assert seconds_until_next_check(settings, now=naive) == pytest.approx(21 * 3600)


def test_seconds_until_next_check_before_check_time_same_day(settings):
    tz = ZoneInfo("America/Chicago")
    morning = settings.model_copy(
        update={
            "watering": settings.watering.model_copy(update={"check_hour": 4, "check_minute": 30})
        }
    )
    before = datetime(2024, 6, 10, 3, 0, tzinfo=tz)
    assert seconds_until_next_check(morning, now=before) == pytest.approx(90 * 60)


def test_max_daily_precip():
    days = [
        timeline_day(datetime="2024-06-01", precip=0.1),
        timeline_day(datetime="2024-06-02", precip=0.4),
        timeline_day(precip=9.0),
    ]
    assert max_daily_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.4)


def test_parse_vc_datetime_variants():
    tz = "America/Chicago"
    assert parse_vc_datetime("2024-06-10", tz).date() == date(2024, 6, 10)
    assert parse_vc_datetime("2024-06-10T04:30:00", tz).hour == 4
    aware = datetime(2024, 6, 10, 4, 30, tzinfo=ZoneInfo("UTC"))
    assert parse_vc_datetime(aware.isoformat(), tz).tzinfo is not None


def test_sum_precip_sums_inches():
    days = [
        timeline_day(datetime="2024-06-01", precip=0.1),
        timeline_day(datetime="2024-06-02", precip=0.2),
    ]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.3)


def test_sum_precip_treats_null_as_zero():
    days = [timeline_day(datetime="2024-06-01", precip=None)]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 1)) == pytest.approx(0.0)


def test_sum_precip_excludes_outside_window():
    days = [
        timeline_day(datetime="2024-06-01", precip=0.5),
        timeline_day(datetime="2024-06-10", precip=9.0),
    ]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 1)) == pytest.approx(0.5)


def test_sum_precip_empty_days_raises():
    with pytest.raises(WeatherError, match="no daily rows"):
        sum_precip([], date(2024, 6, 1), date(2024, 6, 2))


def test_sum_precip_missing_datetime_raises():
    days = [timeline_day(precip=1.0)]
    with pytest.raises(WeatherError, match="missing datetime"):
        sum_precip(days, date(2024, 6, 1), date(2024, 6, 1))


def test_sum_precip_day_count_mismatch_logs_warning(settings, caplog):
    days = [timeline_day(datetime="2024-06-01", precip=0.5)]
    with caplog.at_level("WARNING"):
        total = sum_precip(days, date(2024, 6, 1), date(2024, 6, 7))
    assert total == pytest.approx(0.5)
    assert "expected 7" in caplog.text


def test_require_daily_rows_from_dicts():
    days = _require_daily_rows([{"datetime": "2024-06-10", "precip": 0.2}])
    assert days[0].precip == pytest.approx(0.2)


def test_require_daily_rows_none_raises():
    with pytest.raises(WeatherError, match="missing daily data"):
        _require_daily_rows(None)


def test_require_daily_rows_non_list_raises():
    with pytest.raises(WeatherError, match="missing daily data"):
        _require_daily_rows("bad")


def test_require_daily_rows_invalid_item_raises():
    with pytest.raises(WeatherError, match="invalid JSON"):
        _require_daily_rows([42])


def test_require_daily_rows_invalid_day_raises():
    with pytest.raises(WeatherError, match="invalid JSON"):
        _require_daily_rows([{"precip": "not-a-number"}])


def test_decide_allows_when_balance_and_safety_ok(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    decision = decide(settings, State())
    assert decision.watering_required is True
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is True
    assert decision.evaluation.rain_mtd == pytest.approx(0.0)
    assert decision.irrigation_inches_mtd == pytest.approx(0.3)


def test_decide_blocks_when_forecast_fills_deficit(settings, monkeypatch):
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


def test_decide_blocks_on_storm_event(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.25)
    )
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is True


def test_decide_blocks_in_dormant_month(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 1, 10))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is not None
    assert decision.evaluation.balance_ok is False


def test_decide_blocks_on_freeze(settings, monkeypatch, caplog):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(0.0, 0.0, 0.0, freeze_block=True),
    )
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert "freeze skip active" in caplog.text


def test_state_load_missing(tmp_path):
    assert State.load(tmp_path / "missing.json") == State()


def test_state_load_strips_legacy_blocked_until(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"blocked_until": null, "watering_required": true}\n', encoding="utf-8")
    loaded = State.load(path)
    assert loaded.watering_required is True


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(
        last_weather_update=123.0,
        watering_required=True,
        rainfall_inches=0.25,
        forecast_inches=0.1,
        balance_month=7,
        irrigation_inches_mtd=0.66,
    )
    state.save(path)
    loaded = State.load(path)
    assert loaded.rainfall_inches == pytest.approx(0.25)
    assert loaded.balance_month == 7
    assert loaded.irrigation_inches_mtd == pytest.approx(0.66)


def test_in_sewer_lockout(settings):
    sewer = settings.sewer
    assert in_sewer_lockout(sewer, date(2024, 2, 1)) is True
    assert in_sewer_lockout(sewer, date(2024, 1, 15)) is False


def test_decide_sewer_lockout_blocks(settings, monkeypatch, caplog):
    patch_local_today(monkeypatch, date(2024, 2, 10))

    def _no_api(_settings):
        raise AssertionError("weather API must not run during sewer lockout")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _no_api)
    with caplog.at_level("INFO"):
        decision = decide(settings, State())
    assert decision.watering_required is False
    assert decision.evaluation is None
    assert "sewer lockout" in caplog.text


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(
        watering_required=True,
        rainfall_inches=0.1,
        forecast_inches=0.05,
        balance_month=7,
    )
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, state)
    assert decision.watering_required is True
    assert decision.error == "x"


def test_fail_safe(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    decision = decide(settings, State())
    assert decision.watering_required is False


def test_tick_preserves_rainfall_on_weather_error(settings, tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"state_path": state_path}),
        }
    )
    state = State(rainfall_inches=0.42, forecast_inches=0.11)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    saved = tick(settings, state, lambda _required: None)
    assert saved.rainfall_inches == pytest.approx(0.42)
    assert saved.forecast_inches == pytest.approx(0.11)
    assert saved.last_error == "x"


def test_tick_persists_balance_state(settings, tmp_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"state_path": state_path}),
        }
    )
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    saved = tick(settings, State(), lambda _required: None)
    assert saved.irrigation_inches_mtd == pytest.approx(0.3)
    assert saved.balance_month == 7
    loaded = State.load(state_path)
    assert loaded.irrigation_inches_mtd == pytest.approx(0.3)


def _timeline_days(
    settings,
    today: date,
    *,
    lookback: list[float],
    forecast: list[float],
    mtd_prefix: list[float] | None = None,
) -> list[dict]:
    lookback_start = today - timedelta(days=settings.watering.event_lookback_days - 1)
    by_date: dict[str, dict] = {}
    cursor = month_start(today)
    if mtd_prefix:
        for amount in mtd_prefix:
            by_date[cursor.isoformat()] = {
                "datetime": cursor.isoformat(),
                "precip": amount,
                "tempmin": 40,
            }
            cursor += timedelta(days=1)
    cursor = lookback_start
    for amount in lookback:
        by_date[cursor.isoformat()] = {
            "datetime": cursor.isoformat(),
            "precip": amount,
            "tempmin": 40,
        }
        cursor += timedelta(days=1)
    cursor = today + timedelta(days=1)
    for amount in forecast:
        by_date[cursor.isoformat()] = {
            "datetime": cursor.isoformat(),
            "precip": amount,
            "tempmin": 40,
        }
        cursor += timedelta(days=1)
    return list(by_date.values())


@respx.mock
def test_resolve_location():
    today = date.today()
    url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/53029/{today}/{today}"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "latitude": 43.106,
                "longitude": -88.351,
                "timezone": "America/Chicago",
                "days": [{"datetime": today.isoformat()}],
            },
        )
    )
    location = resolve_location("53029", "test-key")
    assert location.zip_code == "53029"
    assert location.timezone == "America/Chicago"


@respx.mock
def test_resolve_location_missing_fields():
    today = date.today()
    url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/53029/{today}/{today}"
    respx.get(url).mock(return_value=httpx.Response(200, json={"days": []}))
    with pytest.raises(WeatherError, match="could not resolve zip"):
        resolve_location("53029", "test-key")


@respx.mock
def test_fetch_weather(settings, monkeypatch):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    lookback_start, lookback_end = event_lookback_window(settings)
    forecast_start, forecast_end = forecast_window(settings)
    assert forecast_start is not None and forecast_end is not None
    payload = {
        "queryCost": 7,
        "timezone": settings.location.timezone,
        "days": _timeline_days(
            settings,
            today,
            lookback=[0.25, 0.0, 0.35],
            forecast=[0.1, 0.05],
            mtd_prefix=[0.05] * 10,
        ),
    }
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))
    snapshot = fetch_weather(settings)
    parsed_days = [TimelineDay.model_validate(day) for day in payload["days"]]
    mtd_start = month_start(today)
    assert snapshot.rain_mtd == pytest.approx(sum_precip(parsed_days, mtd_start, today))
    assert snapshot.forecast_inches == pytest.approx(
        sum_precip(parsed_days, forecast_start, forecast_end)
    )
    assert snapshot.max_daily_inches == pytest.approx(
        max_daily_precip(parsed_days, lookback_start, lookback_end)
    )
    params = respx.calls.last.request.url.params
    assert params["key"] == settings.weather.api_key
    assert params["include"] == "days"


@respx.mock
def test_fetch_weather_logs_query_cost_at_debug(settings, monkeypatch, caplog):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            json={
                "queryCost": 7,
                "timezone": settings.location.timezone,
                "days": [{"datetime": today.isoformat(), "precip": 0.0, "tempmin": 40}],
            },
        )
    )
    with caplog.at_level("DEBUG"):
        fetch_weather(settings)
    assert "queryCost=7" in caplog.text


@respx.mock
def test_fetch_weather_timezone_mismatch_warns(settings, monkeypatch, caplog):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
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
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json={"days": "bad"}))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "unauthorized"), (429, "rate limit"), (500, "HTTP 500")],
)
def test_fetch_weather_http_errors(settings, monkeypatch, status, message):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(status))
    with pytest.raises(WeatherError, match=message):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_invalid_json(settings, monkeypatch):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_non_object_json(settings, monkeypatch):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_no_forecast_window(settings, monkeypatch):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    no_forecast = settings.model_copy(
        update={"balance": settings.balance.model_copy(update={"forecast_days": 0})}
    )
    api_start, api_end = timeline_window(no_forecast)
    payload = {
        "timezone": settings.location.timezone,
        "days": _timeline_days(
            no_forecast,
            today,
            lookback=[0.2, 0.1, 0.3],
            forecast=[],
            mtd_prefix=[0.0] * 10,
        ),
    }
    url = timeline_url_for(no_forecast, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=payload))
    snapshot = fetch_weather(no_forecast)
    assert snapshot.forecast_inches == pytest.approx(0.0)


@respx.mock
def test_fetch_weather_connection_error(settings, monkeypatch):
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WeatherError, match="visual crossing request failed"):
        fetch_weather(settings)


def test_weather_api_smoke(settings, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.weather.fetch_weather",
        lambda _s: weather_snapshot(0.5, 0.1, 0.2),
    )
    message = weather_api_smoke(settings)
    assert message.startswith("API OK:")
    assert "rain_mtd 0.50 in" in message
    assert "forecast 0.10 in" in message


def test_timeline_request_params(settings):
    params = timeline_request_params(settings)
    assert params["key"] == settings.weather.api_key
    assert params["unitGroup"] == "us"


def test_run_once(tmp_path, settings_path, monkeypatch):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    patch_local_today(monkeypatch, date(2024, 2, 1))
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))

    def _stop(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, sleep=_stop)


def test_run_loop_uses_scheduled_check(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))

    def _stop(seconds: float) -> None:
        assert seconds == pytest.approx(42.0)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(
            settings,
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


def test_pi_pins(fake_output_device, pi_gpio):
    driver = PiPins(pi_gpio)
    assert len(fake_output_device) == 3
    relay, green, red = fake_output_device
    assert relay.pin == 25
    assert green.pin == 4
    assert red.pin == 27
    # __init__ calls apply(False): block watering
    assert relay.value is True
    assert green.value is False
    assert red.value is True
    driver.apply(True)
    assert relay.value is False
    assert green.value is True
    assert red.value is False
    driver.apply(False)
    assert relay.value is True
    driver.cleanup()
    assert all(device.closed for device in fake_output_device)


def test_run_applies_cached_state_before_tick(tmp_path, settings_path, monkeypatch):
    calls: list[bool] = []

    @contextmanager
    def tracking_pins(_gpio):
        class Driver:
            def apply(self, watering_required: bool) -> None:
                calls.append(watering_required)

        yield Driver()

    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    State(watering_required=True).save(state_path)
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    run(settings, once=True, pin_factory=tracking_pins)
    assert calls[0] is True
    assert calls[-1] is True


def test_run_applies_fail_safe_when_no_cached_state(tmp_path, settings_path, monkeypatch):
    calls: list[bool] = []

    @contextmanager
    def tracking_pins(_gpio):
        class Driver:
            def apply(self, watering_required: bool) -> None:
                calls.append(watering_required)

        yield Driver()

    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    run(settings, once=True, pin_factory=tracking_pins)
    assert calls[0] is False
    assert calls[-1] is True


def test_watering_pins_requires_gpio_extra(pi_gpio):
    with (
        patch(
            "rain_bypass.gpio._import_gpiozero",
            side_effect=ImportError("gpiozero not installed"),
        ),
        pytest.raises(RuntimeError, match=r"gpio\.mock"),
        watering_pins(pi_gpio),
    ):
        pass


def test_watering_pins_pi_cleanup(fake_output_device, pi_gpio):
    with watering_pins(pi_gpio) as driver:
        driver.apply(False)
    assert all(device.closed for device in fake_output_device)


def test_main_once(settings_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
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
