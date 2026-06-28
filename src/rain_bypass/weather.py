from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, timedelta
from typing import Protocol

import requests

from rain_bypass.config import Provider, Settings, Weather, local_today

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
        return _provider_for(settings.weather)(settings)
    except WeatherError:
        raise
    except requests.RequestException as exc:
        logger.warning("weather request failed")
        raise WeatherError("weather request failed") from exc
    except Exception as exc:
        logger.warning("weather provider error")
        raise WeatherError("weather request failed") from exc


def precip_window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    past_days = settings.watering.past_days
    return today - timedelta(days=past_days - 1), today


def mm_to_inches(mm: float) -> float:
    return mm / MM_PER_INCH


def _provider_for(weather: Weather) -> PrecipProvider:
    factories: dict[Provider, Callable[[Weather], PrecipProvider]] = {
        Provider.OPEN_METEO: lambda w: OpenMeteo(w.request_timeout_seconds),
        Provider.VISUAL_CROSSING: lambda w: VisualCrossing(
            w.visual_crossing_api_key or "", w.request_timeout_seconds
        ),
    }
    factory = factories.get(weather.provider)
    if factory is None:
        raise WeatherError(f"unsupported provider: {weather.provider}")
    if weather.provider is Provider.VISUAL_CROSSING and not weather.visual_crossing_api_key:
        raise WeatherError("visual_crossing_api_key is not configured")
    return factory(weather)


def _get(url: str, *, params: dict, timeout: int) -> dict:
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _daily_sum(daily: dict, start: date, end: date, field: str) -> float:
    dates = daily.get("time", [])
    amounts = daily.get(field, [])
    start_s, end_s = start.isoformat(), end.isoformat()
    return sum(
        float(amount or 0)
        for day, amount in zip(dates, amounts, strict=False)
        if start_s <= day <= end_s
    )


def _log_precip(source: str, settings: Settings, start: date, end: date, inches: float) -> float:
    logger.info(
        "%s precipitation %.2f in over %s days (%s to %s)",
        source,
        inches,
        settings.watering.past_days,
        start,
        end,
    )
    return inches


def _open_meteo_params(lat: float, lon: float, start: date, end: date) -> dict:
    return {
        "latitude": lat,
        "longitude": lon,
        "daily": "precipitation_sum",
        "timezone": "auto",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _open_meteo_daily(lat: float, lon: float, start: date, end: date, timeout: int) -> dict:
    base = {"latitude": lat, "longitude": lon, "daily": "precipitation_sum", "timezone": "auto"}
    if end >= date.today() - timedelta(days=2):
        payload = _get(
            OPEN_METEO_FORECAST,
            params={
                **base,
                "past_days": max(92, (date.today() - start).days + 1),
                "forecast_days": max(0, (end - date.today()).days + 1),
            },
            timeout=timeout,
        )
        if payload.get("daily", {}).get("time"):
            return payload["daily"]
    archive = _get(
        OPEN_METEO_ARCHIVE,
        params=_open_meteo_params(lat, lon, start, end),
        timeout=timeout,
    )
    return archive.get("daily", {})


class OpenMeteo:
    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def __call__(self, settings: Settings) -> float:
        start, end = precip_window(settings)
        loc = settings.location
        daily = _open_meteo_daily(loc.latitude, loc.longitude, start, end, self.timeout)
        inches = mm_to_inches(_daily_sum(daily, start, end, "precipitation_sum"))
        return _log_precip("open_meteo", settings, start, end, inches)


class VisualCrossing:
    def __init__(self, api_key: str, timeout: int) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def __call__(self, settings: Settings) -> float:
        start, end = precip_window(settings)
        loc = settings.location
        payload = _get(
            f"{VISUAL_CROSSING}/{loc.latitude},{loc.longitude}/{start}/{end}",
            params={
                "key": self.api_key,
                "unitGroup": "us",
                "elements": "precip",
                "include": "days",
            },
            timeout=self.timeout,
        )
        days = payload.get("days")
        if not isinstance(days, list):
            raise WeatherError("visual crossing response missing daily data")
        inches = sum(float(day.get("precip") or 0) for day in days)
        return _log_precip("visual_crossing", settings, start, end, inches)
