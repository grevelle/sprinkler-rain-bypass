from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from conftest import evaluation, patch_local_today, weather_snapshot
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import ConfigError, State
from rain_bypass.status import (
    format_deficit_formula,
    format_duration,
    format_inches,
    format_safety_gate,
    format_status,
    format_timestamp,
    format_updated,
    format_would_decide_now,
    gather_status,
    print_status,
    relay_label,
    relay_mismatch_note,
)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (30 * 60, "30m"),
        (90 * 60, "1h 30m"),
        (25 * 3600, "1d 1h"),
    ],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


def test_format_inches() -> None:
    assert format_inches(None) == "n/a"
    assert format_inches(0.5) == "0.50 in"


def test_format_timestamp_never() -> None:
    assert format_timestamp(None, "UTC") == "never"


def test_format_timestamp_value() -> None:
    text = format_timestamp(1_700_000_000.0, "UTC")
    assert "2023" in text


def test_format_updated_includes_age() -> None:
    now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("UTC"))
    text = format_updated(1_700_000_000.0, "UTC", now=now)
    assert "ago" in text


def test_relay_label_unknown() -> None:
    assert "unknown" in relay_label(None)


def test_relay_label_allow() -> None:
    assert "ALLOW" in relay_label(True)


def test_relay_label_block() -> None:
    assert "BLOCK" in relay_label(False)


def test_format_deficit_formula() -> None:
    ev = evaluation(
        target_to_date=0.97,
        rain_mtd=0.26,
        forecast_inches=0.02,
        deficit=0.05,
    )
    text = format_deficit_formula(ev, 0.63, 0.3)
    assert "0.97 - 0.26 - 0.63 - 0.02 = 0.05 in" in text
    assert "need ≥ 0.30 to allow" in text


def test_format_updated_without_now() -> None:
    assert format_updated(1_700_000_000.0, "UTC") == format_timestamp(1_700_000_000.0, "UTC")


def test_format_safety_gate_storm_block() -> None:
    ev = evaluation(safety_ok=False, freeze_block=False, max_daily_inches=0.25)
    assert format_safety_gate(ev, safety_known=True) == "block (storm / heavy rain)"


def test_format_safety_gate_unknown() -> None:
    assert format_safety_gate(evaluation(), safety_known=False) == (
        "unknown (not saved — run without --cached)"
    )


@pytest.mark.parametrize(
    ("watering_required", "safety_known", "balance_ok", "expected_parts"),
    [
        (False, True, False, ("BLOCK watering (skip today's cycle)",)),
        (None, False, True, ("ALLOW by balance", "--cached")),
    ],
)
def test_format_would_decide_now(
    watering_required: bool | None,
    safety_known: bool,
    balance_ok: bool,
    expected_parts: tuple[str, ...],
) -> None:
    text = format_would_decide_now(
        watering_required, safety_known=safety_known, balance_ok=balance_ok
    )
    for part in expected_parts:
        assert part in text


def test_relay_mismatch_note() -> None:
    assert relay_mismatch_note(True, False) is not None
    assert relay_mismatch_note(True, True) is None


def test_gather_status_sewer_lockout(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 2, 1))
    snap = gather_status(settings, State(), fetch_live=False)
    assert snap.preview.sewer_lockout is True
    assert snap.preview.would_water is False


def test_gather_status_live_weather(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(rain_mtd=0.0, forecast=0.0),
    )
    snap = gather_status(settings, State(), fetch_live=True)
    assert snap.preview.live is not None
    assert snap.preview.evaluation is not None
    assert snap.preview.evaluation.balance_ok is True
    assert snap.preview.evaluation.safety_ok is True
    assert snap.preview.would_water is True
    assert snap.preview.evaluation.monthly_target == pytest.approx(5.0)


def test_gather_status_weather_error(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))

    def _fail(_settings):
        from rain_bypass.exceptions import WeatherError

        raise WeatherError("api down")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _fail)
    snap = gather_status(settings, State(), fetch_live=True)
    assert snap.preview.live is None
    assert snap.preview.live_error == "api down"
    assert snap.preview.would_water is None


def test_gather_status_cached_projects(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state = State(
        watering_required=True,
        rainfall_inches=10.0,
        forecast_inches=0.0,
        max_daily_inches=0.0,
        freeze_block=False,
    )
    snap = gather_status(settings, state, fetch_live=False)
    assert snap.preview.from_saved_weather is True
    assert snap.preview.would_water is False


def test_format_status_sections(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 3600.0)
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(rain_mtd=1.0, forecast=0.2, max_daily=0.1),
    )
    monkeypatch.setattr("rain_bypass.logic.irrigation_mtd", lambda _s, _t: 0.3)
    state = State(
        watering_required=True,
        last_weather_update=1_700_000_000.0,
        rainfall_inches=1.0,
        forecast_inches=0.2,
        max_daily_inches=0.1,
        freeze_block=False,
        last_error="previous failure",
    )
    text = format_status(gather_status(settings, state))
    assert "Sprinkler Rain Bypass" in text
    assert "53029" in text
    assert "previous failure" in text
    assert "Balance (July)" in text
    assert "Formula" in text
    assert "Would decide now" in text
    assert "ALLOW watering" in text
    assert "ago" in text


