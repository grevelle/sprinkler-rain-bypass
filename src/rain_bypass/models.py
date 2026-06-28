from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class FailMode(str, Enum):
    DISABLE_WATERING = "disable_watering"
    KEEP_LAST_STATE = "keep_last_state"


class WeatherProviderName(str, Enum):
    OPEN_METEO = "open_meteo"
    VISUAL_CROSSING = "visual_crossing"


@dataclass(frozen=True)
class LocationSettings:
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class WateringSettings:
    inches_required: float
    past_days: int
    updates_per_day: int


@dataclass(frozen=True)
class SeasonSettings:
    start_month: int
    start_day: int
    end_month: int
    end_day: int


@dataclass(frozen=True)
class WeatherSettings:
    provider: WeatherProviderName
    visual_crossing_api_key: str | None
    request_timeout_seconds: int


@dataclass(frozen=True)
class GpioSettings:
    relay: int
    watering_enabled_led: int
    watering_disabled_led: int
    mock: bool


@dataclass(frozen=True)
class RuntimeSettings:
    state_path: Path
    fail_mode: FailMode
    log_level: str


@dataclass(frozen=True)
class Settings:
    location: LocationSettings
    watering: WateringSettings
    season: SeasonSettings
    weather: WeatherSettings
    gpio: GpioSettings
    runtime: RuntimeSettings
    config_path: Path


@dataclass(frozen=True)
class WateringDecision:
    watering_required: bool
    rainfall_inches: float | None
    in_season: bool
    source: str
    error: str | None = None


@dataclass
class RuntimeState:
    last_weather_update: float | None = None
    watering_required: bool | None = None
    rainfall_inches: float | None = None
    last_error: str | None = None
