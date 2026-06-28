from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from rain_bypass.config import Settings, local_today

logger = logging.getLogger(__name__)

MM_PER_INCH = 25.4
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class WeatherError(RuntimeError):
    pass


def precip_window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    days = settings.watering.past_days
    return today - timedelta(days=days - 1), today


def fetch_precip(settings: Settings) -> float:
    start, end = precip_window(settings)
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    daily = _fetch_daily(loc.latitude, loc.longitude, start, end, timeout)
    inches = _sum_mm(daily, start, end) / MM_PER_INCH
    logger.info(
        "precipitation %.2f in over %s days (%s to %s)",
        inches,
        settings.watering.past_days,
        start,
        end,
    )
    return inches


def _fetch_daily(lat: float, lon: float, start: date, end: date, timeout: int) -> dict:
    base = {"latitude": lat, "longitude": lon, "daily": "precipitation_sum", "timezone": "auto"}
    if end >= date.today() - timedelta(days=2):
        payload = _get(
            FORECAST_URL,
            params={
                **base,
                "past_days": max(92, (date.today() - start).days + 1),
                "forecast_days": max(0, (end - date.today()).days + 1),
            },
            timeout=timeout,
        )
        if payload.get("daily", {}).get("time"):
            return payload["daily"]
    payload = _get(
        ARCHIVE_URL,
        params={**base, "start_date": start.isoformat(), "end_date": end.isoformat()},
        timeout=timeout,
    )
    return payload.get("daily", {})


def _get(url: str, *, params: dict, timeout: int) -> dict:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("weather request failed")
        raise WeatherError("weather request failed") from exc


def _sum_mm(daily: dict, start: date, end: date) -> float:
    dates = daily.get("time", [])
    amounts = daily.get("precipitation_sum", [])
    start_s, end_s = start.isoformat(), end.isoformat()
    return sum(
        float(amount or 0)
        for day, amount in zip(dates, amounts, strict=False)
        if start_s <= day <= end_s
    )
