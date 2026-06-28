from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from rain_bypass.models import (
    FailMode,
    GpioSettings,
    LocationSettings,
    RuntimeSettings,
    SeasonSettings,
    Settings,
    WateringSettings,
    WeatherProviderName,
    WeatherSettings,
)

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib


logger = logging.getLogger(__name__)


class ConfigError(ValueError):
    """Raised when settings.toml is missing or invalid."""


def _require_table(data: dict[str, Any], name: str) -> dict[str, Any]:
    section = data.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"Missing or invalid [{name}] section")
    return section


def _require_float(section: dict[str, Any], key: str) -> float:
    if key not in section:
        raise ConfigError(f"Missing required setting: {key}")
    try:
        return float(section[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Setting {key} must be a number") from exc


def _require_int(section: dict[str, Any], key: str) -> int:
    if key not in section:
        raise ConfigError(f"Missing required setting: {key}")
    try:
        value = int(section[key])
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Setting {key} must be an integer") from exc
    if isinstance(section[key], bool):
        raise ConfigError(f"Setting {key} must be an integer")
    return value


def _optional_str(section: dict[str, Any], key: str, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Setting {key} must be a non-empty string")
    return value


def load_settings(config_path: Path | str) -> Settings:
    path = Path(config_path)
    if not path.is_file():
        raise ConfigError(
            f"Config file not found: {path}. Copy settings.example.toml to settings.toml."
        )

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    location = _require_table(data, "location")
    watering = _require_table(data, "watering")
    season = _require_table(data, "season")
    weather = _require_table(data, "weather")
    gpio = _require_table(data, "gpio")
    runtime = _require_table(data, "runtime")

    provider_raw = _optional_str(weather, "provider", WeatherProviderName.OPEN_METEO.value)
    try:
        provider = WeatherProviderName(provider_raw)
    except ValueError as exc:
        allowed = ", ".join(p.value for p in WeatherProviderName)
        raise ConfigError(f"weather.provider must be one of: {allowed}") from exc

    api_key = weather.get("visual_crossing_api_key")
    if provider is WeatherProviderName.VISUAL_CROSSING:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigError("weather.visual_crossing_api_key is required for visual_crossing")

    fail_mode_raw = _optional_str(runtime, "fail_mode", FailMode.DISABLE_WATERING.value)
    try:
        fail_mode = FailMode(fail_mode_raw)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in FailMode)
        raise ConfigError(f"runtime.fail_mode must be one of: {allowed}") from exc

    updates_per_day = _require_int(watering, "updates_per_day")
    if updates_per_day < 1:
        raise ConfigError("watering.updates_per_day must be at least 1")

    past_days = _require_int(watering, "past_days")
    if past_days < 1:
        raise ConfigError("watering.past_days must be at least 1")

    latitude = _require_float(location, "latitude")
    longitude = _require_float(location, "longitude")
    if not (-90 <= latitude <= 90):
        raise ConfigError("location.latitude must be between -90 and 90")
    if not (-180 <= longitude <= 180):
        raise ConfigError("location.longitude must be between -180 and 180")

    return Settings(
        location=LocationSettings(
            latitude=latitude,
            longitude=longitude,
            timezone=_optional_str(location, "timezone", "UTC"),
        ),
        watering=WateringSettings(
            inches_required=_require_float(watering, "inches_required"),
            past_days=past_days,
            updates_per_day=updates_per_day,
        ),
        season=SeasonSettings(
            start_month=_require_int(season, "start_month"),
            start_day=_require_int(season, "start_day"),
            end_month=_require_int(season, "end_month"),
            end_day=_require_int(season, "end_day"),
        ),
        weather=WeatherSettings(
            provider=provider,
            visual_crossing_api_key=api_key if isinstance(api_key, str) else None,
            request_timeout_seconds=_require_int(weather, "request_timeout_seconds")
            if "request_timeout_seconds" in weather
            else 30,
        ),
        gpio=GpioSettings(
            relay=_require_int(gpio, "relay"),
            watering_enabled_led=_require_int(gpio, "watering_enabled_led"),
            watering_disabled_led=_require_int(gpio, "watering_disabled_led"),
            mock=bool(gpio.get("mock", False)),
        ),
        runtime=RuntimeSettings(
            state_path=Path(_optional_str(runtime, "state_path", "state.json")),
            fail_mode=fail_mode,
            log_level=_optional_str(runtime, "log_level", "INFO").upper(),
        ),
        config_path=path,
    )


def migrate_legacy_ini(legacy_path: Path, output_path: Path) -> None:
    """Best-effort conversion from v1 settings.ini to settings.toml."""
    import configparser

    parser = configparser.ConfigParser()
    parser.read(legacy_path)
    user = parser["UserInput"]
    pins = parser["GPIO.Pins"]

    lines = [
        "# Migrated from settings.ini — review latitude/longitude before use.",
        "",
        "[location]",
        "latitude = 0.0  # REQUIRED: set your coordinates",
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
        "request_timeout_seconds = 30",
        "",
        "[gpio]",
        f"relay = {pins.get('relayswitch', '25')}",
        f"watering_enabled_led = {pins.get('wateringenabled', '4')}",
        f"watering_disabled_led = {pins.get('wateringdisabled', '27')}",
        "mock = false",
        "",
        "[runtime]",
        "state_path = \"state.json\"",
        "fail_mode = \"disable_watering\"",
        "log_level = \"INFO\"",
        "",
    ]
    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote migrated config to %s", output_path)
