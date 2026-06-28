from __future__ import annotations

import logging
from datetime import date, timedelta

import requests

from rain_bypass.models import Settings
from rain_bypass.weather.base import (
    WeatherClient,
    WeatherError,
    local_today_in_timezone,
    mm_to_inches,
    precipitation_window_end,
)

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


class OpenMeteoClient(WeatherClient):
    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def precipitation_inches(self, settings: Settings, window_days: int) -> float:
        location = settings.location
        today = local_today_in_timezone(location.timezone)
        start, end = precipitation_window_end(today, window_days)
        payload = self._fetch_daily_precip(location.latitude, location.longitude, start, end)
        total_mm = sum(float(day.get("precipitation_sum") or 0.0) for day in payload)
        total_inches = mm_to_inches(total_mm)
        logger.info(
            "Open-Meteo precipitation %.2f in over %s days (%s to %s)",
            total_inches,
            window_days,
            start.isoformat(),
            end.isoformat(),
        )
        return total_inches

    def _fetch_daily_precip(
        self,
        latitude: float,
        longitude: float,
        start: date,
        end: date,
    ) -> list[dict[str, float | None]]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "daily": "precipitation_sum",
            "timezone": "auto",
        }

        if end >= date.today() - timedelta(days=2):
            forecast_params = {
                "latitude": latitude,
                "longitude": longitude,
                "daily": "precipitation_sum",
                "timezone": "auto",
                "past_days": max(92, (date.today() - start).days + 1),
                "forecast_days": max(0, (end - date.today()).days + 1),
            }
            response = requests.get(FORECAST_URL, params=forecast_params, timeout=self._timeout)
            response.raise_for_status()
            selected = self._select_window(response.json().get("daily", {}), start, end)
            if selected:
                return selected

        response = requests.get(ARCHIVE_URL, params=params, timeout=self._timeout)
        response.raise_for_status()
        return self._select_window(response.json().get("daily", {}), start, end)

    @staticmethod
    def _select_window(daily: dict, start: date, end: date) -> list[dict[str, float | None]]:
        dates = daily.get("time", [])
        amounts = daily.get("precipitation_sum", [])
        return [
            {"precipitation_sum": amount}
            for day, amount in zip(dates, amounts, strict=False)
            if start.isoformat() <= day <= end.isoformat()
        ]
