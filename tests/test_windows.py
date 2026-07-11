from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import patch_local_today

from rain_bypass.windows import (
    event_lookback_window,
    forecast_window,
    seconds_until_next_check,
    timeline_window,
)


def test_event_lookback_window(settings, monkeypatch) -> None:
    fixed = patch_local_today(monkeypatch, date(2024, 6, 10))
    start, end = event_lookback_window(settings)
    assert end == fixed
    assert start == fixed - timedelta(days=settings.watering.event_lookback_days - 1)


def test_forecast_and_timeline_windows(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 6, 10))
    assert forecast_window(settings) == (date(2024, 6, 11), date(2024, 6, 12))
    assert timeline_window(settings) == (date(2024, 6, 1), date(2024, 6, 12))

    no_forecast = settings.model_copy(
        update={"balance": settings.balance.model_copy(update={"forecast_days": 0})}
    )
    assert forecast_window(no_forecast) is None
    assert timeline_window(no_forecast) == (date(2024, 6, 1), date(2024, 6, 10))


@pytest.mark.parametrize(
    ("now", "check_hour", "check_minute", "expected_seconds"),
    [
        (datetime(2024, 6, 9, 23, 0, tzinfo=ZoneInfo("America/Chicago")), 0, 0, 3600),
        (datetime(2024, 6, 10, 3, 0, tzinfo=ZoneInfo("America/Chicago")), 0, 0, 21 * 3600),
        (datetime(2024, 6, 10, 5, 0, tzinfo=ZoneInfo("America/Chicago")), 0, 0, 19 * 3600),
        (datetime(2024, 6, 10, 3, 0), 0, 0, 21 * 3600),
        (datetime(2024, 6, 10, 3, 0, tzinfo=ZoneInfo("America/Chicago")), 4, 30, 90 * 60),
    ],
)
def test_seconds_until_next_check(
    settings,
    now: datetime,
    check_hour: int,
    check_minute: int,
    expected_seconds: float,
) -> None:
    cfg = settings
    if check_hour != 0 or check_minute != 0:
        cfg = settings.model_copy(
            update={
                "watering": settings.watering.model_copy(
                    update={"check_hour": check_hour, "check_minute": check_minute}
                )
            }
        )
    assert seconds_until_next_check(cfg, now=now) == pytest.approx(expected_seconds)
