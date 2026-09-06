from __future__ import annotations

import logging
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from rain_bypass.balance import balance_display
from rain_bypass.config import State, format_sewer_range, load_settings
from rain_bypass.dashboard_html import (
    DashboardBalance,
    DashboardHistoryRow,
    DashboardView,
    render_dashboard_html,
)
from rain_bypass.history import (
    WateringRecord,
    history_path,
    load_records,
    verdict_badge,
    watering_record_details,
    watering_verdict,
)
from rain_bypass.models import Evaluation
from rain_bypass.status import (
    format_duration,
    format_inches,
    format_updated,
    gather_status,
)
from rain_bypass.windows import missed_check_message

logger = logging.getLogger(__name__)

LIVE_MIN_INTERVAL_SECONDS = 300.0
_last_live_fetch_mono = 0.0


def reset_live_fetch_gate() -> None:
    """Clear the /live rate-limit gate (tests and rare admin use)."""
    global _last_live_fetch_mono
    _last_live_fetch_mono = 0.0


_BIND_LOW_PORT_HINT = (
    "Binding to port {port} failed ({exc}). On Linux, grant CAP_NET_BIND_SERVICE "
    "in the systemd unit or use a higher port (e.g. rain-bypass serve --port 8080)."
)


def _history_row(record: WateringRecord, timezone: str) -> DashboardHistoryRow:
    when = datetime.fromtimestamp(record.checked_at, ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
    verdict, verdict_class = watering_verdict(record)
    credit = f"+{record.inches_credited:.2f} in"
    return DashboardHistoryRow(
        timestamp=when,
        verdict=verdict,
        verdict_class=verdict_class,
        credit=credit,
        details=watering_record_details(record),
    )


def _collapse_history_rows(
    rows: tuple[DashboardHistoryRow, ...],
) -> tuple[DashboardHistoryRow, ...]:
    if not rows:
        return rows
    collapsed: list[DashboardHistoryRow] = []
    for row in rows:
        if collapsed and collapsed[-1].timestamp == row.timestamp:
            continue
        collapsed.append(row)
    return tuple(collapsed)


def _relay_short(allowed: bool | None) -> str:
    if allowed is None:
        return "Relay status unknown"
    if allowed:
        return "Hardware relay open — panel can run"
    return "Hardware relay closed — panel blocked"


def _decision_short(watering_required: bool | None) -> str:
    if watering_required is True:
        return "Watering allowed until next check"
    if watering_required is False:
        return "Watering blocked until next check"
    return "Awaiting daily check"


def _hero_subtitle(*, sewer_lockout: bool, decision_short: str) -> str:
    if sewer_lockout:
        return "Seasonal sewer lockout — watering blocked"
    return decision_short


def _updated_meta(last_updated: str) -> str:
    if last_updated == "never":
        return "Awaiting weather"
    return f"Updated {last_updated}"


def _build_balance(
    evaluation: Evaluation,
    irrigation_mtd: float,
    *,
    inches_per_cycle: float,
    forecast_days: int,
) -> DashboardBalance:
    summary = balance_display(
        evaluation,
        irrigation_mtd,
        inches_per_cycle=inches_per_cycle,
    )
    return DashboardBalance(
        target=format_inches(summary.target),
        rain=format_inches(summary.rain),
        irrigation=format_inches(summary.irrigation),
        received=format_inches(summary.received),
        forecast=format_inches(summary.forecast),
        forecast_label=f"Forecast credit ({forecast_days} days)",
        deficit_amount=format_inches(abs(summary.deficit) if summary.deficit != 0 else 0.0),
        deficit_note=summary.deficit_note,
        deficit_class=summary.deficit_class,
        progress_pct=summary.progress_pct,
    )


def build_dashboard_view(
    settings_path: Path | str,
    *,
    fetch_live: bool = False,
    history_limit: int = 14,
) -> DashboardView:
    global _last_live_fetch_mono
    settings = load_settings(settings_path)
    state = State.load(settings.runtime.state_path)
    live_note: str | None = None
    effective_live = fetch_live
    if fetch_live:
        elapsed = time.monotonic() - _last_live_fetch_mono
        if _last_live_fetch_mono and elapsed < LIVE_MIN_INTERVAL_SECONDS:
            remaining = int(LIVE_MIN_INTERVAL_SECONDS - elapsed)
            live_note = (
                f"Live refresh limited to once per "
                f"{int(LIVE_MIN_INTERVAL_SECONDS // 60)} min "
                f"({remaining}s remaining) — showing saved forecast"
            )
            effective_live = False
        else:
            _last_live_fetch_mono = time.monotonic()
    snapshot = gather_status(settings, state, fetch_live=effective_live)
    preview = snapshot.preview
    loc = settings.location
    evaluation = preview.evaluation
    daily_verdict = snapshot.state.watering_required
    verdict_label, verdict_class = verdict_badge(
        allowed=daily_verdict,
        sewer_lockout=preview.sewer_lockout,
        detail=False,
    )
    forecast_days = settings.balance.forecast_days
    balance = None
    if evaluation is not None:
        balance = _build_balance(
            evaluation,
            preview.irrigation_mtd,
            inches_per_cycle=settings.balance.inches_per_cycle,
            forecast_days=forecast_days,
        )
    last_updated = format_updated(
        snapshot.state.last_weather_update,
        loc.timezone,
        now=snapshot.local_time,
    )
    sewer_note = None
    if preview.sewer_lockout:
        sewer = settings.sewer
        sewer_note = f"Sewer lockout active ({format_sewer_range(sewer)})"
    records = load_records(history_path(settings), limit=history_limit)
    rows = _collapse_history_rows(
        tuple(_history_row(record, loc.timezone) for record in reversed(records))
    )
    decision = _decision_short(daily_verdict)
    return DashboardView(
        verdict_label=verdict_label,
        verdict_class=verdict_class,
        relay_short=_relay_short(snapshot.state.watering_required),
        hero_subtitle=_hero_subtitle(
            sewer_lockout=preview.sewer_lockout,
            decision_short=decision,
        ),
        live_mode=effective_live,
        mode_label="Live weather" if effective_live else "Saved forecast",
        location_short=loc.zip_code,
        updated_meta=_updated_meta(last_updated),
        local_time=snapshot.local_time.strftime("%Y-%m-%d %H:%M %Z"),
        next_check=format_duration(snapshot.next_check_seconds),
        last_error=snapshot.state.last_error,
        stale_check=missed_check_message(settings, snapshot.state, now=snapshot.local_time),
        live_note=live_note,
        sewer_lockout=sewer_note,
        balance=balance,
        history_rows=rows,
        refresh_seconds=None if effective_live else 60,
    )


class DashboardHTTPServer(ThreadingHTTPServer):
    settings_path: Path


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path == "/":
                fetch_live = False
            elif path == "/live":
                fetch_live = True
            else:
                self.send_error(404)
                return
            dashboard_server = cast(DashboardHTTPServer, self.server)
            view = build_dashboard_view(dashboard_server.settings_path, fetch_live=fetch_live)
            body = render_dashboard_html(view).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            logger.info("%s - %s", self.address_string(), format % args)

    return DashboardHandler


def run_server(settings_path: Path | str, *, host: str, port: int) -> None:
    path = Path(settings_path)
    handler = _make_handler()
    try:
        server = DashboardHTTPServer((host, port), handler)
    except OSError as exc:
        if port < 1024:
            raise OSError(_BIND_LOW_PORT_HINT.format(port=port, exc=exc)) from exc
        raise
    server.settings_path = path
    logger.info("dashboard listening on http://%s:%s/", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("dashboard stopped")
    finally:
        server.shutdown()
        server.server_close()
