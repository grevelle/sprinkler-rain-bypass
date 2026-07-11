from __future__ import annotations

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from rain_bypass.config import State, format_sewer_range, load_settings
from rain_bypass.history import (
    WateringRecord,
    history_path,
    load_records,
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

logger = logging.getLogger(__name__)

_BIND_LOW_PORT_HINT = (
    "Binding to port {port} failed ({exc}). On Linux, grant CAP_NET_BIND_SERVICE "
    "in the systemd unit or use a higher port (e.g. rain-bypass serve --port 8080)."
)


@dataclass(frozen=True, slots=True)
class DashboardHistoryRow:
    timestamp: str
    verdict: str
    verdict_class: str
    credit: str
    details: str


@dataclass(frozen=True, slots=True)
class DashboardBalance:
    target: str
    rain: str
    irrigation: str
    received: str
    forecast: str
    forecast_label: str
    deficit_amount: str
    deficit_note: str
    deficit_class: str
    progress_pct: int


_RAIN_STAT_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    '<path d="M20 16.2A4.5 4.5 0 0 0 17.5 8h-.3A6 6 0 1 0 6 16.2"/>'
    '<path d="M8 19v2M12 19v2M16 19v2"/>'
    "</svg>"
)
_IRRIGATION_STAT_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>'
    "</svg>"
)
_RECEIVED_STAT_ICON = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
    '<path d="M12 2L2 7l10 5 10-5-10-5z"/>'
    '<path d="M2 17l10 5 10-5"/>'
    '<path d="M2 12l10 5 10-5"/>'
    "</svg>"
)


def _balance_stat_html(
    label: str,
    value: str,
    icon: str,
    esc: Callable[[str], str],
    *,
    extra_class: str = "",
) -> str:
    stat_class = f"balance-stat {extra_class}".strip()
    return f"""
      <div class="{stat_class}">
        <span class="balance-stat-icon" aria-hidden="true">{icon}</span>
        <span class="balance-stat-label">{esc(label)}</span>
        <span class="balance-stat-value">{esc(value)}</span>
      </div>"""


@dataclass(frozen=True, slots=True)
class DashboardView:
    verdict_label: str
    verdict_class: str
    relay_short: str
    hero_subtitle: str
    live_mode: bool
    mode_label: str
    location_short: str
    updated_meta: str
    local_time: str
    next_check: str
    last_error: str | None
    sewer_lockout: str | None
    balance: DashboardBalance | None
    history_rows: tuple[DashboardHistoryRow, ...]
    refresh_seconds: int | None


@dataclass(frozen=True, slots=True)
class BalanceSummary:
    target: float
    rain: float
    irrigation: float
    received: float
    forecast: float
    deficit: float
    progress_pct: int
    deficit_class: str
    deficit_note: str

    @classmethod
    def from_evaluation(
        cls,
        evaluation: Evaluation,
        irrigation_mtd: float,
        *,
        inches_per_cycle: float,
    ) -> BalanceSummary:
        rain = evaluation.rain_mtd
        forecast = evaluation.forecast_inches
        target = evaluation.target_to_date
        deficit = evaluation.deficit
        received = rain + irrigation_mtd
        effective = received + forecast
        progress_pct = min(100, round(100 * effective / target)) if target > 0 else 0
        if deficit > 0:
            deficit_class = "need"
            deficit_note = (
                f"{deficit:.2f} in gap before balance allows a cycle "
                f"(need ≥ {inches_per_cycle:.2f} in)"
            )
        elif deficit < 0:
            deficit_class = "surplus"
            deficit_note = f"{abs(deficit):.2f} in over the monthly target pace"
        else:
            deficit_class = "even"
            deficit_note = "On target — no watering needed for balance"
        return cls(
            target=target,
            rain=rain,
            irrigation=irrigation_mtd,
            received=received,
            forecast=forecast,
            deficit=deficit,
            progress_pct=progress_pct,
            deficit_class=deficit_class,
            deficit_note=deficit_note,
        )


_dashboard_css_cache: str | None = None


def _load_dashboard_css() -> str:
    global _dashboard_css_cache
    if _dashboard_css_cache is None:
        _dashboard_css_cache = (
            files("rain_bypass").joinpath("static/dashboard.css").read_text(encoding="utf-8")
        )
    return _dashboard_css_cache


def _history_verdict(record: WateringRecord) -> tuple[str, str]:
    return watering_verdict(record)


def _history_details(record: WateringRecord) -> str:
    return watering_record_details(record)


