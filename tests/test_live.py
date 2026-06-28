"""Live Visual Crossing integration tests — require VISUAL_CROSSING_API_KEY."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import Settings, State, local_today
from rain_bypass.logic import allow_watering, decide, watering_required
from rain_bypass.settings_io import load_example_settings, write_settings
from rain_bypass.weather import fetch_weather, sum_precip, timeline_request_params, timeline_url_for
from rain_bypass.windows import forecast_window, past_window, timeline_window

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("VISUAL_CROSSING_API_KEY"),
        reason="set VISUAL_CROSSING_API_KEY to run live Visual Crossing tests",
    ),
]

LIVE_SITES = (
    ("hartland", 43.106, -88.351, "America/Chicago"),
    ("chicago", 41.8781, -87.6298, "America/Chicago"),
    ("phoenix", 33.4484, -112.0740, "America/Phoenix"),
)

LIVE_SETTINGS_OVERRIDES = {
    "watering": {
        "inches_required": 0.6,
        "past_days": 7,
        "near_term_hours": 24,
        "updates_per_day": 1,
    },
    "gpio": {"mock": True},
    "runtime": {"log_level": "WARNING"},
}


class LiveCase(NamedTuple):
    config_path: Path
    settings: Settings


@pytest.fixture(params=LIVE_SITES, ids=[site[0] for site in LIVE_SITES])
def live_case(tmp_path: Path, request: pytest.FixtureRequest) -> LiveCase:
    name, lat, lon, tz = request.param
    state_path = tmp_path / f"{name}-state.json"
    config_path = tmp_path / f"{name}-settings.toml"
    settings = load_example_settings(
        location={"latitude": lat, "longitude": lon, "timezone": tz},
        weather={"api_key": os.environ["VISUAL_CROSSING_API_KEY"]},
        runtime={"state_path": state_path.as_posix(), **LIVE_SETTINGS_OVERRIDES["runtime"]},
        watering=LIVE_SETTINGS_OVERRIDES["watering"],
        gpio=LIVE_SETTINGS_OVERRIDES["gpio"],
    )
    write_settings(config_path, settings)
    return LiveCase(config_path=config_path, settings=settings)


def _assert_days_contract(days: list[object]) -> None:
    assert isinstance(days, list)
    assert days, "expected at least one day from Visual Crossing"
    for day in days:
        assert isinstance(day, dict)
        assert "datetime" in day
        assert "precip" in day
        precip = day["precip"]
        assert precip is None or isinstance(precip, int | float)
        if precip is not None:
            assert precip >= 0.0


def test_live_timeline_api_contract(live_case: LiveCase) -> None:
    settings = live_case.settings
    api_start, api_end = timeline_window(settings)
    response = httpx.get(
        timeline_url_for(settings, api_start, api_end),
        params=timeline_request_params(settings),
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    days = payload.get("days")
    assert isinstance(days, list)
    _assert_days_contract(days)
    day_dates = {str(day["datetime"])[:10] for day in days}
    expected = {
        (api_start + timedelta(days=offset)).isoformat()
        for offset in range((api_end - api_start).days + 1)
    }
    assert day_dates == expected


def test_live_fetch_weather_returns_inches(live_case: LiveCase) -> None:
    settings = live_case.settings
    api_start, api_end = timeline_window(settings)
    past_start, past_end = past_window(settings)
    forecast = forecast_window(settings)
    assert forecast is not None
    forecast_start, forecast_end = forecast
    response = httpx.get(
        timeline_url_for(settings, api_start, api_end),
        params=timeline_request_params(settings),
        timeout=30,
    ).json()
    expected_past = sum_precip(response["days"], past_start, past_end)
    expected_forecast = sum_precip(response["days"], forecast_start, forecast_end)
    snapshot = fetch_weather(settings)
    assert snapshot.past_inches == pytest.approx(expected_past)
    assert snapshot.forecast_inches == pytest.approx(expected_forecast)
    assert snapshot.past_inches >= 0.0
    assert snapshot.forecast_inches >= 0.0
    assert snapshot.past_inches <= 20.0
    assert snapshot.forecast_inches <= 20.0
    assert snapshot.near_term_inches >= 0.0
    assert isinstance(snapshot.freeze_block, bool)


def test_live_decide_matches_threshold(live_case: LiveCase) -> None:
    settings = live_case.settings
    snapshot = fetch_weather(settings)
    decision = decide(settings, State())
    assert decision.error is None
    assert decision.past_inches == pytest.approx(snapshot.past_inches)
    assert decision.forecast_inches == pytest.approx(snapshot.forecast_inches)
    assert decision.watering_required is watering_required(
        local_today(settings.location),
        allow_watering(snapshot, settings),
        decision.blocked_until,
    )


def test_live_main_once_persists_state(live_case: LiveCase) -> None:
    settings = live_case.settings
    result = CliRunner().invoke(app, ["--config", str(live_case.config_path), "--once"])
    assert result.exit_code == 0
    saved = State.load(settings.runtime.state_path)
    assert saved.last_weather_update is not None
    assert saved.rainfall_inches is not None
    assert saved.forecast_inches is not None
    assert saved.last_error is None
    snapshot = fetch_weather(settings)
    assert saved.watering_required is watering_required(
        local_today(settings.location),
        allow_watering(snapshot, settings),
        saved.blocked_until,
    )
