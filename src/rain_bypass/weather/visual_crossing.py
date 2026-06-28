from __future__ import annotations

import logging
from datetime import date

import requests

from rain_bypass.models import Settings
from rain_bypass.weather.base import (
    WeatherClient,
    WeatherError,
    local_today_in_timezone,
    precipitation_window_end,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"


class VisualCrossingClient(WeatherClient):
    def __init__(self, api_key: str, timeout: int = 30) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def precipitation_inches(self, settings: Settings, window_days: int) -> float:
        location = settings.location
        today = local_today_in_timezone(location.timezone)
        start, end = precipitation_window_end(today, window_days)

        url = (
            f"{BASE_URL}/{location.latitude},{location.longitude}/"
            f"{start.isoformat()}/{end.isoformat()}"
        )
        response = requests.get(
            url,
            params={
                "key": self._api_key,
                "unitGroup": "us",
                "elements": "precip",
                "include": "days",
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        days = payload.get("days")
        if not isinstance(days, list):
            raise WeatherError("Visual Crossing response did not include daily data")

        total_inches = sum(float(day.get("precip") or 0.0) for day in days)
        logger.info(
            "Visual Crossing precipitation %.2f in over %s days (%s to %s)",
            total_inches,
            window_days,
            start.isoformat(),
            end.isoformat(),
        )
        return total_inches
