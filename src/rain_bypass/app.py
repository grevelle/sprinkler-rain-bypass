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

VISUAL_CROSSING = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)
# Timeline docs: include=days + elements limits payload and queryCost (~1 record/day).
TIMELINE_QUERY = {
    "unitGroup": "us",
    "elements": "datetime,precip",
    "include": "days",
}


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def precip_window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    past_days = settings.watering.past_days
    return today - timedelta(days=past_days - 1), today


def fetch_precip(settings: Settings) -> float:
    start, end = precip_window(settings)
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    payload = _get_timeline(
        loc.latitude,
        loc.longitude,
        start,
        end,
        api_key=settings.weather.api_key,
        timeout=timeout,
    )
    _log_timeline_meta(payload, settings)
    daily = payload.get("days")
    if not isinstance(daily, list):
        raise requests.RequestException("visual crossing response missing daily data")
    inches = _sum_precip(daily, start, end)
    logger.info(
        "visual_crossing precipitation %.2f in over %s days (%s to %s)",
        inches,
        settings.watering.past_days,
        start,
        end,
    )
    return inches


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


def _timeline_url(lat: float, lon: float, start: date, end: date) -> str:
    return f"{VISUAL_CROSSING}/{lat},{lon}/{start}/{end}"


def _get_timeline(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    api_key: str,
    timeout: int,
) -> dict:
    return _get(
        _timeline_url(lat, lon, start, end),
        params={**TIMELINE_QUERY, "key": api_key},
        timeout=timeout,
    )


def _get(url: str, *, params: dict, timeout: int) -> dict:
    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        logger.warning("weather request failed status=%s", status)
        if status == 401:
            raise requests.RequestException(
                "visual crossing unauthorized; check api_key"
            ) from exc
        if status == 429:
            raise requests.RequestException("visual crossing rate limit exceeded") from exc
        raise requests.RequestException(f"visual crossing HTTP {status}") from exc
    except requests.RequestException:
        logger.warning("weather request failed")
        raise

    try:
        return response.json()
    except ValueError as exc:
        raise requests.RequestException("visual crossing returned invalid JSON") from exc


def _log_timeline_meta(payload: dict, settings: Settings) -> None:
    query_cost = payload.get("queryCost")
    if query_cost is not None:
        logger.debug("visual_crossing queryCost=%s", query_cost)

    api_tz = payload.get("timezone")
    config_tz = settings.location.timezone
    if api_tz and api_tz != config_tz:
        logger.warning(
            "location timezone %s differs from visual crossing %s; window uses config timezone",
            config_tz,
            api_tz,
        )


def _sum_precip(days: list, start: date, end: date) -> float:
    if not days:
        raise requests.RequestException("visual crossing returned no daily rows")

    start_s, end_s = start.isoformat(), end.isoformat()
    total = 0.0
    matched = 0
    for day in days:
        raw = day.get("datetime")
        if not raw:
            raise requests.RequestException("visual crossing day missing datetime")
        day_s = str(raw)[:10]
        if start_s <= day_s <= end_s:
            total += float(day.get("precip") or 0)
            matched += 1

    expected = (end - start).days + 1
    if matched != expected:
        logger.warning(
            "visual_crossing returned %s days in window, expected %s",
            matched,
            expected,
        )
    return total


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
