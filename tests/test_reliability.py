from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import mock_gather_status, status_snapshot
from conftest import preview as make_preview

from rain_bypass.config import State, load_example_settings
from rain_bypass.dashboard_html import DashboardView, render_dashboard_html
from rain_bypass.history import WateringRecord, _load_all_records, append_record, load_records
from rain_bypass.persistence import atomic_write_text
from rain_bypass.status import StatusSnapshot, format_status, gather_status
from rain_bypass.web import LIVE_MIN_INTERVAL_SECONDS, build_dashboard_view, reset_live_fetch_gate
from rain_bypass.windows import daily_check_pending, missed_check_message, todays_check_at


def test_atomic_write_text_replaces_file(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    atomic_write_text(path, "one\n")
    atomic_write_text(path, "two\n")
    assert path.read_text(encoding="utf-8") == "two\n"


def test_state_load_repairs_truncated_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"watering_required": true', encoding="utf-8")
    loaded = State.load(path)
    assert loaded == State()
    assert list(tmp_path.glob("state.json.corrupt-*"))


def test_append_record_replaces_same_local_date(tmp_path: Path) -> None:
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
        local_date="2024-07-01",
        allowed=False,
        inches_credited=0.0,
        irrigation_mtd=0.0,
    )
    append_record(path, first, now=10.0)
    append_record(path, second, now=10.0)
    loaded = load_records(path)
    assert len(loaded) == 1
    assert loaded[0].allowed is False
    assert loaded[0].checked_at == pytest.approx(2.0)


def test_history_load_rewrites_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / "watering_history.jsonl"
    good = WateringRecord(
        checked_at=1.0,
        local_date="2024-07-01",
        allowed=True,
        inches_credited=0.3,
        irrigation_mtd=0.3,
    )
    path.write_text(good.model_dump_json() + "\nbad-line\n", encoding="utf-8")
    loaded = _load_all_records(path)
    assert len(loaded) == 1
    assert "bad-line" not in path.read_text(encoding="utf-8")
    assert list(tmp_path.glob("watering_history.jsonl.corrupt-*"))


def test_daily_check_pending_and_message() -> None:
    settings = load_example_settings()
    settings = settings.model_copy(
        update={
            "watering": settings.watering.model_copy(update={"check_hour": 12, "check_minute": 0})
        }
    )
    tz = ZoneInfo(settings.location.timezone)
    before = datetime(2024, 7, 15, 11, 59, tzinfo=tz)
    assert daily_check_pending(settings, State(), now=before) is False
    after = datetime(2024, 7, 15, 12, 1, tzinfo=tz)
    assert daily_check_pending(settings, State(), now=after) is True
    assert missed_check_message(settings, State(), now=after)
    done = State(last_weather_update=after.timestamp())
    assert daily_check_pending(settings, done, now=after) is False
    assert todays_check_at(settings, now=after).hour == 12


def test_live_rate_limit(settings_path: Path, monkeypatch, settings) -> None:
    reset_live_fetch_gate()
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    first = build_dashboard_view(settings_path, fetch_live=True)
    assert first.live_mode is True
    assert first.live_note is None
    second = build_dashboard_view(settings_path, fetch_live=True)
    assert second.live_mode is False
    assert second.live_note is not None
    assert str(int(LIVE_MIN_INTERVAL_SECONDS // 60)) in second.live_note


def test_missed_check_message_none_when_not_pending() -> None:
    settings = load_example_settings()
    settings = settings.model_copy(
        update={
            "watering": settings.watering.model_copy(update={"check_hour": 12, "check_minute": 0})
        }
    )
    tz = ZoneInfo(settings.location.timezone)
    before = datetime(2024, 7, 15, 11, 0, tzinfo=tz)
    assert missed_check_message(settings, State(), now=before) is None


def test_status_includes_missed_check(settings, monkeypatch) -> None:
    monkeypatch.setattr(
        "rain_bypass.status.local_now",
        lambda _s, now=None: datetime(
            2024, 7, 15, 12, 0, tzinfo=ZoneInfo(settings.location.timezone)
        ),
    )
    monkeypatch.setattr("rain_bypass.status.preview", lambda *_a, **_k: make_preview())
    snap = gather_status(settings, State(), fetch_live=False)
    text = format_status(snap)
    assert "Check status" in text


def test_status_omits_missed_check_when_current(settings) -> None:
    now = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo(settings.location.timezone))
    snap = StatusSnapshot(
        settings=settings,
        state=State(last_weather_update=now.timestamp()),
        local_time=now,
        next_check_seconds=3600.0,
        preview=make_preview(),
        fetch_live=False,
    )
    text = format_status(snap)
    assert "Check status" not in text


def test_atomic_write_cleans_temp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "note.txt"

    def boom(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("rain_bypass.persistence.os.replace", boom)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "data\n")
    assert not list(tmp_path.glob(".note.txt.*.tmp"))


def test_dashboard_renders_without_optional_alerts() -> None:
    view = DashboardView(
        verdict_label="ALLOW",
        verdict_class="allow",
        relay_short="open",
        hero_subtitle="ok",
        live_mode=False,
        mode_label="Saved forecast",
        location_short="53029",
        updated_meta="never",
        local_time="2024-07-15 12:00 CDT",
        next_check="12h",
        last_error=None,
        stale_check=None,
        live_note=None,
        sewer_lockout=None,
        balance=None,
        history_rows=(),
        refresh_seconds=60,
    )
    html = render_dashboard_html(view)
    assert "Missed check" not in html
    assert "Live weather" not in html
    assert "Last error" not in html


def test_quarantine_missing_and_replace_failure(tmp_path: Path, monkeypatch) -> None:
    from rain_bypass.persistence import quarantine_corrupt

    assert quarantine_corrupt(tmp_path / "missing.json") is None
    target = tmp_path / "broken.json"
    target.write_text("{bad", encoding="utf-8")

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr("rain_bypass.persistence.os.replace", boom)
    assert quarantine_corrupt(target) is None


def test_history_unreadable_repairs(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "watering_history.jsonl"
    path.write_text("x", encoding="utf-8")

    def boom(self, *args, **kwargs):
        raise UnicodeError("bad")

    monkeypatch.setattr(Path, "read_text", boom)
    assert _load_all_records(path) == []


def test_dashboard_renders_stale_and_live_notes(settings_path: Path, monkeypatch, settings) -> None:
    reset_live_fetch_gate()
    monkeypatch.setattr(
        "rain_bypass.web.missed_check_message",
        lambda *_a, **_k: "No check today since 00:00 - relay may be stale",
    )
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    view = build_dashboard_view(settings_path, fetch_live=False)
    html = render_dashboard_html(view)
    assert "Missed check" in html
    reset_live_fetch_gate()
    build_dashboard_view(settings_path, fetch_live=True)
    limited = build_dashboard_view(settings_path, fetch_live=True)
    assert "Live weather" in render_dashboard_html(limited)
