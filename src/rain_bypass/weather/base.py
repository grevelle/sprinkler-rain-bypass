from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from rain_bypass.models import Settings


class WeatherClient(ABC):
    @abstractmethod
    def precipitation_inches(self, settings: Settings, window_days: int) -> float:
        """Return total precipitation in inches over the configured window."""


class WeatherError(RuntimeError):
    """Raised when a weather provider request fails."""


def precipitation_window_end(today: date, past_days: int) -> tuple[date, date]:
    if past_days < 1:
        raise ValueError("past_days must be at least 1")
    start = today - timedelta(days=past_days - 1)
    return start, today


def local_today_in_timezone(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


def mm_to_inches(mm: float) -> float:
    return mm / 25.4
