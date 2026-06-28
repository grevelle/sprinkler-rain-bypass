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

from rain_bypass.config import (
    FailMode,
    Settings,
    State,
    in_season,
    in_sewer_baseline_window,
    load_settings,
    local_today,
)
from rain_bypass.gpio import PinFactory, watering_pins

logger = logging.getLogger(__name__)

VISUAL_CROSSING = (
    "https://weather.visualcrossing.com/VisualCrossingWebServices/rest/services/timeline"
)
TIMELINE_BASE = {"unitGroup": "us"}


class WeatherSnapshot(NamedTuple):
    past_inches: float
    forecast_inches: float
    max_daily_inches: float
    near_term_inches: float
    freeze_block: bool


# Backward-compatible alias for tests and callers expecting precip totals only.
PrecipTotals = WeatherSnapshot


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


def local_now(settings: Settings, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.location.timezone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        return current.replace(tzinfo=tz)
    return current.astimezone(tz)


def near_term_window(
    settings: Settings, now: datetime | None = None
) -> tuple[datetime, datetime] | None:
    hours = settings.watering.near_term_hours
    if hours <= 0:
        return None
    start = local_now(settings, now)
    return start, start + timedelta(hours=hours)


def timeline_params(settings: Settings) -> dict:
    w = settings.watering
    elements = ["datetime", "precip"]
    includes = ["days"]
    if w.freeze_skip:
        elements.append("tempmin")
    if w.near_term_hours > 0:
        includes.append("hours")
    return {**TIMELINE_BASE, "elements": ",".join(elements), "include": ",".join(includes)}


def past_ok(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    w = settings.watering
    if snapshot.past_inches > w.inches_required:
        return False
    if w.event_inches > 0 and snapshot.max_daily_inches >= w.event_inches:
        return False
    return True


def allow_watering(snapshot: WeatherSnapshot, settings: Settings) -> bool:
    w = settings.watering
    if snapshot.freeze_block:
        return False
    if w.near_term_hours > 0 and snapshot.near_term_inches > w.near_term_inches_max:
        return False
    if snapshot.forecast_inches > w.forecast_inches_max:
        return False
    return past_ok(snapshot, settings)


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

    current = local_now(settings, now)
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


def fetch_precip(settings: Settings) -> WeatherSnapshot:
    return fetch_weather(settings)


def fetch_weather(settings: Settings, *, now: datetime | None = None) -> WeatherSnapshot:
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
        settings=settings,
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

    today = local_today(loc)
    freeze_block = _freeze_block(daily, settings, today)
    near_term_inches = 0.0
    window = near_term_window(settings, now)
    if window is not None:
        hourly = payload.get("hours")
        if not isinstance(hourly, list):
            raise requests.RequestException("visual crossing response missing hourly data")
        near_term_inches = _sum_precip_hours(hourly, window[0], window[1], loc.timezone)

    w = settings.watering
    logger.info(
        "visual_crossing past %.2f in (max day %.2f), forecast %.2f in, "
        "near_term %.2f in / %sh, freeze_block=%s; "
        "allow if past<=%.2f, event>=%.2f blocks, forecast<=%.2f, "
        "near_term<=%.2f, freeze_skip=%s",
        past_inches,
        max_daily,
        forecast_inches,
        near_term_inches,
        w.near_term_hours,
        freeze_block,
        w.inches_required,
        w.event_inches,
        w.forecast_inches_max,
        w.near_term_inches_max,
        w.freeze_skip,
    )
    if forecast_start is not None:
        logger.debug(
            "windows past=%s..%s forecast=%s..%s",
            past_start,
            past_end,
            forecast_start,
            forecast_end,
        )
    return WeatherSnapshot(
        past_inches,
        forecast_inches,
        max_daily,
        near_term_inches,
        freeze_block,
    )


def decide(
    settings: Settings, state: State
) -> tuple[bool, float | None, float | None, date | None, bool, str | None]:
    """Return (watering_required, past_inches, forecast_inches, blocked_until, in_season, error)."""
    today = local_today(settings.location)

    if in_sewer_baseline_window(settings.sewer, today):
        sewer = settings.sewer
        logger.info(
            "sewer baseline window (%02d/%02d-%02d/%02d); "
            "watering blocked to protect annual sewer cap",
            sewer.start_month,
            sewer.start_day,
            sewer.end_month,
            sewer.end_day,
        )
        return False, None, None, state.blocked_until, False, None

    if not in_season(settings.season, today):
        return False, None, None, state.blocked_until, False, None

    blocked_until = state.blocked_until

    try:
        snapshot = fetch_weather(settings)
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

    weather_allow = allow_watering(snapshot, settings)
    blocked_until = update_blocked_until(
        today,
        past_ok_flag=past_ok(snapshot, settings),
        rain_delay_days=settings.watering.rain_delay_days,
        blocked_until=blocked_until,
    )
    required = watering_required(today, weather_allow, blocked_until)
    if snapshot.freeze_block:
        logger.info(
            "freeze skip active (tempmin below %.1f F today or tomorrow)",
            settings.watering.freeze_temp_f,
        )
    if blocked_until and today <= blocked_until and weather_allow:
        logger.info("rain delay active through %s; watering blocked", blocked_until)
    return required, snapshot.past_inches, snapshot.forecast_inches, blocked_until, True, None


def _timeline_url(lat: float, lon: float, start: date, end: date) -> str:
    return f"{VISUAL_CROSSING}/{lat},{lon}/{start}/{end}"


def _get_timeline(
    lat: float,
    lon: float,
    start: date,
    end: date,
    *,
    settings: Settings,
    api_key: str,
    timeout: int,
) -> dict:
    return _get(
        _timeline_url(lat, lon, start, end),
        params={**timeline_params(settings), "key": api_key},
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


def _parse_vc_datetime(raw: str, timezone: str) -> datetime:
    tz = ZoneInfo(timezone)
    text = str(raw).strip().replace(" ", "T")
    if len(text) == 10:
        text = f"{text}T00:00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _freeze_block(daily: list, settings: Settings, today: date) -> bool:
    if not settings.watering.freeze_skip:
        return False
    threshold = settings.watering.freeze_temp_f
    watch = {today.isoformat(), (today + timedelta(days=1)).isoformat()}
    for day in daily:
        raw = day.get("datetime")
        if not raw:
            continue
        day_s = str(raw)[:10]
        if day_s not in watch:
            continue
        tempmin = day.get("tempmin")
        if tempmin is not None and float(tempmin) < threshold:
            return True
    return False


def _sum_precip_hours(hours: list, start: datetime, end: datetime, timezone: str) -> float:
    if not hours:
        raise requests.RequestException("visual crossing returned no hourly rows")

    total = 0.0
    matched = 0
    for hour in hours:
        raw = hour.get("datetime")
        if not raw:
            raise requests.RequestException("visual crossing hour missing datetime")
        when = _parse_vc_datetime(str(raw), timezone)
        if start <= when < end:
            total += float(hour.get("precip") or 0)
            matched += 1
    if matched == 0:
        logger.warning(
            "visual_crossing returned no hourly rows between %s and %s",
            start,
            end,
        )
    return total


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
