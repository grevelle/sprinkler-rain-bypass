from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import requests

from rain_bypass.config import Provider, Settings

logger = logging.getLogger(__name__)

MM_PER_INCH = 25.4
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
VISUAL_CROSSING = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)


class WeatherError(RuntimeError):
    pass


class PrecipProvider(Protocol):
    def __call__(self, settings: Settings) -> float: ...


def fetch_precip(settings: Settings) -> float:
    try:
        return provider_for(settings)(settings)
    except WeatherError:
        raise
    except requests.RequestException as exc:
        logger.warning("Weather request failed")
        raise WeatherError("weather request failed") from exc
    except Exception as exc:
        logger.warning("Weather provider error")
        raise WeatherError("weather request failed") from exc


def provider_for(settings: Settings) -> PrecipProvider:
    weather = settings.weather
    if weather.provider is Provider.OPEN_METEO:
        return OpenMeteo(weather.request_timeout_seconds)
    if weather.provider is Provider.VISUAL_CROSSING:
        assert weather.visual_crossing_api_key
        return VisualCrossing(weather.visual_crossing_api_key, weather.request_timeout_seconds)
    raise WeatherError(f"unsupported provider: {weather.provider}")


def precip_window(settings: Settings) -> tuple[date, date]:
    today = datetime.now(ZoneInfo(settings.location.timezone)).date()
    days = settings.watering.past_days
    return today - timedelta(days=days - 1), today


def mm_to_inches(mm: float) -> float:
    return mm / MM_PER_INCH


def _get(url: str, *, params: dict, timeout: int) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _daily_sum(daily: dict, start: date, end: date, field: str) -> float:
    dates = daily.get("time", [])
    amounts = daily.get(field, [])
    start_s, end_s = start.isoformat(), end.isoformat()
    return sum(float(amount or 0) for day, amount in zip(dates, amounts, strict=False) if start_s <= day <= end_s)


class OpenMeteo:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def __call__(self, settings: Settings) -> float:
        start, end = precip_window(settings)
        lat, lon = settings.location.latitude, settings.location.longitude
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "precipitation_sum",
            "timezone": "auto",
        }

        if end >= date.today() - timedelta(days=2):
            payload = _get(
                OPEN_METEO_FORECAST,
                params={
                    **params,
                    "past_days": max(92, (date.today() - start).days + 1),
                    "forecast_days": max(0, (end - date.today()).days + 1),
                },
                timeout=self.timeout,
            )
            inches = mm_to_inches(_daily_sum(payload.get("daily", {}), start, end, "precipitation_sum"))
            if payload.get("daily", {}).get("time"):
                return _log(settings, "open_meteo", start, end, inches)

        payload = _get(
            OPEN_METEO_ARCHIVE,
            params={**params, "start_date": start.isoformat(), "end_date": end.isoformat()},
            timeout=self.timeout,
        )
        inches = mm_to_inches(_daily_sum(payload.get("daily", {}), start, end, "precipitation_sum"))
        return _log(settings, "open_meteo", start, end, inches)


class VisualCrossing:
    def __init__(self, api_key: str, timeout: int) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def __call__(self, settings: Settings) -> float:
        start, end = precip_window(settings)
        loc = settings.location
        payload = _get(
            f"{VISUAL_CROSSING}/{loc.latitude},{loc.longitude}/{start}/{end}",
            params={"key": self.api_key, "unitGroup": "us", "elements": "precip", "include": "days"},
            timeout=self.timeout,
        )
        days = payload.get("days")
        if not isinstance(days, list):
            raise WeatherError("visual crossing response missing daily data")
        inches = sum(float(day.get("precip") or 0) for day in days)
        return _log(settings, "visual_crossing", start, end, inches)


def _log(settings: Settings, source: str, start: date, end: date, inches: float) -> float:
    logger.info(
        "%s precipitation %.2f in over %s days (%s to %s)",
        source,
        inches,
        settings.watering.past_days,
        start,
        end,
    )
    return inches
