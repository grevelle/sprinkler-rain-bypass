from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ConfigError(ValueError):
    """Invalid or missing settings file."""


class FailMode(StrEnum):
    DISABLE_WATERING = "disable_watering"
    KEEP_LAST_STATE = "keep_last_state"


class Provider(StrEnum):
    OPEN_METEO = "open_meteo"
    VISUAL_CROSSING = "visual_crossing"


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = "UTC"


class Watering(BaseModel):
    model_config = ConfigDict(frozen=True)
    inches_required: float
    past_days: int = Field(ge=1)
    updates_per_day: int = Field(ge=1)

    @property
    def interval_seconds(self) -> float:
        return 86400 / self.updates_per_day


class Season(BaseModel):
    model_config = ConfigDict(frozen=True)
    start_month: int = Field(ge=1, le=12)
    start_day: int = Field(ge=1, le=31)
    end_month: int = Field(ge=1, le=12)
    end_day: int = Field(ge=1, le=31)


class Weather(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: Provider = Provider.OPEN_METEO
    visual_crossing_api_key: str | None = None
    request_timeout_seconds: int = Field(default=30, ge=1)

    @model_validator(mode="after")
    def require_api_key_for_visual_crossing(self) -> Weather:
        if self.provider is Provider.VISUAL_CROSSING and not self.visual_crossing_api_key:
            raise ValueError("visual_crossing_api_key is required when provider is visual_crossing")
        return self


class Gpio(BaseModel):
    model_config = ConfigDict(frozen=True)
    relay: int
    watering_enabled_led: int
    watering_disabled_led: int
    mock: bool = False


class Runtime(BaseModel):
    model_config = ConfigDict(frozen=True)
    state_path: Path = Path("state.json")
    fail_mode: FailMode = FailMode.DISABLE_WATERING
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)
    location: Location
    watering: Watering
    season: Season
    weather: Weather
    gpio: Gpio
    runtime: Runtime


def load_settings(config_path: Path | str) -> Settings:
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(f"Config not found: {path}. Copy settings.example.toml to settings.toml.")

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    try:
        return Settings.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc


def migrate_legacy_ini(legacy_path: Path, output_path: Path) -> None:
    import configparser

    parser = configparser.ConfigParser()
    parser.read(legacy_path)
    user, pins = parser["UserInput"], parser["GPIO.Pins"]

    output_path.write_text(
        "\n".join(
            [
                "# Migrated from settings.ini — set latitude and longitude before use.",
                "",
                "[location]",
                "latitude = 0.0",
                "longitude = 0.0",
                f"timezone = \"{user.get('timezone', 'UTC')}\"",
                "",
                "[watering]",
                f"inches_required = {user.get('inchesrequired', '0.6')}",
                f"past_days = {user.get('raindays', '7')}",
                f"updates_per_day = {user.get('weatherupdatesperday', '1')}",
                "",
                "[season]",
                f"start_month = {user.get('firstmonthtowater', '3')}",
                f"start_day = {user.get('firstdaytowater', '19')}",
                f"end_month = {user.get('lastmonthtowater', '9')}",
                f"end_day = {user.get('lastdaytowater', '12')}",
                "",
                "[weather]",
                "provider = \"visual_crossing\"",
                f"visual_crossing_api_key = \"{user.get('visualcrossingkey', '')}\"",
                "",
                "[gpio]",
                f"relay = {pins.get('relayswitch', '25')}",
                f"watering_enabled_led = {pins.get('wateringenabled', '4')}",
                f"watering_disabled_led = {pins.get('wateringdisabled', '27')}",
                "",
                "[runtime]",
                "",
            ]
        ),
        encoding="utf-8",
    )
