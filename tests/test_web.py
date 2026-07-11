from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from conftest import (
    build_dashboard,
    evaluation,
    mock_gather_status,
    patch_local_today,
    status_snapshot,
    watering_record,
)
from conftest import (
    preview as make_preview,
)
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import State, load_settings
from rain_bypass.history import WateringRecord, append_record, history_path
from rain_bypass.models import Preview
from rain_bypass.status import gather_status
from rain_bypass.web import (
    DashboardHistoryRow,
    DashboardHTTPServer,
    _collapse_history_rows,
    _decision_short,
    _hero_subtitle,
    _make_handler,
    _relay_short,
    _updated_meta,
    _verdict_badge,
    build_dashboard_view,
    render_dashboard_html,
    run_server,
)


def test_verdict_badge_allow_block_unknown() -> None:
    assert _verdict_badge(True, sewer_lockout=False) == ("ALLOW", "allow")
    assert _verdict_badge(False, sewer_lockout=False) == ("BLOCK", "block")
    assert _verdict_badge(None, sewer_lockout=False) == ("UNKNOWN", "unknown")
    assert _verdict_badge(True, sewer_lockout=True) == ("BLOCK", "block")


def test_dashboard_copy_helpers() -> None:
    assert _relay_short(True) == "Hardware relay open — panel can run"
    assert _relay_short(False) == "Hardware relay closed — panel blocked"
    assert _relay_short(None) == "Relay status unknown"
    assert _decision_short(True) == "Watering allowed until next check"
    assert _decision_short(False) == "Watering blocked until next check"
    assert _decision_short(None) == "Awaiting daily check"
    assert _updated_meta("never") == "Awaiting weather"
    assert _updated_meta("2024-07-15").startswith("Updated ")
    assert _hero_subtitle(
        sewer_lockout=True, decision_short="Watering blocked until next check"
    ).startswith("Seasonal")
    assert _collapse_history_rows(()) == ()
    row = DashboardHistoryRow(
        timestamp="2024-07-15 12:00",
        verdict="BLOCK",
        verdict_class="block",
        credit="+0.00 in",
        details="test",
    )
    assert len(_collapse_history_rows((row, row))) == 1
    assert _hero_subtitle(
        sewer_lockout=False, decision_short="Watering blocked until next check"
    ) == ("Watering blocked until next check")


def test_build_dashboard_view_uses_daily_verdict_not_projection(
    settings, settings_path: Path, monkeypatch
) -> None:
    """Hero follows saved relay state for the full check interval, not a live re-projection."""
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    state = State(
        watering_required=True,
        rainfall_inches=10.0,
        forecast_inches=0.0,
        max_daily_inches=0.0,
        freeze_block=False,
    )
    preview = make_preview(
        evaluation=evaluation(watering_required=False, balance_ok=False, deficit=-7.0),
    )
    snapshot = status_snapshot(settings, preview_obj=preview, state=state)
    mock_gather_status(monkeypatch, snapshot)
    view = build_dashboard_view(settings_path)
    assert snapshot.preview.would_water is False
    assert view.verdict_label == "ALLOW"
    assert view.hero_subtitle == "Watering allowed until next check"


def test_build_dashboard_view_cached(settings, settings_path: Path, monkeypatch) -> None:
    view = build_dashboard(settings_path, monkeypatch, fetch_live=False)
    assert view.verdict_label == "ALLOW"
    assert view.live_mode is False
    assert view.refresh_seconds == 60
    assert view.mode_label == "Saved forecast"
    assert view.balance is not None
    assert view.balance.received
    assert view.hero_subtitle == "Watering allowed until next check"


def test_build_dashboard_view_balance_surplus(settings, settings_path: Path, monkeypatch) -> None:
    preview = make_preview(
        evaluation=evaluation(
            deficit=-0.1,
            target_to_date=0.5,
            rain_mtd=0.4,
        ),
        irrigation_mtd=0.3,
    )
    view = build_dashboard(settings_path, monkeypatch, preview_obj=preview)
    assert view.balance is not None
    assert view.balance.deficit_class == "surplus"


def test_build_dashboard_view_balance_even(settings, settings_path: Path, monkeypatch) -> None:
    preview = make_preview(evaluation=evaluation(deficit=0.0))
    view = build_dashboard(settings_path, monkeypatch, preview_obj=preview)
    assert view.balance is not None
    assert view.balance.deficit_class == "even"


