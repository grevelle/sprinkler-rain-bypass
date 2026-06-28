from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx

from rain_bypass import config
from rain_bypass.config import Settings
from rain_bypass.exceptions import WeatherError
from rain_bypass.models import WeatherSnapshot
from rain_bypass.windows import (
    forecast_window,
    near_term_window,
    past_window,
    timeline_location_path,
    timeline_window,
)

logger = logging.getLogger(__name__)

VISUAL_CROSSING = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)
TIMELINE_BASE: dict[str, str] = {"unitGroup": "us"}


def timeline_params(settings: Settings) -> dict[str, str]:
    w = settings.watering
    elements = ["datetime", "precip"]
    includes = ["days"]
    if w.freeze_skip:
        elements.append("tempmin")
    if w.near_term_hours > 0:
        includes.append("hours")
    return {**TIMELINE_BASE, "elements": ",".join(elements), "include": ",".join(includes)}


def timeline_url_for(settings: Settings, start: date, end: date) -> str:
    return f"{VISUAL_CROSSING}/{timeline_location_path(settings.location)}/{start}/{end}"


def timeline_request_params(settings: Settings) -> dict[str, str]:
    return {"key": settings.weather.api_key, **timeline_params(settings)}


def weather_api_smoke(settings: Settings) -> str:
    past_start, past_end = past_window(settings)
    api_start, api_end = timeline_window(settings)
    snapshot = fetch_weather(settings)
    w = settings.watering
    return (
        f"API OK: past {snapshot.past_inches:.2f} in ({past_start} to {past_end}), "
        f"forecast {snapshot.forecast_inches:.2f} in, "
        f"near_term {snapshot.near_term_inches:.2f} in / {w.near_term_hours}h, "
        f"freeze_block={snapshot.freeze_block} (timeline {api_start} to {api_end})"
    )


def fetch_weather(settings: Settings, *, now: datetime | None = None) -> WeatherSnapshot:
    api_start, api_end = timeline_window(settings)
    past_start, past_end = past_window(settings)
    forecast = forecast_window(settings)
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    payload = _get_timeline(
        settings,
        api_start,
        api_end,
        api_key=settings.weather.api_key,
        timeout=timeout,
    )
    _log_timeline_meta(payload, settings)
    daily = _require_daily_rows(payload.get("days"))

    past_inches = sum_precip(daily, past_start, past_end)
    max_daily = max_daily_precip(daily, past_start, past_end)
    forecast_start: date | None
    forecast_end: date | None
    if forecast is None:
        forecast_inches = 0.0
        forecast_start = forecast_end = None
    else:
        forecast_start, forecast_end = forecast
        forecast_inches = sum_precip(daily, forecast_start, forecast_end)

    today = config.local_today(loc)
    freeze_block = freeze_block_for_days(daily, settings, today)
    near_term_inches = 0.0
    window = near_term_window(settings, now)
    if window is not None:
        hourly = _require_hourly_rows(payload.get("hours"))
        near_term_inches = sum_precip_hours(hourly, window[0], window[1], loc.timezone)

    w = settings.watering
    logger.info(
        "visual_crossing past %.2f in (max day %.2f), forecast %.2f in, "
        "near_term %.2f in / %sh, freeze_block=%s; "
        "allow if past<=%.2f, event>=%.2f blocks, forecast<=%.2f, "
        "near_term<=%.2f, freeze_skip=%s",
        past_inches,
        max_daily,
        forecast_inches,
        near_term_inches,
        w.near_term_hours,
        freeze_block,
        w.inches_required,
        w.event_inches,
        w.forecast_inches_max,
        w.near_term_inches_max,
        w.freeze_skip,
    )
    if forecast_start is not None and forecast_end is not None:
        logger.debug(
            "windows past=%s..%s forecast=%s..%s",
            past_start,
            past_end,
            forecast_start,
            forecast_end,
        )
    return WeatherSnapshot(
        past_inches,
        forecast_inches,
        max_daily,
        near_term_inches,
        freeze_block,
    )


