from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from rain_bypass.config import FailMode, Season, Settings, State, load_settings, local_today
from rain_bypass.gpio import PinFactory, watering_pins

logger = logging.getLogger(__name__)

MM_PER_INCH = 25.4
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def fetch_precip(settings: Settings) -> float:
    today = local_today(settings.location)
    days = settings.watering.past_days
    start, end = today - timedelta(days=days - 1), today
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    daily = _open_meteo_daily(loc.latitude, loc.longitude, start, end, timeout)
    return _sum_mm(daily, start, end) / MM_PER_INCH


def decide(settings: Settings, state: State) -> tuple[bool, float | None, bool, str | None]:
    """Return (watering_required, rainfall_inches, in_season, error)."""
    if not in_season(settings.season, local_today(settings.location)):
        return False, None, False, None

    try:
        rainfall = fetch_precip(settings)
    except requests.RequestException as exc:
        logger.warning("weather failed; fail_mode=%s", settings.runtime.fail_mode)
        keep = (
            settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE
            and state.watering_required is not None
        )
        return (
            state.watering_required if keep else False,
            state.rainfall_inches,
            True,
            str(exc),
        )

    required = rainfall <= settings.watering.inches_required
    return required, rainfall, True, None


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
    except requests.RequestException:
        logger.warning("weather request failed")
        raise


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
    watering_required, rainfall_inches, _, error = decide(settings, state)
    apply(watering_required)
    state = State(
        last_weather_update=time.time(),
        watering_required=watering_required,
        rainfall_inches=rainfall_inches,
        last_error=error,
    )
    state.save(settings.runtime.state_path)
    return state


def run(settings: Settings, *, once: bool = False, pin_factory: PinFactory = watering_pins) -> None:
    state = State.load(settings.runtime.state_path)
    with pin_factory(settings.gpio) as driver:
        if once:
            _tick(settings, state, driver.apply)
            return

        while True:
            state = _tick(settings, state, driver.apply)
            time.sleep(settings.watering.interval_seconds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sprinkler rain bypass controller")
    parser.add_argument("-c", "--config", type=Path, default=Path("settings.toml"))
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    logging.basicConfig(
        level=getattr(logging, settings.runtime.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    try:
        run(settings, once=args.once)
    except KeyboardInterrupt:
        logger.info("stopped")
        return 0
    except Exception:
        logger.exception("fatal error")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
