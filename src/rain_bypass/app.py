from __future__ import annotations

import logging
import time
from datetime import date, timedelta

import requests

from rain_bypass.config import FailMode, FrozenModel, Season, Settings, State, local_today
from rain_bypass.gpio import PinFactory, watering_pins

logger = logging.getLogger(__name__)

MM_PER_INCH = 25.4
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


class WeatherError(RuntimeError):
    pass


class Decision(FrozenModel):
    watering_required: bool
    rainfall_inches: float | None
    in_season: bool
    error: str | None = None


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def precip_window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    days = settings.watering.past_days
    return today - timedelta(days=days - 1), today


def fetch_precip(settings: Settings) -> float:
    start, end = precip_window(settings)
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    daily = _open_meteo_daily(loc.latitude, loc.longitude, start, end, timeout)
    inches = _sum_mm(daily, start, end) / MM_PER_INCH
    logger.info(
        "precipitation %.2f in over %s days (%s to %s)",
        inches,
        settings.watering.past_days,
        start,
        end,
    )
    return inches


def decide(settings: Settings, state: State) -> Decision:
    if not in_season(settings.season, local_today(settings.location)):
        logger.info("outside watering season")
        return Decision(watering_required=False, rainfall_inches=None, in_season=False)

    try:
        rainfall = fetch_precip(settings)
    except WeatherError as exc:
        logger.warning("weather failed; fail_mode=%s", settings.runtime.fail_mode)
        return _fallback(settings, state, str(exc))

    required = rainfall <= settings.watering.inches_required
    logger.info(
        "rainfall %.2f in (threshold %.2f in) -> watering %s",
        rainfall,
        settings.watering.inches_required,
        "required" if required else "blocked",
    )
    return Decision(watering_required=required, rainfall_inches=rainfall, in_season=True)


def _fallback(settings: Settings, state: State, message: str) -> Decision:
    keep = (
        settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE
        and state.watering_required is not None
    )
    return Decision(
        watering_required=state.watering_required if keep else False,
        rainfall_inches=state.rainfall_inches,
        in_season=True,
        error=message,
    )


def _open_meteo_daily(lat: float, lon: float, start: date, end: date, timeout: int) -> dict:
    base = {"latitude": lat, "longitude": lon, "daily": "precipitation_sum", "timezone": "auto"}
    if end >= date.today() - timedelta(days=2):
        payload = _get(
            FORECAST_URL,
            params={
                **base,
                "past_days": max(92, (date.today() - start).days + 1),
                "forecast_days": max(0, (end - date.today()).days + 1),
            },
            timeout=timeout,
        )
        if payload.get("daily", {}).get("time"):
            return payload["daily"]
    payload = _get(
        ARCHIVE_URL,
        params={**base, "start_date": start.isoformat(), "end_date": end.isoformat()},
        timeout=timeout,
    )
    return payload.get("daily", {})


def _get(url: str, *, params: dict, timeout: int) -> dict:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        logger.warning("weather request failed")
        raise WeatherError("weather request failed") from exc


def _sum_mm(daily: dict, start: date, end: date) -> float:
    dates = daily.get("time", [])
    amounts = daily.get("precipitation_sum", [])
    start_s, end_s = start.isoformat(), end.isoformat()
    return sum(
        float(amount or 0)
        for day, amount in zip(dates, amounts, strict=False)
        if start_s <= day <= end_s
    )


def _tick(settings: Settings, state: State, apply) -> State:
    decision = decide(settings, state)
    apply(decision.watering_required)
    state = State(
        last_weather_update=time.time(),
        watering_required=decision.watering_required,
        rainfall_inches=decision.rainfall_inches,
        last_error=decision.error,
    )
    state.save(settings.runtime.state_path)
    return state


def run(settings: Settings, *, once: bool = False, pin_factory: PinFactory = watering_pins) -> None:
    state = State.load(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        if once:
            _tick(settings, state, driver.apply)
            return

        logger.info("starting loop (interval %.0fs)", settings.watering.interval_seconds)
        while True:
            state = _tick(settings, state, driver.apply)
            time.sleep(settings.watering.interval_seconds)
