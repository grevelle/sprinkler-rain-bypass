from __future__ import annotations

import logging

import requests

from rain_bypass.models import Settings, WeatherProviderName
from rain_bypass.weather.base import WeatherClient, WeatherError
from rain_bypass.weather.open_meteo import OpenMeteoClient
from rain_bypass.weather.visual_crossing import VisualCrossingClient

logger = logging.getLogger(__name__)


def build_weather_client(settings: Settings) -> WeatherClient:
    if settings.weather.provider is WeatherProviderName.OPEN_METEO:
        return OpenMeteoClient(timeout=settings.weather.request_timeout_seconds)
    if settings.weather.provider is WeatherProviderName.VISUAL_CROSSING:
        if not settings.weather.visual_crossing_api_key:
            raise WeatherError("Visual Crossing API key is not configured")
        return VisualCrossingClient(
            api_key=settings.weather.visual_crossing_api_key,
            timeout=settings.weather.request_timeout_seconds,
        )
    raise WeatherError(f"Unsupported weather provider: {settings.weather.provider}")


def fetch_precipitation_inches(client: WeatherClient, settings: Settings) -> float:
    try:
        return client.precipitation_inches(settings, settings.watering.past_days)
    except WeatherError:
        raise
    except requests.RequestException as exc:
        logger.warning("Weather request failed")
        raise WeatherError("Weather provider request failed") from exc
    except Exception as exc:
        logger.warning("Weather provider returned an unexpected error")
        raise WeatherError("Weather provider request failed") from exc
