"""Live Visual Crossing integration tests — require VISUAL_CROSSING_API_KEY."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

import pytest
import requests

from rain_bypass.app import TIMELINE_QUERY, VISUAL_CROSSING, _sum_precip, decide, fetch_precip, main
from rain_bypass.config import Settings, State, load_settings, local_today

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("VISUAL_CROSSING_API_KEY"),
        reason="set VISUAL_CROSSING_API_KEY to run live Visual Crossing tests",
    ),
]

LIVE_SITES = (
    ("nyc", 40.7128, -74.0060, "America/New_York"),
    ("chicago", 41.8781, -87.6298, "America/Chicago"),
    ("phoenix", 33.4484, -112.0740, "America/Phoenix"),
)

LIVE_SETTINGS = """
[location]
latitude = {lat}
longitude = {lon}
timezone = "{tz}"

[watering]
inches_required = 0.6
past_days = 7
updates_per_day = 1

[season]
start_month = 1
start_day = 1
end_month = 12
end_day = 31

[weather]
api_key = "{api_key}"

[gpio]
relay = 25
watering_enabled_led = 4
watering_disabled_led = 27
mock = true

[runtime]
state_path = "{state_path}"
fail_mode = "disable_watering"
log_level = "WARNING"
weather_timeout_seconds = 30
"""


class LiveCase(NamedTuple):
    config_path: Path
    settings: Settings


@pytest.fixture(params=LIVE_SITES, ids=[site[0] for site in LIVE_SITES])
def live_case(tmp_path: Path, request) -> LiveCase:
    name, lat, lon, tz = request.param
    state_path = tmp_path / f"{name}-state.json"
    config_path = tmp_path / f"{name}-settings.toml"
    config_path.write_text(
        LIVE_SETTINGS.format(
            lat=lat,
            lon=lon,
            tz=tz,
            api_key=os.environ["VISUAL_CROSSING_API_KEY"],
            state_path=state_path.as_posix(),
        ),
        encoding="utf-8",
    )
    return LiveCase(config_path=config_path, settings=load_settings(config_path))


def _window(settings: Settings) -> tuple[date, date]:
    today = local_today(settings.location)
    start = today - timedelta(days=settings.watering.past_days - 1)
    return start, today


def _timeline_url(settings: Settings, start: date, end: date) -> str:
    loc = settings.location
    return f"{VISUAL_CROSSING}/{loc.latitude},{loc.longitude}/{start}/{end}"


def _timeline_params(settings: Settings) -> dict:
    return {
        "key": settings.weather.api_key,
        **TIMELINE_QUERY,
    }


def _assert_days_contract(days: list) -> None:
    assert isinstance(days, list)
    assert days, "expected at least one day from Visual Crossing"
    for day in days:
        assert isinstance(day, dict)
        assert "datetime" in day
        assert "precip" in day
        precip = day["precip"]
        assert precip is None or isinstance(precip, (int, float))
        if precip is not None:
            assert precip >= 0.0


def test_live_timeline_api_contract(live_case: LiveCase):
    settings = live_case.settings
    start, end = _window(settings)
    response = requests.get(
        _timeline_url(settings, start, end),
        params=_timeline_params(settings),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    days = payload.get("days")
    _assert_days_contract(days)
    day_dates = {str(day["datetime"])[:10] for day in days}
    expected = {
        (start + timedelta(days=offset)).isoformat()
        for offset in range(settings.watering.past_days)
    }
    assert day_dates == expected


def test_live_fetch_precip_returns_inches(live_case: LiveCase):
    settings = live_case.settings
    start, end = _window(settings)
    response = requests.get(
        _timeline_url(settings, start, end),
        params=_timeline_params(settings),
        timeout=30,
    ).json()
    expected = _sum_precip(response["days"], start, end)
    inches = fetch_precip(settings)
    assert inches == pytest.approx(expected)
    assert inches >= 0.0
    assert inches <= 20.0


def test_live_decide_matches_threshold(live_case: LiveCase):
    settings = live_case.settings
    rainfall_inches = fetch_precip(settings)
    required, rainfall, in_season_flag, error = decide(settings, State())
    assert error is None
    assert in_season_flag is True
    assert rainfall == pytest.approx(rainfall_inches)
    assert required is (rainfall <= settings.watering.inches_required)


def test_live_main_once_persists_state(live_case: LiveCase):
    settings = live_case.settings
    assert main(["--config", str(live_case.config_path), "--once"]) == 0
    saved = State.load(settings.runtime.state_path)
    assert saved.last_weather_update is not None
    assert saved.rainfall_inches is not None
    assert saved.last_error is None
    assert saved.watering_required is (
        saved.rainfall_inches <= settings.watering.inches_required
    )