def test_build_dashboard_view_live_and_sewer(settings, settings_path: Path, monkeypatch) -> None:
    preview = make_preview(sewer_lockout=True, evaluation=evaluation(watering_required=False))
    view = build_dashboard(settings_path, monkeypatch, preview_obj=preview, fetch_live=True)
    assert view.verdict_label == "BLOCK"
    assert view.live_mode is True
    assert view.refresh_seconds is None
    assert view.sewer_lockout is not None
    assert "Sewer lockout" in view.sewer_lockout


def test_build_dashboard_view_with_history(settings, settings_path: Path, monkeypatch) -> None:
    path = history_path(settings)
    append_record(
        path,
        watering_record(
            checked_at=1_721_000_000.0,
            allowed=True,
            inches_credited=0.3,
            rain_mtd=0.1,
            forecast_inches=0.05,
            deficit=0.4,
        ),
    )
    view = build_dashboard(settings_path, monkeypatch, history_limit=5)
    assert len(view.history_rows) == 1
    assert view.history_rows[0].verdict == "ALLOW"


def test_build_dashboard_view_error_and_noevaluation(
    settings, settings_path: Path, monkeypatch
) -> None:
    state = State(last_error="<script>alert(1)</script>", watering_required=None)
    preview = Preview(
        irrigation_mtd=0.0,
        sewer_lockout=False,
        live=None,
        live_error=None,
        evaluation=None,
        cached_verdict=None,
        from_saved_weather=False,
        safety_known=False,
    )
    view = build_dashboard(settings_path, monkeypatch, preview_obj=preview, state=state)
    html_text = render_dashboard_html(view)
    assert view.last_error == "<script>alert(1)</script>"
    assert "<script>alert(1)</script>" not in html_text
    assert "&lt;script&gt;" in html_text
    assert view.verdict_label == "UNKNOWN"
    assert view.balance is None
    assert "Water budget will appear" in html_text


