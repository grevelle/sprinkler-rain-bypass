from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

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


class PrecipTotals(NamedTuple):
    past_inches: float
    forecast_inches: float
    max_daily_inches: float


def in_season(season: Season, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    return start <= today <= end if start <= end else today >= start or today <= end


def past_window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    past_days = settings.watering.past_days
    return today - timedelta(days=past_days - 1), today


def forecast_window(settings: Settings) -> tuple[date, date] | None:
    forecast_days = settings.watering.forecast_days
    if forecast_days <= 0:
        return None
    today = local_today(settings.location)
    return today + timedelta(days=1), today + timedelta(days=forecast_days)


def timeline_window(settings: Settings) -> tuple[date, date]:
    past_start, past_end = past_window(settings)
    forecast = forecast_window(settings)
    if forecast is None:
        return past_start, past_end
    return past_start, forecast[1]


def precip_window(settings: Settings) -> tuple[date, date]:
    """Past lookback window (backward compatible alias)."""
    return past_window(settings)


def past_ok(totals: PrecipTotals, settings: Settings) -> bool:
    w = settings.watering
    if totals.past_inches > w.inches_required:
        return False
    if w.event_inches > 0 and totals.max_daily_inches >= w.event_inches:
        return False
    return True


def allow_watering(totals: PrecipTotals, settings: Settings) -> bool:
    w = settings.watering
    forecast_ok = totals.forecast_inches <= w.forecast_inches_max
    return past_ok(totals, settings) and forecast_ok


def update_blocked_until(
    today: date,
    *,
    past_ok_flag: bool,
    rain_delay_days: int,
    blocked_until: date | None,
) -> date | None:
    if rain_delay_days <= 0:
        return None
    if not past_ok_flag:
        candidate = today + timedelta(days=rain_delay_days)
        return candidate if blocked_until is None else max(blocked_until, candidate)
    if blocked_until and today > blocked_until:
        return None
    return blocked_until


def watering_required(
    today: date,
    weather_allow: bool,
    blocked_until: date | None,
) -> bool:
    if blocked_until and today <= blocked_until:
        return False
    return weather_allow


def seconds_until_next_check(settings: Settings, *, now: datetime | None = None) -> float:
    if settings.watering.updates_per_day != 1:
        return settings.watering.interval_seconds

    tz = ZoneInfo(settings.location.timezone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        current = current.replace(tzinfo=tz)
    else:
        current = current.astimezone(tz)

    w = settings.watering
    target = current.replace(
        hour=w.check_hour,
        minute=w.check_minute,
        second=0,
        microsecond=0,
    )
    if current >= target:
        target += timedelta(days=1)
    return (target - current).total_seconds()


def fetch_precip(settings: Settings) -> PrecipTotals:
    api_start, api_end = timeline_window(settings)
    past_start, past_end = past_window(settings)
    forecast = forecast_window(settings)
    loc = settings.location
    timeout = settings.runtime.weather_timeout_seconds
    payload = _get_timeline(
        loc.latitude,
        loc.longitude,
        api_start,
        api_end,
        api_key=settings.weather.api_key,
        timeout=timeout,
    )
    _log_timeline_meta(payload, settings)
    daily = payload.get("days")
    if not isinstance(daily, list):
        raise requests.RequestException("visual crossing response missing daily data")

    past_inches = _sum_precip(daily, past_start, past_end)
    max_daily = _max_daily_precip(daily, past_start, past_end)
    if forecast is None:
        forecast_inches = 0.0
        forecast_start = forecast_end = None
    else:
        forecast_start, forecast_end = forecast
        forecast_inches = _sum_precip(daily, forecast_start, forecast_end)

    w = settings.watering
    if forecast is None:
        logger.info(
            "visual_crossing past %.2f in (max day %.2f, %s to %s); "
            "allow if past <= %.2f in and no day >= %.2f in",
            past_inches,
            max_daily,
            past_start,
            past_end,
            w.inches_required,
            w.event_inches,
        )
    else:
        logger.info(
            "visual_crossing past %.2f in (max day %.2f, %s to %s), "
            "forecast %.2f in (%s to %s); allow if past <= %.2f, no day >= %.2f, "
            "forecast <= %.2f",
            past_inches,
            max_daily,
            past_start,
            past_end,
            forecast_inches,
            forecast_start,
            forecast_end,
            w.inches_required,
            w.event_inches,
            w.forecast_inches_max,
        )
    return PrecipTotals(past_inches, forecast_inches, max_daily)


def decide(
    settings: Settings, state: State
) -> tuple[bool, float | None, float | None, date | None, bool, str | None]:
    """Return (watering_required, past_inches, forecast_inches, blocked_until, in_season, error)."""
    if not in_season(settings.season, local_today(settings.location)):
        return False, None, None, state.blocked_until, False, None

    today = local_today(settings.location)
    blocked_until = state.blocked_until

    try:
        totals = fetch_precip(settings)
    except requests.RequestException as exc:
        logger.warning("weather failed; fail_mode=%s", settings.runtime.fail_mode)
        keep = (
            settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE
            and state.watering_required is not None
        )
        return (
            state.watering_required if keep else False,
            state.rainfall_inches,
            state.forecast_inches,
            blocked_until,
            True,
            str(exc),
        )

    weather_allow = allow_watering(totals, settings)
    blocked_until = update_blocked_until(
        today,
        past_ok_flag=past_ok(totals, settings),
        rain_delay_days=settings.watering.rain_delay_days,
        blocked_until=blocked_until,
    )
    required = watering_required(today, weather_allow, blocked_until)
    if blocked_until and today <= blocked_until and weather_allow:
        logger.info("rain delay active through %s; watering blocked", blocked_until)
    return required, totals.past_inches, totals.forecast_inches, blocked_until, True, None


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


def _max_daily_precip(days: list, start: date, end: date) -> float:
    start_s, end_s = start.isoformat(), end.isoformat()
    peak = 0.0
    for day in days:
        raw = day.get("datetime")
        if not raw:
            continue
        day_s = str(raw)[:10]
        if start_s <= day_s <= end_s:
            peak = max(peak, float(day.get("precip") or 0))
    return peak


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
    watering_required_flag, rainfall_inches, forecast_inches, blocked_until, _, error = decide(
        settings, state
    )
    apply(watering_required_flag)
    state = State(
        last_weather_update=time.time(),
        watering_required=watering_required_flag,
        rainfall_inches=rainfall_inches,
        forecast_inches=forecast_inches,
        blocked_until=blocked_until,
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
            time.sleep(seconds_until_next_check(settings))


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
