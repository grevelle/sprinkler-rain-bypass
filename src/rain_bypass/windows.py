from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rain_bypass import config
from rain_bypass.config import Location, Settings


def month_start(today: date) -> date:
    return today.replace(day=1)


def event_lookback_window(settings: Settings) -> tuple[date, date]:
    today = config.local_today(settings.location)
    lookback_days = settings.watering.event_lookback_days
    return today - timedelta(days=lookback_days - 1), today


def forecast_window(settings: Settings) -> tuple[date, date] | None:
    forecast_days = settings.balance.forecast_days
    if forecast_days <= 0:
        return None
    today = config.local_today(settings.location)
    return today + timedelta(days=1), today + timedelta(days=forecast_days)


def timeline_window(settings: Settings) -> tuple[date, date]:
    today = config.local_today(settings.location)
    lookback_start, lookback_end = event_lookback_window(settings)
    api_start = min(lookback_start, month_start(today))
    forecast = forecast_window(settings)
    if forecast is None:
        return api_start, max(lookback_end, today)
    return api_start, forecast[1]


def local_now(settings: Settings, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(settings.location.timezone)
    current = now or datetime.now(tz)
    if current.tzinfo is None:
        return current.replace(tzinfo=tz)
    return current.astimezone(tz)


def seconds_until_next_check(settings: Settings, *, now: datetime | None = None) -> float:
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
    return location.zip_code
