from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import pytest
from conftest import evaluation, patch_local_today, weather_snapshot
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import State
from rain_bypass.history import (
    WateringRecord,
    _write_records,
    append_record,
    append_watering_history,
    build_record,
    history_path,
    irrigation_mtd,
    load_records,
    migrate_legacy_irrigation,
    print_history,
)
from rain_bypass.logic import decide
from rain_bypass.models import Decision


def test_history_path_default(settings):
    assert history_path(settings) == settings.runtime.state_path.with_name("watering_history.jsonl")


def test_history_path_override(settings, tmp_path: Path):
    custom = tmp_path / "logs" / "water.jsonl"
    runtime = settings.runtime.model_copy(update={"history_path": custom})
    updated = settings.model_copy(update={"runtime": runtime})
    assert history_path(updated) == custom


def test_build_record_credits_allow(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    decision = Decision(
        watering_required=True,
        evaluation=evaluation(),
        error=None,
    )
    record = build_record(settings, State(), decision, checked_at=1_700_000_000.0)
    assert record.local_date == "2024-07-15"
    assert record.allowed is True
    assert record.inches_credited == pytest.approx(0.3)
    assert record.irrigation_mtd == pytest.approx(0.3)
    assert record.sewer_lockout is False


def test_build_record_sewer_lockout(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 2, 1))
    decision = Decision(
        watering_required=False,
        evaluation=None,
        error=None,
    )
    record = build_record(settings, State(), decision)
    assert record.sewer_lockout is True
    assert record.inches_credited == pytest.approx(0.0)


