from __future__ import annotations

import tomllib
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(ValueError):
    """Invalid or missing settings file."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FailMode(StrEnum):
    DISABLE_WATERING = "disable_watering"
    KEEP_LAST_STATE = "keep_last_state"


class Location(FrozenModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = "UTC"


class Watering(FrozenModel):
    inches_required: float
    past_days: int = Field(ge=1)
    forecast_days: int = Field(default=2, ge=0)
    forecast_inches_max: float = Field(default=0.5, ge=0)
    event_inches: float = Field(default=0.25, ge=0)
    rain_delay_days: int = Field(default=2, ge=0)
    near_term_hours: int = Field(default=24, ge=0)
    near_term_inches_max: float = Field(default=0.25, ge=0)
    freeze_skip: bool = Field(default=True)
    freeze_temp_f: float = Field(default=32)
    check_hour: int = Field(default=4, ge=0, le=23)
    check_minute: int = Field(default=30, ge=0, le=59)
    updates_per_day: int = Field(ge=1)

    @property
    def interval_seconds(self) -> float:
        return 86400 / self.updates_per_day


class SewerLockout(FrozenModel):
    """Hard block window — city sewer bills often use winter water use as the annual cap."""

    start_month: int = Field(default=1, ge=1, le=12)
    start_day: int = Field(default=16, ge=1, le=31)
    end_month: int = Field(default=3, ge=1, le=12)
    end_day: int = Field(default=15, ge=1, le=31)


class Weather(FrozenModel):
    api_key: str = Field(min_length=1)


class Gpio(FrozenModel):
    relay: int
    watering_enabled_led: int
    watering_disabled_led: int
    mock: bool = False


class Runtime(FrozenModel):
    state_path: Path = Path("state.json")
    fail_mode: FailMode = FailMode.DISABLE_WATERING
    log_level: str = "INFO"
    weather_timeout_seconds: int = Field(default=45, ge=1)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


class Settings(FrozenModel):
    location: Location
    watering: Watering
    sewer: SewerLockout = Field(default_factory=SewerLockout)
    weather: Weather
    gpio: Gpio
    runtime: Runtime


class State(FrozenModel):
    last_weather_update: float | None = None
    watering_required: bool | None = None
    rainfall_inches: float | None = None
    forecast_inches: float | None = None
    blocked_until: date | None = None
    last_error: str | None = None

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.is_file():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


def local_today(location: Location) -> date:
    return datetime.now(ZoneInfo(location.timezone)).date()


def in_sewer_lockout(sewer: SewerLockout, today: date) -> bool:
    start = date(today.year, sewer.start_month, sewer.start_day)
    end = date(today.year, sewer.end_month, sewer.end_day)
    return start <= today <= end


def load_settings(config_path: Path | str) -> Settings:
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}. Copy settings.example.toml to settings.toml.")

    with path.open("rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(str(exc)) from exc

    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