def _history_row(record: WateringRecord, timezone: str) -> DashboardHistoryRow:
    when = datetime.fromtimestamp(record.checked_at, ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
    verdict, verdict_class = _history_verdict(record)
    credit = f"+{record.inches_credited:.2f} in"
    return DashboardHistoryRow(
        timestamp=when,
        verdict=verdict,
        verdict_class=verdict_class,
        credit=credit,
        details=_history_details(record),
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
    summary = BalanceSummary.from_evaluation(
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


def _verdict_badge(watering_required: bool | None, *, sewer_lockout: bool) -> tuple[str, str]:
    if sewer_lockout:
        return "BLOCK", "block"
    if watering_required is True:
        return "ALLOW", "allow"
    if watering_required is False:
        return "BLOCK", "block"
    return "UNKNOWN", "unknown"


def build_dashboard_view(
    settings_path: Path | str,
    *,
    fetch_live: bool = False,
    history_limit: int = 14,
) -> DashboardView:
    settings = load_settings(settings_path)
    state = State.load(settings.runtime.state_path)
    snapshot = gather_status(settings, state, fetch_live=fetch_live)
    preview = snapshot.preview
    loc = settings.location
    evaluation = preview.evaluation
    daily_verdict = snapshot.state.watering_required
    verdict_label, verdict_class = _verdict_badge(
        daily_verdict,
        sewer_lockout=preview.sewer_lockout,
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
        live_mode=fetch_live,
        mode_label="Live weather" if fetch_live else "Saved forecast",
        location_short=loc.zip_code,
        updated_meta=_updated_meta(last_updated),
        local_time=snapshot.local_time.strftime("%Y-%m-%d %H:%M %Z"),
        next_check=format_duration(snapshot.next_check_seconds),
        last_error=snapshot.state.last_error,
        sewer_lockout=sewer_note,
        balance=balance,
        history_rows=rows,
        refresh_seconds=None if fetch_live else 60,
    )


def _history_item_html(row: DashboardHistoryRow, esc: Callable[[str], str]) -> str:
    return (
        f'<li class="history-item">'
        f'<div class="history-track" aria-hidden="true">'
        f'<span class="history-dot dot-{esc(row.verdict_class)}"></span>'
        f"</div>"
        f'<div class="history-body">'
        f'<div class="history-top">'
        f'<time class="history-time">{esc(row.timestamp)}</time>'
        f'<span class="chip chip-{esc(row.verdict_class)}">{esc(row.verdict)}</span>'
        f'<span class="history-credit">{esc(row.credit)}</span>'
        f"</div>"
        f'<p class="history-details">{esc(row.details)}</p>'
        f"</div>"
        f"</li>"
    )


def _balance_card_html(balance: DashboardBalance, esc: Callable[[str], str]) -> str:
    gap_label = {
        "need": "Gap to allow cycle",
        "surplus": "Over target pace",
        "even": "Balance gap",
    }[balance.deficit_class]
    return f"""
  <section class="card balance-card">
    <div class="section-head">
      <h2>Water budget</h2>
      <span>Target {esc(balance.target)}</span>
    </div>
    <div class="balance-track" role="progressbar"
         aria-valuenow="{balance.progress_pct}" aria-valuemin="0" aria-valuemax="100"
         aria-label="Water received toward monthly target pace">
      <div class="balance-fill balance-{esc(balance.deficit_class)}"
           style="width:{balance.progress_pct}%"></div>
    </div>
    <div class="balance-stats">
{_balance_stat_html("Rain", balance.rain, _RAIN_STAT_ICON, esc)}
{_balance_stat_html("Irrigation", balance.irrigation, _IRRIGATION_STAT_ICON, esc)}
{
        _balance_stat_html(
            "Received",
            balance.received,
            _RECEIVED_STAT_ICON,
            esc,
            extra_class="balance-stat-total",
        )
    }
    </div>
    <div class="balance-foot">
      <div class="balance-foot-row">
        <span class="balance-foot-label">{esc(balance.forecast_label)}</span>
        <span class="balance-foot-value">{esc(balance.forecast)}</span>
      </div>
      <div class="balance-foot-row balance-gap balance-gap-{esc(balance.deficit_class)}">
        <span class="balance-foot-label">{esc(gap_label)}</span>
        <span class="balance-foot-value">{esc(balance.deficit_amount)}</span>
      </div>
      <p class="balance-note">{esc(balance.deficit_note)}</p>
    </div>
  </section>"""


def render_dashboard_html(view: DashboardView) -> str:
    esc = html.escape
    mode_class = "mode-live" if view.live_mode else "mode-cached"
    refresh_meta = ""
    if view.refresh_seconds is not None:
        refresh_meta = f'<meta http-equiv="refresh" content="{view.refresh_seconds}">'

    if view.history_rows:
        items = "".join(_history_item_html(row, esc) for row in view.history_rows)
        history_html = f'<ul class="history-list">{items}</ul>'
    else:
        history_html = '<p class="empty-state">No watering history yet.</p>'

    error_html = ""
    if view.last_error:
        error_html = (
            f'<div class="alert alert-error" role="alert">'
            f"<strong>Last error</strong> {esc(view.last_error)}"
            f"</div>"
        )

    sewer_html = ""
    if view.sewer_lockout:
        sewer_html = (
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Sewer lockout</strong> {esc(view.sewer_lockout)}"
            f"</div>"
        )

    footer_href = "/" if view.live_mode else "/live"
    footer_label = "Back to saved forecast" if view.live_mode else "Refresh live weather"
    footer_icon = "←" if view.live_mode else "↻"
    footer_loading = "false" if view.live_mode else "true"
    balance_html = (
        _balance_card_html(view.balance, esc)
        if view.balance is not None
        else (
            '<section class="card balance-card balance-unavailable">'
            '<p class="empty-state">Water budget will appear after the first weather check.</p>'
            "</section>"
        )
    )

    brand_icon = (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<path d="M12 2.5c0 0-6 6.2-6 10.8a6 6 0 0 0 12 0C18 8.7 12 2.5 12 2.5z"'
        ' fill="currentColor" opacity="0.95"/>'
        '<path d="M12 6.5c0 0-3 3.4-3 5.8a3 3 0 0 0 6 0c0-2.4-3-5.8-3-5.8z"'
        ' fill="#fff" opacity="0.35"/>'
        "</svg>"
    )

    dashboard_css = _load_dashboard_css()

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d9488">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
{refresh_meta}
<title>Sprinkler Rain Bypass</title>
<style>
{dashboard_css}
</style>
</head>
<body>
<div class="wrap">
  <header class="topbar">
    <div class="brand">
      <div class="brand-icon">{brand_icon}</div>
      <div class="brand-text">
        <span class="brand-title">Rain Bypass</span>
        <span class="brand-sub">{esc(view.location_short)}</span>
      </div>
    </div>
    <div class="topbar-meta">
      <span class="mode-pill {mode_class}">{esc(view.mode_label)}</span>
      <span class="updated-pill">{esc(view.updated_meta)}</span>
    </div>
  </header>

  <section class="card hero hero-{esc(view.verdict_class)}" aria-labelledby="verdict-label">
    <div class="status-stage">
      <div class="status-glow status-ring-{esc(view.verdict_class)}" aria-hidden="true"></div>
      <div class="status-ring status-ring-{esc(view.verdict_class)}"
           role="img" id="verdict-label"
           aria-label="{esc(view.verdict_label)} watering status">
        <span class="status-word" aria-hidden="true">{esc(view.verdict_label)}</span>
      </div>
    </div>
    <p class="hero-subtitle">{esc(view.hero_subtitle)}</p>
    <p class="relay-short">{esc(view.relay_short)}</p>
  </section>

  {balance_html}

  <section class="card schedule-card">
    <div class="section-head">
      <h2>Next check</h2>
      <span>{esc(view.local_time)}</span>
    </div>
    <p class="next-check-value">{esc(view.next_check)}</p>
    {sewer_html}
    {error_html}
  </section>

  <section class="card">
    <div class="section-head">
      <h2>Recent history</h2>
      <span>{len(view.history_rows)} events</span>
    </div>
    {history_html}
  </section>
</div>

<footer>
  <div class="footer-inner">
    <a class="footer-btn" href="{footer_href}" data-loading="{footer_loading}">
      <span>{footer_icon}</span>{esc(footer_label)}
    </a>
  </div>
</footer>
<script>
(function () {{
  var btn = document.querySelector('.footer-btn[data-loading="true"]');
  if (!btn) return;
  btn.addEventListener('click', function () {{
    btn.classList.add('is-loading');
    btn.setAttribute('aria-busy', 'true');
    btn.innerHTML = '<span>↻</span>Fetching weather…';
  }});
}})();
</script>
</body>
</html>"""


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
