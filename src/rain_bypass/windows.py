from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rain_bypass import config
from rain_bypass.balance import month_start
from rain_bypass.config import Settings, State


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


def todays_check_at(settings: Settings, *, now: datetime | None = None) -> datetime:
    current = local_now(settings, now)
    watering = settings.watering
    return current.replace(
        hour=watering.check_hour,
        minute=watering.check_minute,
        second=0,
        microsecond=0,
    )


def daily_check_pending(
    settings: Settings,
    state: State,
    *,
    now: datetime | None = None,
) -> bool:
    """True when today's scheduled check time has passed and no check ran since then."""
    current = local_now(settings, now)
    scheduled = todays_check_at(settings, now=current)
    if current < scheduled:
        return False
    if state.last_weather_update is None:
        return True
    last = datetime.fromtimestamp(state.last_weather_update, tz=current.tzinfo)
    return last < scheduled


def missed_check_message(
    settings: Settings,
    state: State,
    *,
    now: datetime | None = None,
) -> str | None:
    if not daily_check_pending(settings, state, now=now):
        return None
    watering = settings.watering
    return (
        f"No check today since {watering.check_hour:02d}:{watering.check_minute:02d} - "
        "relay may be stale"
    )