def test_format_status_cached_mode(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    state = State(
        watering_required=True,
        rainfall_inches=0.0,
        forecast_inches=0.0,
        max_daily_inches=0.0,
        freeze_block=False,
    )
    snap = gather_status(settings, state, fetch_live=False)
    text = format_status(snap)
    assert "Projected decision (saved weather, no API)" in text
    assert "Live evaluation" not in text
    assert "Would decide now" in text
    assert "ALLOW watering if the panel runs today" in text


def test_format_status_cached_shows_mismatch(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    state = State(
        watering_required=True,
        rainfall_inches=10.0,
        forecast_inches=0.0,
        max_daily_inches=0.0,
        freeze_block=False,
    )
    text = format_status(gather_status(settings, state, fetch_live=False))
    assert "Note: saved relay is ALLOW" in text
    assert "projects BLOCK now" in text


def test_format_status_cached_missing_safety_fields(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    state = State(
        watering_required=False,
        rainfall_inches=0.0,
        forecast_inches=0.0,
    )
    text = format_status(gather_status(settings, state, fetch_live=False))
    assert "Max day" in text
    assert "n/a (not saved)" in text
    assert "unknown (not saved)" in text
    assert "ALLOW by balance" in text


def test_format_status_unknown_verdict(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    snap = gather_status(settings, State(), fetch_live=False)
    text = format_status(snap)
    assert "no saved values" in text
    assert "unknown (need live weather or a completed cycle)" in text


def test_format_status_sewer_lockout(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 2, 1))
    fixed_now = datetime(2024, 2, 1, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    text = format_status(gather_status(settings, State(), fetch_live=False))
    assert "Sewer lockout" in text
    assert "ACTIVE" in text
    assert "BLOCK watering" in text


def test_format_status_live_without_weather_payload(settings, monkeypatch) -> None:
    from rain_bypass.models import Preview
    from rain_bypass.status import StatusSnapshot, format_status

    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    preview_result = Preview(
        irrigation_mtd=0.0,
        sewer_lockout=False,
        live=None,
        live_error=None,
        evaluation=None,
        cached_verdict=None,
    )
    snapshot = StatusSnapshot(
        settings=settings,
        state=State(),
        local_time=fixed_now,
        next_check_seconds=60.0,
        preview=preview_result,
        fetch_live=True,
    )
    text = format_status(snapshot)
    assert "Live evaluation" in text
    assert "Would decide now" in text


def test_format_status_live_weather_only(settings, monkeypatch) -> None:
    from rain_bypass.models import Preview
    from rain_bypass.status import StatusSnapshot, format_status

    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    snapshot = StatusSnapshot(
        settings=settings,
        state=State(),
        local_time=fixed_now,
        next_check_seconds=60.0,
        preview=Preview(
            irrigation_mtd=0.0,
            sewer_lockout=False,
            live=weather_snapshot(rain_mtd=0.5, forecast=0.1),
            live_error=None,
            evaluation=None,
            cached_verdict=None,
        ),
        fetch_live=True,
    )
    text = format_status(snapshot)
    assert "Rain MTD" in text
    assert "0.50 in" in text
    assert "Balance (" not in text


def test_format_status_live_evaluation_only(settings, monkeypatch) -> None:
    from rain_bypass.models import Preview
    from rain_bypass.status import StatusSnapshot, format_status

    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    snapshot = StatusSnapshot(
        settings=settings,
        state=State(),
        local_time=fixed_now,
        next_check_seconds=60.0,
        preview=Preview(
            irrigation_mtd=0.63,
            sewer_lockout=False,
            live=None,
            live_error=None,
            evaluation=evaluation(
                target_to_date=0.97,
                rain_mtd=0.26,
                forecast_inches=0.02,
                deficit=0.05,
            ),
            cached_verdict=None,
        ),
        fetch_live=True,
    )
    text = format_status(snapshot)
    assert "Balance (July)" in text
    assert "Formula" in text


def test_format_status_weather_error(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)

    def _fail(_settings):
        from rain_bypass.exceptions import WeatherError

        raise WeatherError("api down")

    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _fail)
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "error — api down" in text
    assert "unknown (need live weather or a completed cycle)" in text


def test_format_status_freeze_block(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(rain_mtd=0.0, freeze_block=True),
    )
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "Freeze block" in text
    assert "yes" in text
    assert "Safety gate" in text
    assert "block (freeze)" in text


def test_format_status_block_verdict(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    fixed_now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr("rain_bypass.status.local_now", lambda _s, now=None: fixed_now)
    monkeypatch.setattr("rain_bypass.status.seconds_until_next_check", lambda _s, now=None: 60.0)
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(rain_mtd=10.0, forecast=0.0),
    )
    text = format_status(gather_status(settings, State(), fetch_live=True))
    assert "BLOCK watering" in text


def test_print_status_cli(settings_path, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(rain_mtd=0.0),
    )
    result = CliRunner().invoke(app, ["status", "--config", str(settings_path)])
    assert result.exit_code == 0
    assert "Sprinkler Rain Bypass" in result.stdout
    assert "Would decide now" in result.stdout


def test_print_status_cli_cached(settings_path, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    result = CliRunner().invoke(app, ["status", "--config", str(settings_path), "--cached"])
    assert result.exit_code == 0
    assert "Projected decision" in result.stdout
    assert "Live evaluation" not in result.stdout


def test_print_status_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with (
        patch("rain_bypass.status.load_settings", side_effect=ConfigError("bad config")),
        pytest.raises(ConfigError, match="bad config"),
    ):
        print_status(missing)


def test_status_cli_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    result = CliRunner().invoke(app, ["status", "--config", str(missing)])
    assert result.exit_code == 1
    assert "Config not found" in result.stderr or "Config not found" in result.stdout
