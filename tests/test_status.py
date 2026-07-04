from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import ConfigError, State
from rain_bypass.models import WeatherSnapshot
from rain_bypass.status import (
    format_duration,
    format_status,
    format_timestamp,
    gather_status,
    print_status,
    relay_label,
)


def _snapshot(
    rain_mtd: float = 0.0,
    forecast: float = 0.0,
    max_daily: float = 0.0,
    freeze_block: bool = False,
) -> WeatherSnapshot:
    return WeatherSnapshot(rain_mtd, forecast, max_daily, freeze_block)


def test_format_duration_minutes() -> None:
    assert format_duration(30 * 60) == "30m"


def test_format_duration_hours() -> None:
    assert format_duration(90 * 60) == "1h 30m"


def test_format_duration_days() -> None:
    assert format_duration(25 * 3600) == "1d 1h"


def test_format_timestamp_never() -> None:
    assert format_timestamp(None, "UTC") == "never"


def test_format_timestamp_value() -> None:
    text = format_timestamp(1_700_000_000.0, "UTC")
    assert "2023" in text


def test_relay_label_unknown() -> None:
    assert "unknown" in relay_label(None)


def test_relay_label_allow() -> None:
    assert "ALLOW" in relay_label(True)


def test_relay_label_block() -> None:
    assert "BLOCK" in relay_label(False)


def test_gather_status_sewer_lockout(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 2, 1))
    snap = gather_status(settings, State(), fetch_live=False)
    assert snap.sewer_lockout is True
    assert snap.would_water is False


def test_gather_status_live_weather(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.status.fetch_weather",
        lambda _s: _snapshot(rain_mtd=0.0, forecast=0.0),
    )
    snap = gather_status(settings, State(), fetch_live=True)
    assert snap.live is not None
    assert snap.balance_ok is True
    assert snap.safety_ok is True
    assert snap.would_water is True
    assert snap.monthly_target == pytest.approx(5.0)


def test_gather_status_weather_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))

    def _fail(_settings):
        from rain_bypass.exceptions import WeatherError

        raise WeatherError("api down")

    monkeypatch.setattr("rain_bypass.status.fetch_weather", _fail)
    snap = gather_status(settings, State(), fetch_live=True)
    assert snap.live is None
    assert snap.live_error == "api down"
    assert snap.would_water is None


def test_gather_status_cached_uses_saved_decision(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    state = State(watering_required=False)
    snap = gather_status(settings, state, fetch_live=False)
    assert snap.would_water is False
    assert snap.live is None


def test_format_status_sections(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 3600.0)
    monkeypatch.setattr(
        "rain_bypass.status.fetch_weather",
        lambda _s: _snapshot(rain_mtd=1.0, forecast=0.2, max_daily=0.1),
    )
    state = State(
        watering_required=True,
        last_weather_update=1_700_000_000.0,
        rainfall_inches=1.0,
        forecast_inches=0.2,
        irrigation_inches_mtd=0.3,
        last_error="previous failure",
    )
    text = format_status(gather_status(settings, state))
    assert "Sprinkler Rain Bypass" in text
    assert "53029" in text
    assert "previous failure" in text
    assert "Balance (July)" in text
    assert "ALLOW watering" in text


def test_format_status_cached_mode(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    snap = gather_status(settings, State(watering_required=True), fetch_live=False)
    text = format_status(snap)
    assert "skipped (--cached)" in text
    assert "ALLOW watering if the panel runs today" in text


def test_format_status_unknown_verdict(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    snap = gather_status(settings, State(), fetch_live=False)
    text = format_status(snap)
    assert "unknown (need live weather or a completed cycle)" in text


def test_format_status_sewer_lockout(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 2, 1))
    fixed_now = datetime(2024, 2, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    text = format_status(gather_status(settings, State(), fetch_live=False))
    assert "Sewer lockout" in text
    assert "ACTIVE" in text
    assert "BLOCK watering" in text


def test_format_status_weather_error(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)

    def _fail(_settings):
        from rain_bypass.exceptions import WeatherError

        raise WeatherError("api down")

    monkeypatch.setattr("rain_bypass.status.fetch_weather", _fail)
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "error — api down" in text
    assert "unknown (need live weather or a completed cycle)" in text


def test_format_status_freeze_block(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    monkeypatch.setattr(
        "rain_bypass.status.fetch_weather",
        lambda _s: _snapshot(rain_mtd=0.0, freeze_block=True),
    )
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "Freeze block" in text
    assert "yes" in text
    assert "Safety gate" in text
    assert "block" in text


def test_format_status_block_verdict(settings, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    monkeypatch.setattr(
        "rain_bypass.status.fetch_weather",
        lambda _s: _snapshot(rain_mtd=10.0, forecast=0.0),
    )
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "BLOCK watering" in text


def test_print_status_cli(settings_path, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.status.fetch_weather",
        lambda _s: _snapshot(rain_mtd=0.0),
    )
    result = CliRunner().invoke(app, ["status", "--config", str(settings_path)])
    assert result.exit_code == 0
    assert "Sprinkler Rain Bypass" in result.stdout


def test_print_status_cli_cached(settings_path, monkeypatch) -> None:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: date(2024, 7, 15))
    result = CliRunner().invoke(app, ["status", "--config", str(settings_path), "--cached"])
    assert result.exit_code == 0
    assert "skipped (--cached)" in result.stdout


def test_print_status_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with patch("rain_bypass.status.load_settings", side_effect=ConfigError("bad config")):
        with pytest.raises(ConfigError, match="bad config"):
            print_status(missing)


def test_status_cli_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    result = CliRunner().invoke(app, ["status", "--config", str(missing)])
    assert result.exit_code == 1
    assert "Config not found" in result.stderr or "Config not found" in result.stdout
