from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from pathlib import Path

import typer
from pydantic import BaseModel

from rain_bypass.config import (
    Balance,
    ConfigError,
    Location,
    Settings,
    Watering,
    load_example_settings,
    load_settings,
    validate_model,
)
from rain_bypass.exceptions import WeatherError
from rain_bypass.platform import is_raspberry_pi
from rain_bypass.prompting import Prompter
from rain_bypass.weather import resolve_location


@dataclass(frozen=True, slots=True)
class PromptDefaults:
    zip_code: str
    inches_per_cycle: float
    check_hour: int
    check_minute: int


def _prompt_field[M: BaseModel](model: type[M], data: object) -> M:
    try:
        return validate_model(model, data)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from None


def validate_api_key(key: str) -> str:
    text = key.strip()
    if not text:
        raise typer.BadParameter("API key cannot be empty.")
    if len(text) < 10:
        raise typer.BadParameter(
            "API key looks too short; paste the full key from Visual Crossing."
        )
    if '"' in text or "\\" in text:
        raise typer.BadParameter("API key contains invalid characters for settings.toml.")
    return text


def parse_zip_code(value: str) -> str:
    return _prompt_field(
        Location,
        {"zip_code": value.strip(), "latitude": 0, "longitude": 0, "timezone": "UTC"},
    ).zip_code


def parse_inches_per_cycle(value: str) -> float:
    return _prompt_field(Balance, {"inches_per_cycle": value.strip()}).inches_per_cycle


def parse_check_time(value: str) -> tuple[int, int]:
    text = value.strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if not match:
        raise typer.BadParameter("Check time must be HH:MM in 24-hour format (e.g. 00:00).")
    watering = _prompt_field(
        Watering,
        {"check_hour": int(match.group(1)), "check_minute": int(match.group(2))},
    )
    return watering.check_hour, watering.check_minute


def prompt_defaults_from(settings: Settings) -> PromptDefaults:
    return PromptDefaults(
        zip_code=settings.location.zip_code,
        inches_per_cycle=settings.balance.inches_per_cycle,
        check_hour=settings.watering.check_hour,
        check_minute=settings.watering.check_minute,
    )


def load_settings_base(settings_path: Path) -> Settings:
    if settings_path.is_file():
        with contextlib.suppress(ConfigError, OSError):
            return load_settings(settings_path)
    return load_example_settings()


def load_prompt_defaults(settings_path: Path) -> tuple[str | None, PromptDefaults]:
    base = load_settings_base(settings_path)
    key: str | None = None
    if settings_path.is_file():
        with contextlib.suppress(ConfigError, OSError):
            key = load_settings(settings_path).weather.api_key
    return key, prompt_defaults_from(base)


def apply_prompt_overrides(
    base: Settings,
    *,
    location: Location,
    api_key: str,
    inches_per_cycle: float,
    check_hour: int,
    check_minute: int,
    gpio_mock: bool,
) -> Settings:
    return base.model_copy(
        update={
            "location": location,
            "weather": base.weather.model_copy(update={"api_key": api_key}),
            "balance": base.balance.model_copy(update={"inches_per_cycle": inches_per_cycle}),
            "watering": base.watering.model_copy(
                update={"check_hour": check_hour, "check_minute": check_minute}
            ),
            "gpio": base.gpio.model_copy(update={"mock": gpio_mock}),
        }
    )


def build_settings(
    api_key: str,
    zip_code: str,
    *,
    base: Settings | None,
    inches_per_cycle: float,
    check_hour: int,
    check_minute: int,
    gpio_mock: bool | None = None,
) -> Settings:
    key = validate_api_key(api_key)
    zip5 = parse_zip_code(zip_code)
    mock = not is_raspberry_pi() if gpio_mock is None else gpio_mock
    try:
        location = resolve_location(zip5, key)
    except WeatherError as exc:
        raise typer.BadParameter(str(exc)) from None
    settings_base = base or load_example_settings()
    return apply_prompt_overrides(
        settings_base,
        location=location,
        api_key=key,
        inches_per_cycle=inches_per_cycle,
        check_hour=check_hour,
        check_minute=check_minute,
        gpio_mock=mock,
    )


def prompt_watering_profile(prompter: Prompter, defaults: PromptDefaults) -> tuple[float, int, int]:
    typer.echo(
        "\n==> Sprinkler program — one daily run; calibrate inches per cycle with catch cups later if needed."
    )
    inches = parse_inches_per_cycle(
        prompter.text(
            "Inches per cycle (from settings.example.toml)",
            default=f"{defaults.inches_per_cycle:g}",
        )
    )
    check_hour, check_minute = parse_check_time(
        prompter.text(
            "Daily check time before irrigation (HH:MM, 24h)",
            default=f"{defaults.check_hour:02d}:{defaults.check_minute:02d}",
        )
    )
    return inches, check_hour, check_minute


def prompt_settings(
    base: Settings,
    prompter: Prompter,
    *,
    existing_key: str | None = None,
    defaults: PromptDefaults,
) -> Settings:
    typer.echo(
        "\n==> Press Enter for defaults from your existing settings or settings.example.toml.\n"
    )
    typer.echo("==> Visual Crossing API (free key: https://www.visualcrossing.com/weather-api)")
    while True:
        if existing_key and prompter.confirm(
            "Keep existing Visual Crossing API key?", default=True
        ):
            api_key = existing_key
        else:
            api_key = prompter.secret("Visual Crossing API key")
        zip_code = prompter.text("ZIP code", default=defaults.zip_code)
        if not is_raspberry_pi():
            typer.secho(
                "warning: Raspberry Pi not detected; gpio.mock=true for this machine.",
                fg=typer.colors.YELLOW,
            )
        try:
            inches, check_hour, check_minute = prompt_watering_profile(prompter, defaults)
            return build_settings(
                api_key,
                zip_code,
                base=base,
                inches_per_cycle=inches,
                check_hour=check_hour,
                check_minute=check_minute,
            )
        except typer.BadParameter as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            typer.echo("Try again.\n")
            existing_key = None
