from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rain_bypass import config
from rain_bypass.config import Location, Settings


def past_window(settings: Settings) -> tuple[date, date]:
    today = config.local_today(settings.location)
    past_days = settings.watering.past_days
    return today - timedelta(days=past_days - 1), today


def forecast_window(settings: Settings) -> tuple[date, date] | None:
    forecast_days = settings.watering.forecast_days
    if forecast_days <= 0:
        return None
    today = config.local_today(settings.location)
    return today + timedelta(days=1), today + timedelta(days=forecast_days)


def timeline_window(settings: Settings) -> tuple[date, date]:
    past_start, past_end = past_window(settings)
    forecast = forecast_window(settings)
    if forecast is None:
        return past_start, past_end
    return past_start, forecast[1]


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


def timeline_location_path(location: Location) -> str:
    return f"{location.latitude},{location.longitude}"
