from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from rain_bypass.config import State, load_settings
from rain_bypass.history import WateringRecord, history_path, load_records
from rain_bypass.status import (
    format_duration,
    format_updated,
    format_would_decide_now,
    gather_status,
    relay_label,
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
class DashboardView:
    verdict_label: str
    verdict_class: str
    relay_text: str
    live_mode: bool
    location: str
    local_time: str
    next_check: str
    irrigation_mtd: str
    rain_mtd: str
    forecast: str
    deficit: str
    would_decide: str
    last_updated: str
    last_error: str | None
    sewer_lockout: str | None
    history_rows: tuple[DashboardHistoryRow, ...]
    refresh_seconds: int | None


def _format_inches(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} in"


def _history_verdict(record: WateringRecord) -> tuple[str, str]:
    if record.sewer_lockout:
        return "BLOCK (sewer)", "block"
    if record.weather_error:
        return "BLOCK (weather)", "block"
    if record.allowed:
        return "ALLOW", "allow"
    return "BLOCK", "block"


def _history_row(record: WateringRecord, timezone: str) -> DashboardHistoryRow:
    when = datetime.fromtimestamp(record.checked_at, ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M")
    verdict, verdict_class = _history_verdict(record)
    credit = f"+{record.inches_credited:.2f} in"
    parts = [f"irr MTD {record.irrigation_mtd:.2f} in"]
    if record.rain_mtd is not None:
        parts.append(f"rain {record.rain_mtd:.2f}")
    if record.forecast_inches is not None:
        parts.append(f"fc {record.forecast_inches:.2f}")
    if record.deficit is not None:
        parts.append(f"deficit {record.deficit:.2f}")
    return DashboardHistoryRow(
        timestamp=when,
        verdict=verdict,
        verdict_class=verdict_class,
        credit=credit,
        details=", ".join(parts),
    )


def _verdict_badge(would_water: bool | None, *, sewer_lockout: bool) -> tuple[str, str]:
    if sewer_lockout:
        return "BLOCK", "block"
    if would_water is True:
        return "ALLOW", "allow"
    if would_water is False:
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
    balance_ok = evaluation.balance_ok if evaluation is not None else None
    would_label = format_would_decide_now(
        preview.would_water,
        safety_known=preview.safety_known,
        balance_ok=balance_ok,
    )
    verdict_label, verdict_class = _verdict_badge(
        preview.would_water,
        sewer_lockout=preview.sewer_lockout,
    )
    rain_mtd = evaluation.rain_mtd if evaluation is not None else snapshot.state.rainfall_inches
    forecast = (
        evaluation.forecast_inches if evaluation is not None else snapshot.state.forecast_inches
    )
    deficit = evaluation.deficit if evaluation is not None else None
    forecast_days = settings.balance.forecast_days
    sewer_note = None
    if preview.sewer_lockout:
        sewer = settings.sewer
        sewer_note = (
            f"Sewer lockout active "
            f"({sewer.start_month:02d}/{sewer.start_day:02d}"
            f"-{sewer.end_month:02d}/{sewer.end_day:02d})"
        )
    records = load_records(history_path(settings), limit=history_limit)
    rows = tuple(_history_row(record, loc.timezone) for record in reversed(records))
    return DashboardView(
        verdict_label=verdict_label,
        verdict_class=verdict_class,
        relay_text=relay_label(snapshot.state.watering_required),
        live_mode=fetch_live,
        location=f"{loc.zip_code} ({loc.timezone})",
        local_time=snapshot.local_time.strftime("%Y-%m-%d %H:%M %Z"),
        next_check=format_duration(snapshot.next_check_seconds),
        irrigation_mtd=_format_inches(preview.irrigation_mtd),
        rain_mtd=_format_inches(rain_mtd),
        forecast=f"{_format_inches(forecast)} ({forecast_days} days)",
        deficit=_format_inches(deficit),
        would_decide=would_label,
        last_updated=format_updated(
            snapshot.state.last_weather_update,
            loc.timezone,
            now=snapshot.local_time,
        ),
        last_error=snapshot.state.last_error,
        sewer_lockout=sewer_note,
        history_rows=rows,
        refresh_seconds=None if fetch_live else 60,
    )


def render_dashboard_html(view: DashboardView) -> str:
    esc = html.escape
    mode_note = "Live weather" if view.live_mode else "Cached status"
    refresh_meta = ""
    if view.refresh_seconds is not None:
        refresh_meta = f'<meta http-equiv="refresh" content="{view.refresh_seconds}">'
    history_html = ""
    if view.history_rows:
        rows: list[str] = []
        for row in view.history_rows:
            rows.append(
                f'<li class="history-item">'
                f'<span class="history-time">{esc(row.timestamp)}</span> '
                f'<span class="badge badge-{esc(row.verdict_class)}">{esc(row.verdict)}</span> '
                f'<span class="history-credit">{esc(row.credit)}</span>'
                f'<div class="history-details">{esc(row.details)}</div>'
                f"</li>"
            )
        history_html = f'<ul class="history-list">{"".join(rows)}</ul>'
    else:
        history_html = '<p class="muted">No watering history yet.</p>'

    error_html = ""
    if view.last_error:
        error_html = f'<p class="error">Last error: {esc(view.last_error)}</p>'

    sewer_html = ""
    if view.sewer_lockout:
        sewer_html = f'<p class="warn">{esc(view.sewer_lockout)}</p>'

    footer_link = (
        '<a href="/">Back to cached view</a>'
        if view.live_mode
        else '<a href="/live">Refresh live weather</a>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{refresh_meta}
<title>Sprinkler Rain Bypass</title>
<style>
:root {{
  color-scheme: light dark;
  --bg: #f0f2f5;
  --card: #fff;
  --text: #1a1a1a;
  --muted: #666;
  --allow: #1b7f3a;
  --allow-bg: #e6f4ea;
  --block: #c5221f;
  --block-bg: #fce8e6;
  --unknown-bg: #f1f3f4;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #121212;
    --card: #1e1e1e;
    --text: #e8e8e8;
    --muted: #aaa;
    --allow-bg: #1e3a24;
    --block-bg: #3a1e1e;
    --unknown-bg: #2a2a2a;
  }}
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
  padding: max(1rem, env(safe-area-inset-top))
           max(1rem, env(safe-area-inset-right))
           max(1rem, env(safe-area-inset-bottom))
           max(1rem, env(safe-area-inset-left));
}}
.wrap {{ max-width: 480px; margin: 0 auto; }}
.card {{
  background: var(--card);
  border-radius: 12px;
  padding: 1rem 1.1rem;
  margin-bottom: 0.85rem;
  box-shadow: 0 1px 3px rgba(0,0,0,.08);
}}
.hero {{ text-align: center; padding: 1.25rem 1rem; }}
.badge {{
  display: inline-block;
  font-weight: 700;
  font-size: 1.5rem;
  letter-spacing: 0.04em;
  padding: 0.35rem 1rem;
  border-radius: 999px;
}}
.badge-allow {{ background: var(--allow-bg); color: var(--allow); }}
.badge-block {{ background: var(--block-bg); color: var(--block); }}
.badge-unknown {{ background: var(--unknown-bg); color: var(--muted); }}
.relay {{ margin-top: 0.5rem; color: var(--muted); font-size: 0.95rem; }}
.mode {{ font-size: 0.85rem; color: var(--muted); margin-bottom: 0.5rem; }}
h2 {{ font-size: 1rem; margin: 0 0 0.65rem; }}
dl {{ margin: 0; display: grid; grid-template-columns: 9.5rem 1fr; gap: 0.35rem 0.5rem; }}
dt {{ color: var(--muted); margin: 0; }}
dd {{ margin: 0; font-weight: 500; }}
.history-list {{ list-style: none; padding: 0; margin: 0; }}
.history-item {{ padding: 0.55rem 0; border-bottom: 1px solid rgba(127,127,127,.2); }}
.history-item:last-child {{ border-bottom: none; }}
.history-time {{ font-size: 0.85rem; color: var(--muted); }}
.history-credit {{ font-weight: 600; }}
.history-details {{ font-size: 0.85rem; color: var(--muted); margin-top: 0.15rem; }}
.muted {{ color: var(--muted); }}
.error {{ color: var(--block); font-size: 0.9rem; }}
.warn {{ color: #b06000; font-size: 0.9rem; }}
footer {{ text-align: center; padding: 0.5rem 0 1rem; font-size: 0.95rem; }}
footer a {{ color: #1967d2; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="card hero">
    <p class="mode">{esc(mode_note)}</p>
    <div class="badge badge-{esc(view.verdict_class)}">{esc(view.verdict_label)}</div>
    <p class="relay">{esc(view.relay_text)}</p>
  </div>
  <div class="card">
    <h2>Summary</h2>
    <dl>
      <dt>Location</dt><dd>{esc(view.location)}</dd>
      <dt>Local time</dt><dd>{esc(view.local_time)}</dd>
      <dt>Next check</dt><dd>{esc(view.next_check)}</dd>
      <dt>Irrigation MTD</dt><dd>{esc(view.irrigation_mtd)}</dd>
      <dt>Rain MTD</dt><dd>{esc(view.rain_mtd)}</dd>
      <dt>Forecast</dt><dd>{esc(view.forecast)}</dd>
      <dt>Deficit</dt><dd>{esc(view.deficit)}</dd>
      <dt>Would decide</dt><dd>{esc(view.would_decide)}</dd>
      <dt>Last updated</dt><dd>{esc(view.last_updated)}</dd>
    </dl>
    {sewer_html}
    {error_html}
  </div>
  <div class="card">
    <h2>Recent history</h2>
    {history_html}
  </div>
  <footer>{footer_link}</footer>
</div>
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