def test_render_dashboard_html_links(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    cached = render_dashboard_html(build_dashboard_view(settings_path, fetch_live=False))
    live = render_dashboard_html(build_dashboard_view(settings_path, fetch_live=True))
    assert 'href="/live"' in cached
    assert 'data-loading="true"' in cached
    assert 'http-equiv="refresh"' in cached
    assert 'href="/"' in live
    assert 'http-equiv="refresh"' not in live
    assert "Saved forecast" in cached


def test_render_dashboard_html_empty_history(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    html_text = render_dashboard_html(build_dashboard_view(settings_path))
    assert "No watering history yet." in html_text
    assert "Awaiting weather" in html_text


def test_render_dashboard_html_updated_pill(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    state = State(watering_required=True, last_weather_update=1_721_000_000.0)
    preview = make_preview()
    snapshot = status_snapshot(settings, preview_obj=preview, state=state)
    mock_gather_status(monkeypatch, snapshot)
    html_text = render_dashboard_html(build_dashboard_view(settings_path))
    assert "Updated " in html_text
    assert "Awaiting weather" not in html_text


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_dashboard_http_handler(settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    settings = load_settings(settings_path)
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    port = _free_port()
    handler = _make_handler()
    server = DashboardHTTPServer(("127.0.0.1", port), handler)
    server.settings_path = settings_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "ALLOW" in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/live") as live_response:
            live_body = live_response.read().decode("utf-8")
        assert "Live weather" in live_body
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/missing")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_render_sewer_lockout_html(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    preview = make_preview(sewer_lockout=True, evaluation=evaluation(watering_required=False))
    snapshot = status_snapshot(settings, preview_obj=preview)
    mock_gather_status(monkeypatch, snapshot)
    html_text = render_dashboard_html(build_dashboard_view(settings_path))
    assert "Sewer lockout active" in html_text


def test_render_with_history_rows(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    path = history_path(settings)
    append_record(
        path,
        WateringRecord(
            checked_at=1_721_000_000.0,
            local_date="2024-07-15",
            allowed=True,
            inches_credited=0.3,
            irrigation_mtd=0.3,
        ),
    )
    html_text = render_dashboard_html(build_dashboard_view(settings_path))
    assert "history-list" in html_text
    assert "+0.30 in" in html_text
    assert "Irrigation credited" in html_text
    assert "Water budget" in html_text
    assert "balance-stat-icon" in html_text
    assert "balance-headline" not in html_text
    assert "next-check-value" in html_text


def test_history_collapse_duplicate_timestamp(settings, settings_path: Path, monkeypatch) -> None:
    patch_local_today(monkeypatch, datetime(2024, 7, 15).date())
    snapshot = status_snapshot(settings)
    mock_gather_status(monkeypatch, snapshot)
    path = history_path(settings)
    ts = 1_721_000_000.0
    for allowed, credit in ((False, 0.0), (True, 0.63)):
        append_record(
            path,
            WateringRecord(
                checked_at=ts,
                local_date="2024-07-15",
                allowed=allowed,
                inches_credited=credit,
                irrigation_mtd=0.63,
            ),
        )
    view = build_dashboard_view(settings_path)
    assert len(view.history_rows) == 1


def test_cli_serve_keyboard_interrupt(monkeypatch, settings_path: Path) -> None:
    def interrupt(*_a, **_k) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.cli.run_server", interrupt)
    result = CliRunner().invoke(app, ["serve", "-c", str(settings_path)])
    assert result.exit_code == 0


def test_run_server_smoke(settings_path: Path, monkeypatch) -> None:
    created: list[object] = []

    class FakeServer:
        def __init__(self, _addr, handler) -> None:
            created.append(self)

        def serve_forever(self) -> None:
            raise KeyboardInterrupt

        def shutdown(self) -> None:
            pass

        def server_close(self) -> None:
            pass

    monkeypatch.setattr("rain_bypass.web.DashboardHTTPServer", FakeServer)
    run_server(settings_path, host="127.0.0.1", port=8080)
    assert len(created) == 1
    assert created[0].settings_path == settings_path


def test_run_server_low_port_bind_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "rain_bypass.web.DashboardHTTPServer",
        MagicMock(side_effect=OSError("permission denied")),
    )
    with pytest.raises(OSError, match="Binding to port 80 failed"):
        run_server("settings.toml", host="0.0.0.0", port=80)


def test_run_server_high_port_bind_error(monkeypatch) -> None:
    monkeypatch.setattr(
        "rain_bypass.web.DashboardHTTPServer",
        MagicMock(side_effect=OSError("address in use")),
    )
    with pytest.raises(OSError, match="address in use"):
        run_server("settings.toml", host="127.0.0.1", port=8080)


def test_gather_status_integration(settings, tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "watering_required": True,
                "rainfall_inches": 0.2,
                "forecast_inches": 0.1,
                "last_weather_update": 1_721_000_000.0,
            }
        ),
        encoding="utf-8",
    )
    runtime = settings.runtime.model_copy(update={"state_path": state_path})
    updated = settings.model_copy(update={"runtime": runtime})
    snapshot = gather_status(updated, State.load(state_path), fetch_live=False)
    assert snapshot.preview.irrigation_mtd >= 0.0


def test_cli_serve(monkeypatch, settings_path: Path) -> None:
    monkeypatch.setattr("rain_bypass.cli.run_server", lambda *_a, **_k: None)
    result = CliRunner().invoke(app, ["serve", "-c", str(settings_path)])
    assert result.exit_code == 0


def test_cli_serve_overrides(monkeypatch, settings_path: Path) -> None:
    captured: dict[str, object] = {}

    def fake_run_server(path, *, host, port) -> None:
        captured["path"] = path
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr("rain_bypass.cli.run_server", fake_run_server)
    result = CliRunner().invoke(
        app,
        ["serve", "-c", str(settings_path), "--host", "127.0.0.1", "--port", "9090"],
    )
    assert result.exit_code == 0
    assert captured == {
        "path": settings_path,
        "host": "127.0.0.1",
        "port": 9090,
    }


def test_cli_serve_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    result = CliRunner().invoke(app, ["serve", "-c", str(missing)])
    assert result.exit_code == 1


def test_cli_serve_bind_error(monkeypatch, settings_path: Path) -> None:
    monkeypatch.setattr(
        "rain_bypass.cli.run_server",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("bind failed")),
    )
    result = CliRunner().invoke(app, ["serve", "-c", str(settings_path)])
    assert result.exit_code == 1


def test_handler_log_message(caplog) -> None:
    import logging

    handler_class = _make_handler()
    mock_self = MagicMock()
    mock_self.address_string.return_value = "127.0.0.1"
    with caplog.at_level(logging.INFO, logger="rain_bypass.web"):
        handler_class.log_message(mock_self, "GET %s", "/")
    assert "GET /" in caplog.text
