from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import mock_gather_status, status_snapshot

from rain_bypass.config import State
from rain_bypass.history import WateringRecord, append_record, history_path
from rain_bypass.web import (
    DashboardHTTPServer,
    _make_handler,
    _stale_check_message,
    build_dashboard_view,
    render_dashboard_html,
    reset_live_fetch_gate,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, _newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def test_stale_check_message_paths(settings, monkeypatch) -> None:
    tz = ZoneInfo(settings.location.timezone)
    now = datetime(2024, 7, 15, 12, 30, tzinfo=tz)

    monkeypatch.setattr("rain_bypass.web.missed_check_message", lambda *_a, **_k: None)
    assert _stale_check_message(settings, State(), now=now) is None

    monkeypatch.setattr(
        "rain_bypass.web.missed_check_message",
        lambda *_a, **_k: "No check today since 00:00 - relay may be stale",
    )
    never = _stale_check_message(settings, State(), now=now)
    assert never is not None
    assert "No successful check" in never

    stamped = State(last_weather_update=now.timestamp() - 7200)
    aged = _stale_check_message(settings, stamped, now=now)
    assert aged is not None
    assert "Last successful check" in aged


def test_live_rate_limit_redirects_home_with_note(
    settings_path: Path, monkeypatch, settings
) -> None:
    reset_live_fetch_gate()
    mock_gather_status(monkeypatch, status_snapshot(settings))
    first = build_dashboard_view(settings_path, fetch_live=True)
    assert first.live_mode is True

    port = _free_port()
    handler = _make_handler()
    server = DashboardHTTPServer(("127.0.0.1", port), handler)
    server.settings_path = settings_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            opener.open(f"http://127.0.0.1:{port}/live")
        assert exc_info.value.code == 303
        assert exc_info.value.headers.get("Location") == "/"

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Live weather" in body
        assert "alert-stack" in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_history_details_are_collapsed(settings, settings_path: Path, monkeypatch) -> None:
    mock_gather_status(monkeypatch, status_snapshot(settings))
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
    html = render_dashboard_html(build_dashboard_view(settings_path))
    assert "history-more" in html
    assert "<summary>Details</summary>" in html
