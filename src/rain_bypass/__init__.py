"""Sprinkler rain bypass — rain-aware GPIO controller for irrigation systems."""

from importlib.metadata import PackageNotFoundError, version

from rain_bypass.cli import app
from rain_bypass.config import ConfigError, FailMode, Settings, State, load_settings
from rain_bypass.controller import run
from rain_bypass.exceptions import WeatherError
from rain_bypass.logic import allow_watering, decide, past_ok, watering_required
from rain_bypass.models import Decision, WeatherSnapshot
from rain_bypass.weather import fetch_weather, weather_api_smoke

try:
    __version__ = version("sprinkler-rain-bypass")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0"

__all__ = [
    "ConfigError",
    "Decision",
    "FailMode",
    "Settings",
    "State",
    "WeatherError",
    "WeatherSnapshot",
    "__version__",
    "allow_watering",
    "app",
    "decide",
    "fetch_weather",
    "load_settings",
    "past_ok",
    "run",
    "watering_required",
    "weather_api_smoke",
]