def test_build_record_weather_error(settings, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    decision = Decision(
        watering_required=False,
        evaluation=None,
        error="api down",
    )
    state = State(rainfall_inches=0.42, forecast_inches=0.1)
    record = build_record(settings, state, decision)
    assert record.weather_error == "api down"
    assert record.rain_mtd == pytest.approx(0.42)
    assert record.forecast_inches == pytest.approx(0.1)
    assert record.inches_credited == pytest.approx(0.0)


def test_append_and_load_records(tmp_path: Path):
    path = tmp_path / "watering_history.jsonl"
    first = WateringRecord(
        checked_at=1.0,
        local_date="2024-07-01",
        allowed=True,
        inches_credited=0.3,
        irrigation_mtd=0.3,
    )
    second = WateringRecord(
        checked_at=2.0,
        local_date="2024-07-02",
        allowed=False,
        inches_credited=0.0,
        irrigation_mtd=0.3,
    )
    append_record(path, first, now=10.0)
    append_record(path, second, now=10.0)
    loaded = load_records(path)
    assert len(loaded) == 2
    assert loaded[0].local_date == "2024-07-01"
    assert loaded[1].allowed is False


def test_load_records_empty_and_limit(tmp_path: Path):
    path = tmp_path / "missing.jsonl"
    assert load_records(path) == []
    assert load_records(path, limit=0) == []

    path.write_text(
        "\n".join(
            json.dumps(
                WateringRecord(
                    checked_at=float(index),
                    local_date=f"2024-07-{index:02d}",
                    allowed=False,
                    inches_credited=0.0,
                    irrigation_mtd=0.0,
                ).model_dump()
            )
            for index in range(1, 6)
        )
        + "\n",
        encoding="utf-8",
    )
    assert len(load_records(path, limit=2)) == 2


def test_load_records_skips_blank_lines(tmp_path: Path):
    path = tmp_path / "watering_history.jsonl"
    path.write_text(
        "\n"
        + json.dumps(
            WateringRecord(
                checked_at=1.0,
                local_date="2024-07-01",
                allowed=True,
                inches_credited=0.3,
                irrigation_mtd=0.3,
            ).model_dump()
        )
        + "\n\n",
        encoding="utf-8",
    )
    assert len(load_records(path)) == 1


def test_append_record_prunes_entries_older_than_one_year(tmp_path: Path):
    path = tmp_path / "watering_history.jsonl"
    now = 2_000_000_000.0
    cutoff = now - 365 * 86_400
    stale = WateringRecord(
        checked_at=cutoff - 1.0,
        local_date="2023-07-01",
        allowed=False,
        inches_credited=0.0,
        irrigation_mtd=0.0,
    )
    recent = WateringRecord(
        checked_at=cutoff,
        local_date="2024-07-01",
        allowed=True,
        inches_credited=0.3,
        irrigation_mtd=0.3,
    )
    _write_records(path, [stale, recent])
    fresh = WateringRecord(
        checked_at=now,
        local_date="2026-07-10",
        allowed=True,
        inches_credited=0.3,
        irrigation_mtd=0.6,
    )
    append_record(path, fresh, now=now)
    loaded = load_records(path, limit=10)
    assert len(loaded) == 2
    assert loaded[0].local_date == "2024-07-01"
    assert loaded[1].local_date == "2026-07-10"


def test_retention_cutoff_uses_current_time():
    from rain_bypass.history import HISTORY_RETENTION, _retention_cutoff

    before = time.time()
    cutoff = _retention_cutoff()
    after = time.time()
    assert before - HISTORY_RETENTION.total_seconds() <= cutoff
    assert cutoff <= after - HISTORY_RETENTION.total_seconds()


def test_irrigation_mtd_sums_current_month(settings, tmp_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    path = history_path(settings)
    append_record(
        path,
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=True,
            inches_credited=0.3,
            irrigation_mtd=0.3,
        ),
        now=10.0,
    )
    append_record(
        path,
        WateringRecord(
            checked_at=2.0,
            local_date="2024-07-10",
            allowed=True,
            inches_credited=0.3,
            irrigation_mtd=0.6,
        ),
        now=10.0,
    )
    append_record(
        path,
        WateringRecord(
            checked_at=3.0,
            local_date="2024-06-30",
            allowed=True,
            inches_credited=0.9,
            irrigation_mtd=0.9,
        ),
        now=10.0,
    )
    assert irrigation_mtd(settings, date(2024, 7, 15)) == pytest.approx(0.6)


def test_migrate_legacy_irrigation(settings, tmp_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    state_path.write_text(
        '{"irrigation_inches_mtd": 0.66, "balance_month": 7, "watering_required": true}\n',
        encoding="utf-8",
    )
    migrate_legacy_irrigation(settings)
    assert irrigation_mtd(settings, date(2024, 7, 15)) == pytest.approx(0.66)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert "irrigation_inches_mtd" not in raw
    assert "balance_month" not in raw


def test_migrate_legacy_irrigation_skips_stale_month(settings, tmp_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    state_path.write_text(
        '{"irrigation_inches_mtd": 0.66, "balance_month": 6, "watering_required": true}\n',
        encoding="utf-8",
    )
    migrate_legacy_irrigation(settings)
    assert irrigation_mtd(settings, date(2024, 7, 15)) == pytest.approx(0.0)


def test_migrate_legacy_irrigation_strips_when_history_already_current(
    settings, tmp_path, monkeypatch
):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    append_record(
        history_path(settings),
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=True,
            inches_credited=0.66,
            irrigation_mtd=0.66,
        ),
        now=10.0,
    )
    state_path.write_text(
        '{"irrigation_inches_mtd": 0.66, "balance_month": 7, "watering_required": true}\n',
        encoding="utf-8",
    )
    migrate_legacy_irrigation(settings)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert "irrigation_inches_mtd" not in raw


def test_migrate_legacy_irrigation_ignores_non_object_state(settings, tmp_path):
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    state_path.write_text("[1, 2, 3]\n", encoding="utf-8")
    migrate_legacy_irrigation(settings)


def test_migrate_legacy_irrigation_strips_when_history_exceeds_legacy(
    settings, tmp_path, monkeypatch
):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    append_record(
        history_path(settings),
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=True,
            inches_credited=0.9,
            irrigation_mtd=0.9,
        ),
        now=10.0,
    )
    state_path.write_text(
        '{"irrigation_inches_mtd": 0.66, "balance_month": 7, "watering_required": true}\n',
        encoding="utf-8",
    )
    migrate_legacy_irrigation(settings)
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    assert "irrigation_inches_mtd" not in raw
    assert irrigation_mtd(settings, date(2024, 7, 15)) == pytest.approx(0.9)


def test_strip_legacy_irrigation_fields_noop(settings, tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text('{"watering_required": true}\n', encoding="utf-8")
    from rain_bypass.history import _strip_legacy_irrigation_fields

    _strip_legacy_irrigation_fields(state_path, {"watering_required": True})
    assert state_path.read_text(encoding="utf-8") == '{"watering_required": true}\n'


def test_append_watering_history_from_tick(settings, tmp_path, monkeypatch):
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather",
        lambda _s: weather_snapshot(0.0, 0.0, 0.0),
    )
    record = append_watering_history(settings, State(), decide(settings, State()))
    path = history_path(settings)
    assert path.is_file()
    loaded = load_records(path)
    assert len(loaded) == 1
    assert loaded[0].allowed == record.allowed


def test_print_history_empty(settings_path, capsys):
    print_history(settings_path)
    captured = capsys.readouterr().out
    assert "No history yet" in captured


def test_print_history_shows_records(settings, tmp_path, capsys):
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={"runtime": settings.runtime.model_copy(update={"state_path": state_path})}
    )
    path = history_path(settings)
    append_record(
        path,
        WateringRecord(
            checked_at=1_718_000_000.0,
            local_date="2024-06-10",
            allowed=True,
            inches_credited=0.3,
            irrigation_mtd=0.3,
            rain_mtd=0.5,
            forecast_inches=0.1,
            deficit=0.2,
        ),
    )
    settings_path = tmp_path / "settings.toml"
    from rain_bypass.config import write_settings

    write_settings(settings_path, settings)
    print_history(settings_path)
    captured = capsys.readouterr().out
    assert "ALLOW" in captured
    assert "irr_mtd=0.30" in captured
    assert "rain=0.50" in captured


def test_cli_history_command(settings_path, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.history.load_records",
        lambda _path, limit=30: [
            WateringRecord(
                checked_at=1.0,
                local_date="2024-07-01",
                allowed=False,
                inches_credited=0.0,
                irrigation_mtd=0.0,
            )
        ],
    )
    result = CliRunner().invoke(app, ["history", "-c", str(settings_path)])
    assert result.exit_code == 0
    assert "watering history" in result.stdout


def test_cli_history_config_error(tmp_path):
    missing = tmp_path / "missing.toml"
    result = CliRunner().invoke(app, ["history", "-c", str(missing)])
    assert result.exit_code == 1


def test_format_allowed_branches():
    from rain_bypass.history import _format_allowed

    assert "ALLOW" in _format_allowed(
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=True,
            inches_credited=0.3,
            irrigation_mtd=0.3,
        )
    )
    assert "sewer" in _format_allowed(
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=False,
            inches_credited=0.0,
            irrigation_mtd=0.0,
            sewer_lockout=True,
        )
    )
    assert "weather" in _format_allowed(
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=False,
            inches_credited=0.0,
            irrigation_mtd=0.0,
            weather_error="x",
        )
    )
    assert "BLOCK" in _format_allowed(
        WateringRecord(
            checked_at=1.0,
            local_date="2024-07-01",
            allowed=False,
            inches_credited=0.0,
            irrigation_mtd=0.0,
        )
    )
