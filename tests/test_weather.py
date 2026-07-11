from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx
from conftest import patch_local_today, timeline_day, weather_snapshot

from rain_bypass.exceptions import WeatherError
from rain_bypass.weather import (
    TimelineDay,
    _require_daily_rows,
    fetch_weather,
    freeze_block_for_days,
    max_daily_precip,
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
    timeline_window,
)


def test_max_daily_precip() -> None:
    days = [
        timeline_day(datetime="2024-06-01", precip=0.1),
        timeline_day(datetime="2024-06-02", precip=0.4),
        timeline_day(precip=9.0),
    ]
    assert max_daily_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.4)


def test_sum_precip_sums_inches() -> None:
    days = [
        timeline_day(datetime="2024-06-01", precip=0.1),
        timeline_day(datetime="2024-06-02", precip=0.2),
    ]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.3)


def test_sum_precip_treats_null_as_zero() -> None:
    days = [timeline_day(datetime="2024-06-01", precip=None)]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 1)) == pytest.approx(0.0)


def test_sum_precip_excludes_outside_window() -> None:
    days = [
        timeline_day(datetime="2024-06-01", precip=0.5),
        timeline_day(datetime="2024-06-10", precip=9.0),
    ]
    assert sum_precip(days, date(2024, 6, 1), date(2024, 6, 1)) == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("days", "start", "end", "match"),
    [
        ([], date(2024, 6, 1), date(2024, 6, 2), "no daily rows"),
        ([timeline_day(precip=1.0)], date(2024, 6, 1), date(2024, 6, 1), "missing datetime"),
    ],
)
def test_sum_precip_raises(days, start, end, match) -> None:
    with pytest.raises(WeatherError, match=match):
        sum_precip(days, start, end)


def test_sum_precip_day_count_mismatch_logs_warning(settings, caplog) -> None:
    days = [timeline_day(datetime="2024-06-01", precip=0.5)]
    with caplog.at_level("WARNING"):
        total = sum_precip(days, date(2024, 6, 1), date(2024, 6, 7))
    assert total == pytest.approx(0.5)
    assert "expected 7" in caplog.text


def test_require_daily_rows_from_dicts() -> None:
    days = _require_daily_rows([{"datetime": "2024-06-10", "precip": 0.2}])
    assert days[0].precip == pytest.approx(0.2)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (None, "missing daily data"),
        ("bad", "missing daily data"),
        ([42], "invalid JSON"),
        ([{"precip": "not-a-number"}], "invalid JSON"),
    ],
)
def test_require_daily_rows_raises(payload, match) -> None:
    with pytest.raises(WeatherError, match=match):
        _require_daily_rows(payload)


def test_timeline_params(settings) -> None:
    params = timeline_params(settings)
    assert params["elements"] == "datetime,precip,tempmin"
    assert params["include"] == "days"


def test_freeze_block(settings) -> None:
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
def test_resolve_location() -> None:
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
def test_resolve_location_missing_fields() -> None:
    today = date.today()
    url = f"https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline/53029/{today}/{today}"
    respx.get(url).mock(return_value=httpx.Response(200, json={"days": []}))
    with pytest.raises(WeatherError, match="could not resolve zip"):
        resolve_location("53029", "test-key")


@respx.mock
def test_fetch_weather(settings, monkeypatch) -> None:
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
def test_fetch_weather_logs_query_cost_at_debug(settings, monkeypatch, caplog) -> None:
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
def test_fetch_weather_timezone_mismatch_warns(settings, monkeypatch, caplog) -> None:
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
def test_fetch_weather_missing_days(settings, monkeypatch) -> None:
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
def test_fetch_weather_http_errors(settings, monkeypatch, status, message) -> None:
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(status))
    with pytest.raises(WeatherError, match=message):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_invalid_json(settings, monkeypatch) -> None:
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, text="not json"))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_non_object_json(settings, monkeypatch) -> None:
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(WeatherError, match="invalid JSON"):
        fetch_weather(settings)


@respx.mock
def test_fetch_weather_no_forecast_window(settings, monkeypatch) -> None:
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
def test_fetch_weather_connection_error(settings, monkeypatch) -> None:
    today = date(2024, 6, 10)
    patch_local_today(monkeypatch, today)
    api_start, api_end = timeline_window(settings)
    url = timeline_url_for(settings, api_start, api_end)
    respx.get(url).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(WeatherError, match="visual crossing request failed"):
        fetch_weather(settings)


def test_weather_api_smoke(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "rain_bypass.weather.fetch_weather",
        lambda _s: weather_snapshot(0.5, 0.1, 0.2),
    )
    message = weather_api_smoke(settings)
    assert message.startswith("API OK:")
    assert "rain_mtd 0.50 in" in message
    assert "forecast 0.10 in" in message


def test_timeline_request_params(settings) -> None:
    params = timeline_request_params(settings)
    assert params["key"] == settings.weather.api_key
    assert params["unitGroup"] == "us"
