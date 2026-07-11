from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import tomli_w
import tomllib
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rain_bypass.paths import repo_root

EXAMPLE_SETTINGS_PATH = repo_root() / "settings.example.toml"

DEFAULT_MONTHLY_TARGETS: dict[int, float] = {
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 3.0,
    5: 5.0,
    6: 6.5,
    7: 5.0,
    8: 5.0,
    9: 4.0,
    10: 2.0,
    11: 1.0,
    12: 0.0,
}


class ConfigError(ValueError):
    """Invalid or missing settings file."""


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FailMode(StrEnum):
    DISABLE_WATERING = "disable_watering"
    KEEP_LAST_STATE = "keep_last_state"


class Location(FrozenModel):
    zip_code: str = Field(min_length=5, max_length=10)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = "UTC"

    @field_validator("zip_code")
    @classmethod
    def normalize_zip_code(cls, value: str) -> str:
        text = value.strip()
        if not re.fullmatch(r"\d{5}(?:-\d{4})?", text):
            raise ValueError("zip_code must be 5 digits or ZIP+4")
        return text[:5]


class BalanceMonth(FrozenModel):
    target_inches_per_month: float = Field(ge=0)


def default_monthly_table() -> dict[int, BalanceMonth]:
    return {
        month: BalanceMonth(target_inches_per_month=inches)
        for month, inches in DEFAULT_MONTHLY_TARGETS.items()
    }


class Balance(FrozenModel):
    inches_per_cycle: float = Field(gt=0)
    forecast_days: int = Field(default=2, ge=0)
    monthly: dict[int, BalanceMonth] = Field(default_factory=default_monthly_table)

    @field_validator("monthly", mode="before")
    @classmethod
    def merge_monthly_overrides(cls, value: object) -> dict[int, BalanceMonth]:
        base = default_monthly_table()
        if not isinstance(value, dict):
            return base
        overrides = cast(dict[str | int, object], value)
        if not overrides:
            return base
        merged = dict(base)
        for key, item in overrides.items():
            month = int(key)
            if isinstance(item, BalanceMonth):
                merged[month] = item
            else:
                merged[month] = BalanceMonth.model_validate(item)
        return merged


class Watering(FrozenModel):
    check_hour: int = Field(default=0, ge=0, le=23)
    check_minute: int = Field(default=0, ge=0, le=59)
    event_lookback_days: int = Field(default=3, ge=1)
    event_inches: float = Field(default=0.25, ge=0)
    freeze_temp_f: float = Field(default=32)


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
    history_path: Path | None = None
    fail_mode: FailMode = FailMode.DISABLE_WATERING
    log_level: str = "INFO"
    weather_timeout_seconds: int = Field(default=45, ge=1)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()


class Web(FrozenModel):
    host: str = "0.0.0.0"
    port: int = Field(default=80, ge=1, le=65535)


class Settings(FrozenModel):
    location: Location
    balance: Balance
    watering: Watering
    sewer: SewerLockout = Field(default_factory=SewerLockout)
    weather: Weather
    gpio: Gpio
    runtime: Runtime
    web: Web = Field(default_factory=Web)


class State(FrozenModel):
    last_weather_update: float | None = None
    watering_required: bool | None = None
    rainfall_inches: float | None = None
    forecast_inches: float | None = None
    max_daily_inches: float | None = None
    freeze_block: bool | None = None
    last_error: str | None = None

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.is_file():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data = cast(dict[str, Any], raw)
            data.pop("blocked_until", None)
            data.pop("irrigation_inches_mtd", None)
            data.pop("balance_month", None)
            return cls.model_validate(data)
        return cls.model_validate(raw)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")


def local_today(location: Location) -> date:
    return datetime.now(ZoneInfo(location.timezone)).date()


def in_sewer_lockout(sewer: SewerLockout, today: date) -> bool:
    start = date(today.year, sewer.start_month, sewer.start_day)
    end = date(today.year, sewer.end_month, sewer.end_day)
    return start <= today <= end


def format_sewer_range(sewer: SewerLockout) -> str:
    return (
        f"{sewer.start_month:02d}/{sewer.start_day:02d}-{sewer.end_month:02d}/{sewer.end_day:02d}"
    )


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


def validate_model[M: BaseModel](model: type[M], data: object) -> M:
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(str(exc.errors()[0]["msg"])) from None


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        existing = base.get(key)
        if isinstance(value, Mapping) and isinstance(existing, dict):
            _deep_merge(
                cast(dict[str, Any], existing),
                cast(Mapping[str, Any], value),
            )
        else:
            base[key] = value
    return base


def _load_example_data(**sections: Mapping[str, Any]) -> dict[str, Any]:
    if not EXAMPLE_SETTINGS_PATH.is_file():
        raise FileNotFoundError(f"Example settings not found: {EXAMPLE_SETTINGS_PATH}")
    with EXAMPLE_SETTINGS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return _deep_merge(data, sections)


def load_example_settings(**sections: Mapping[str, Any]) -> Settings:
    return Settings.model_validate(_load_example_data(**sections))


def settings_to_toml_dict(settings: Settings) -> dict[str, Any]:
    data = settings.model_dump()
    runtime = dict(data["runtime"])
    runtime["state_path"] = str(settings.runtime.state_path)
    if settings.runtime.history_path is None:
        runtime.pop("history_path", None)
    else:
        runtime["history_path"] = str(settings.runtime.history_path)
    data["runtime"] = runtime
    balance = dict(data["balance"])
    balance.pop("monthly", None)
    data["balance"] = balance
    return data


def write_settings(path: Path | str, settings: Settings) -> None:
    target = Path(path)
    target.write_text(tomli_w.dumps(settings_to_toml_dict(settings)), encoding="utf-8")
    if os.name == "posix":
        os.chmod(target, 0o600)
