from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from rain_bypass.config import load_settings
from rain_bypass.logic import evaluate_watering, is_in_watering_season
from rain_bypass.models import FailMode, RuntimeState, SeasonSettings
from rain_bypass.weather.base import mm_to_inches, precipitation_window_end


def test_precipitation_window_end() -> None:
    start, end = precipitation_window_end(date(2024, 6, 10), 7)
    assert start == date(2024, 6, 4)
    assert end == date(2024, 6, 10)


def test_mm_to_inches() -> None:
    assert mm_to_inches(25.4) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("today", "expected"),
    [
        (date(2024, 6, 1), True),
        (date(2024, 2, 1), False),
        (date(2024, 3, 19), True),
        (date(2024, 9, 12), True),
    ],
)
def test_is_in_watering_season(today: date, expected: bool) -> None:
    season = SeasonSettings(start_month=3, start_day=19, end_month=9, end_day=12)
    assert is_in_watering_season(season, today) is expected


def test_out_of_season_skips_weather(example_config: Path) -> None:
    settings = load_settings(example_config)
    out_of_season = SeasonSettings(start_month=12, start_day=1, end_month=12, end_day=31)
    settings = replace(settings, season=out_of_season)
    decision = evaluate_watering(settings, RuntimeState())
    assert decision.watering_required is False
    assert decision.in_season is False
    assert decision.source == "season"


def test_fail_mode_keep_last_state(example_config: Path) -> None:
    settings = load_settings(example_config)
    runtime = replace(settings.runtime, fail_mode=FailMode.KEEP_LAST_STATE)
    settings = replace(settings, runtime=runtime)
    state = RuntimeState(watering_required=True, rainfall_inches=0.1)

    class BrokenClient:
        def precipitation_inches(self, _settings, _window_days) -> float:
            raise RuntimeError("offline")

    from rain_bypass import logic as logic_module

    original = logic_module.build_weather_client
    logic_module.build_weather_client = lambda _settings: BrokenClient()
    try:
        decision = evaluate_watering(settings, state)
    finally:
        logic_module.build_weather_client = original

    assert decision.watering_required is True
    assert decision.source == "last_state"