def _get_timeline(
    settings: Settings,
    start: date,
    end: date,
    *,
    api_key: str,
    timeout: int,
) -> dict[str, Any]:
    return _get_json(
        timeline_url_for(settings, start, end),
        params={**timeline_params(settings), "key": api_key},
        timeout=timeout,
    )


def _require_daily_rows(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise WeatherError("visual crossing response missing daily data")
    return cast(list[Mapping[str, Any]], raw)


def _require_hourly_rows(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        raise WeatherError("visual crossing response missing hourly data")
    return cast(list[Mapping[str, Any]], raw)


def _get_json(url: str, *, params: dict[str, str], timeout: int) -> dict[str, Any]:
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("weather request failed status=%s", status)
        if status == 401:
            raise WeatherError("visual crossing unauthorized; check api_key") from exc
        if status == 429:
            raise WeatherError("visual crossing rate limit exceeded") from exc
        raise WeatherError(f"visual crossing HTTP {status}") from exc
    except httpx.HTTPError as exc:
        logger.warning("weather request failed")
        raise WeatherError(str(exc)) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError("visual crossing returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WeatherError("visual crossing returned invalid JSON")
    return cast(dict[str, Any], payload)


def _log_timeline_meta(payload: dict[str, Any], settings: Settings) -> None:
    query_cost = payload.get("queryCost")
    if query_cost is not None:
        logger.debug("visual_crossing queryCost=%s", query_cost)

    api_tz = payload.get("timezone")
    config_tz = settings.location.timezone
    if api_tz and api_tz != config_tz:
        logger.warning(
            "location timezone %s differs from visual crossing %s; window uses config timezone",
            config_tz,
            api_tz,
        )


def parse_vc_datetime(raw: str, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    text = str(raw).strip().replace(" ", "T")
    if len(text) == 10:
        text = f"{text}T00:00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def freeze_block_for_days(daily: list[Mapping[str, Any]], settings: Settings, today: date) -> bool:
    if not settings.watering.freeze_skip:
        return False
    threshold = settings.watering.freeze_temp_f
    watch = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    for day in daily:
        raw = day.get("datetime")
        if not raw:
            continue
        day_s = str(raw)[:10]
        if day_s not in watch:
            continue
        tempmin = day.get("tempmin")
        if tempmin is not None and float(tempmin) < threshold:
            return True
    return False


def sum_precip_hours(
    hours: list[Mapping[str, Any]], start: datetime, end: datetime, timezone: str
) -> float:
    if not hours:
        raise WeatherError("visual crossing returned no hourly rows")

    total = 0.0
    matched = 0
    for hour in hours:
        raw = hour.get("datetime")
        if not raw:
            raise WeatherError("visual crossing hour missing datetime")
        when = parse_vc_datetime(str(raw), timezone)
        if start <= when < end:
            total += float(hour.get("precip") or 0)
            matched += 1
    if matched == 0:
        logger.warning(
            "visual_crossing returned no hourly rows between %s and %s",
            start,
            end,
        )
    return total


def daily_precip_values(
    days: list[Mapping[str, Any]], start: date, end: date, *, strict: bool = True
) -> list[float]:
    if not days:
        raise WeatherError("visual crossing returned no daily rows")

    start_s, end_s = start.isoformat(), end.isoformat()
    values: list[float] = []
    for day in days:
        raw = day.get("datetime")
        if not raw:
            if strict:
                raise WeatherError("visual crossing day missing datetime")
            continue
        day_s = str(raw)[:10]
        if start_s <= day_s <= end_s:
            values.append(float(day.get("precip") or 0))
    return values


def max_daily_precip(days: list[Mapping[str, Any]], start: date, end: date) -> float:
    values = daily_precip_values(days, start, end, strict=False)
    return max(values) if values else 0.0


def sum_precip(days: list[Mapping[str, Any]], start: date, end: date) -> float:
    values = daily_precip_values(days, start, end)
    expected = (end - start).days + 1
    if len(values) != expected:
        logger.warning(
            "visual_crossing returned %s days in window, expected %s",
            len(values),
            expected,
        )
    return sum(values)
