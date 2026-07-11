from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import cast
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rain_bypass import config
from rain_bypass.config import Location, Settings
from rain_bypass.exceptions import WeatherError
from rain_bypass.models import WeatherSnapshot
from rain_bypass.windows import (
    event_lookback_window,
    forecast_window,
    month_start,
    timeline_location_path,
    timeline_window,
)

logger = logging.getLogger(__name__)

VISUAL_CROSSING = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)
TIMELINE_BASE: dict[str, str] = {"unitGroup": "us"}


class TimelineDay(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    datetime: str | None = None
    precip: float | None = None
    tempmin: float | None = None


class TimelineResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    days: list[TimelineDay] | None = None
    latitude: float | None = None
    longitude: float | None = None
    timezone: str | None = None
    query_cost: int | None = Field(default=None, alias="queryCost")


def timeline_params(settings: Settings) -> dict[str, str]:
    return {
        **TIMELINE_BASE,
        "elements": "datetime,precip,tempmin",
        "include": "days",
    }


def timeline_url_for(settings: Settings, start: date, end: date) -> str:
    return f"{VISUAL_CROSSING}/{timeline_location_path(settings.location)}/{start}/{end}"


def resolve_location(zip_code: str, api_key: str, *, timeout: int = 45) -> Location:
    """Resolve ZIP code to coordinates and timezone via Visual Crossing."""
    today = date.today()
    url = f"{VISUAL_CROSSING}/{zip_code}/{today}/{today}"
    payload = _get_json(
        url,
        params={
            **TIMELINE_BASE,
            "elements": "datetime",
            "include": "days",
            "key": api_key,
        },
        timeout=timeout,
    )
    if payload.latitude is None or payload.longitude is None or not payload.timezone:
        raise WeatherError("visual crossing could not resolve zip code")
    return Location(
        zip_code=zip_code,
        latitude=float(payload.latitude),
        longitude=float(payload.longitude),
        timezone=str(payload.timezone),
    )


def timeline_request_params(settings: Settings) -> dict[str, str]:
    return {"key": settings.weather.api_key, **timeline_params(settings)}


def weather_api_smoke(settings: Settings) -> str:
    lookback_start, lookback_end = event_lookback_window(settings)
    api_start, api_end = timeline_window(settings)
    snapshot = fetch_weather(settings)
    return (
        f"API OK: rain_mtd {snapshot.rain_mtd:.2f} in, "
        f"forecast {snapshot.forecast_inches:.2f} in, "
        f"max_day {snapshot.max_daily_inches:.2f} in ({lookback_start} to {lookback_end}), "
        f"freeze_block={snapshot.freeze_block} (timeline {api_start} to {api_end})"
    )


def fetch_weather(settings: Settings, *, now: datetime | None = None) -> WeatherSnapshot:
    del now  # daily windows only; kept for call-site compatibility
    api_start, api_end = timeline_window(settings)
    lookback_start, lookback_end = event_lookback_window(settings)
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
    daily = _require_daily_rows(payload.days)

    today = config.local_today(loc)
    mtd_start = month_start(today)
    rain_mtd = sum_precip(daily, mtd_start, today)
    max_daily = max_daily_precip(daily, lookback_start, lookback_end)

    forecast_start: date | None
    forecast_end: date | None
    if forecast is None:
        forecast_inches = 0.0
        forecast_start = forecast_end = None
    else:
        forecast_start, forecast_end = forecast
        forecast_inches = sum_precip(daily, forecast_start, forecast_end)

    freeze_block = freeze_block_for_days(daily, settings, today)

    w = settings.watering
    b = settings.balance
    logger.info(
        "visual_crossing rain_mtd %.2f in, forecast %.2f in, max_day %.2f in, "
        "freeze_block=%s; balance needs >= %.2f in/cycle, event>=%.2f blocks, "
        "forecast_days=%s",
        rain_mtd,
        forecast_inches,
        max_daily,
        freeze_block,
        b.inches_per_cycle,
        w.event_inches,
        b.forecast_days,
    )
    if forecast_start is not None and forecast_end is not None:
        logger.debug(
            "windows lookback=%s..%s mtd=%s..%s forecast=%s..%s",
            lookback_start,
            lookback_end,
            mtd_start,
            today,
            forecast_start,
            forecast_end,
        )
    return WeatherSnapshot(
        rain_mtd,
        forecast_inches,
        max_daily,
        freeze_block,
    )


def _get_timeline(
    settings: Settings,
    start: date,
    end: date,
    *,
    api_key: str,
    timeout: int,
) -> TimelineResponse:
    return _get_json(
        timeline_url_for(settings, start, end),
        params={**timeline_params(settings), "key": api_key},
        timeout=timeout,
    )


def _require_daily_rows(raw: object) -> list[TimelineDay]:
    if raw is None or not isinstance(raw, list):
        raise WeatherError("visual crossing response missing daily data")
    days: list[TimelineDay] = []
    for item in cast(list[object], raw):
        if isinstance(item, TimelineDay):
            days.append(item)
            continue
        if isinstance(item, dict):
            try:
                days.append(TimelineDay.model_validate(item))
            except ValidationError as exc:
                raise WeatherError("visual crossing returned invalid JSON") from exc
            continue
        raise WeatherError("visual crossing returned invalid JSON")
    return days


def _get_json(url: str, *, params: dict[str, str], timeout: int) -> TimelineResponse:
    try:
        response = httpx.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        logger.warning("weather request failed status=%s", status)
        if status == 401:
            raise WeatherError("visual crossing unauthorized; check api_key") from None
        if status == 429:
            raise WeatherError("visual crossing rate limit exceeded") from None
        raise WeatherError(f"visual crossing HTTP {status}") from None
    except httpx.HTTPError:
        logger.warning("weather request failed")
        raise WeatherError("visual crossing request failed") from None

    try:
        payload = response.json()
    except ValueError as exc:
        raise WeatherError("visual crossing returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise WeatherError("visual crossing returned invalid JSON")
    try:
        return TimelineResponse.model_validate(payload)
    except ValidationError as exc:
        raise WeatherError("visual crossing returned invalid JSON") from exc


def _log_timeline_meta(payload: TimelineResponse, settings: Settings) -> None:
    if payload.query_cost is not None:
        logger.debug("visual_crossing queryCost=%s", payload.query_cost)

    api_tz = payload.timezone
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


def freeze_block_for_days(daily: Sequence[TimelineDay], settings: Settings, today: date) -> bool:
    threshold = settings.watering.freeze_temp_f
    watch = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    for day in daily:
        raw = day.datetime
        if not raw:
            continue
        day_s = str(raw)[:10]
        if day_s not in watch:
            continue
        tempmin = day.tempmin
        if tempmin is not None and float(tempmin) < threshold:
            return True
    return False


def daily_precip_values(
    days: Sequence[TimelineDay], start: date, end: date, *, strict: bool = True
) -> list[float]:
    if not days:
        raise WeatherError("visual crossing returned no daily rows")

    start_s, end_s = start.isoformat(), end.isoformat()
    values: list[float] = []
    for day in days:
        raw = day.datetime
        if not raw:
            if strict:
                raise WeatherError("visual crossing day missing datetime")
            continue
        day_s = str(raw)[:10]
        if start_s <= day_s <= end_s:
            values.append(float(day.precip or 0))
    return values


def max_daily_precip(days: Sequence[TimelineDay], start: date, end: date) -> float:
    values = daily_precip_values(days, start, end, strict=False)
    return max(values) if values else 0.0


def sum_precip(days: Sequence[TimelineDay], start: date, end: date) -> float:
    values = daily_precip_values(days, start, end)
    expected = (end - start).days + 1
    if len(values) != expected:
        logger.warning(
            "visual_crossing returned %s days in window, expected %s",
            len(values),
            expected,
        )
    return sum(values)
