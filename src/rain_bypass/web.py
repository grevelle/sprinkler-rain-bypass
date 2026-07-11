from __future__ import annotations

import html
import logging
from collections.abc import Callable
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


def _metric_tile(
    label: str,
    value: str,
    esc: Callable[[str], str],
    *,
    accent: str = "",
    icon: str = "",
) -> str:
    accent_class = f" metric-{accent}" if accent else ""
    icon_html = f'<span class="metric-icon" aria-hidden="true">{icon}</span>' if icon else ""
    return (
        f'<div class="metric{accent_class}">'
        f"{icon_html}"
        f'<span class="metric-label">{esc(label)}</span>'
        f'<span class="metric-value">{esc(value)}</span>'
        f"</div>"
    )


def _detail_row(
    label: str,
    value: str,
    esc: Callable[[str], str],
    *,
    highlight: bool = False,
) -> str:
    row_class = " detail-row-highlight" if highlight else ""
    return (
        f'<li class="detail-row{row_class}">'
        f'<span class="detail-label">{esc(label)}</span>'
        f'<span class="detail-value">{esc(value)}</span>'
        f"</li>"
    )


def render_dashboard_html(view: DashboardView) -> str:
    esc = html.escape
    mode_label = "Live weather" if view.live_mode else "Cached"
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
    footer_label = "Back to cached view" if view.live_mode else "Refresh live weather"
    footer_icon = "←" if view.live_mode else "↻"
    if view.last_updated == "never":
        updated_meta = "Awaiting weather"
    else:
        updated_meta = f"Updated {view.last_updated}"

    rain_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M20 16.2A4.5 4.5 0 0 0 17.5 8h-.3A6 6 0 1 0 6 16.2"/>'
        '<path d="M8 19v2M12 19v2M16 19v2"/>'
        "</svg>"
    )
    irrigation_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"/>'
        "</svg>"
    )
    deficit_icon = (
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">'
        '<path d="M3 3v18h18"/><path d="M7 16l4-4 4 4 5-6"/>'
        "</svg>"
    )
    brand_icon = (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<path d="M12 2.5c0 0-6 6.2-6 10.8a6 6 0 0 0 12 0C18 8.7 12 2.5 12 2.5z"'
        ' fill="currentColor" opacity="0.95"/>'
        '<path d="M12 6.5c0 0-3 3.4-3 5.8a3 3 0 0 0 6 0c0-2.4-3-5.8-3-5.8z"'
        ' fill="#fff" opacity="0.35"/>'
        "</svg>"
    )

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
:root {{
  color-scheme: light dark;
  --bg-1: #dbeafe;
  --bg-2: #ecfeff;
  --bg-3: #f0fdfa;
  --orb-a: rgba(14, 165, 233, 0.22);
  --orb-b: rgba(20, 184, 166, 0.18);
  --orb-c: rgba(59, 130, 246, 0.12);
  --card: rgba(255, 255, 255, 0.78);
  --card-solid: #ffffff;
  --card-border: rgba(255, 255, 255, 0.85);
  --text: #0f172a;
  --muted: #64748b;
  --line: rgba(100, 116, 139, 0.16);
  --allow: #047857;
  --allow-soft: rgba(16, 185, 129, 0.14);
  --allow-glow: rgba(16, 185, 129, 0.42);
  --block: #b91c1c;
  --block-soft: rgba(239, 68, 68, 0.12);
  --block-glow: rgba(239, 68, 68, 0.38);
  --unknown-soft: rgba(148, 163, 184, 0.2);
  --accent: #0d9488;
  --accent-2: #0284c7;
  --shadow-sm: 0 4px 16px rgba(15, 23, 42, 0.06);
  --shadow: 0 20px 50px rgba(15, 23, 42, 0.1);
  --radius: 22px;
  --radius-sm: 16px;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg-1: #0c4a6e;
    --bg-2: #134e4a;
    --bg-3: #0f172a;
    --orb-a: rgba(56, 189, 248, 0.14);
    --orb-b: rgba(45, 212, 191, 0.12);
    --orb-c: rgba(96, 165, 250, 0.1);
    --card: rgba(15, 23, 42, 0.72);
    --card-solid: #1e293b;
    --card-border: rgba(148, 163, 184, 0.12);
    --text: #f8fafc;
    --muted: #94a3b8;
    --line: rgba(148, 163, 184, 0.18);
    --allow-soft: rgba(16, 185, 129, 0.18);
    --block-soft: rgba(239, 68, 68, 0.16);
    --unknown-soft: rgba(148, 163, 184, 0.22);
    --shadow-sm: 0 4px 16px rgba(0, 0, 0, 0.22);
    --shadow: 0 24px 60px rgba(0, 0, 0, 0.45);
  }}
}}
* {{ box-sizing: border-box; }}
html {{ scroll-behavior: smooth; }}
body {{
  margin: 0;
  min-height: 100dvh;
  font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background:
    radial-gradient(ellipse 80% 50% at 10% -10%, var(--orb-a), transparent 55%),
    radial-gradient(ellipse 70% 45% at 95% 5%, var(--orb-b), transparent 50%),
    radial-gradient(ellipse 60% 40% at 50% 100%, var(--orb-c), transparent 55%),
    linear-gradient(165deg, var(--bg-1) 0%, var(--bg-2) 38%, var(--bg-3) 100%);
  background-attachment: fixed;
  color: var(--text);
  line-height: 1.5;
  padding:
    max(0.85rem, env(safe-area-inset-top))
    max(1rem, env(safe-area-inset-right))
    max(5.5rem, calc(env(safe-area-inset-bottom) + 4.5rem))
    max(1rem, env(safe-area-inset-left));
  -webkit-font-smoothing: antialiased;
  -webkit-tap-highlight-color: transparent;
}}
body::before {{
  content: "";
  position: fixed;
  inset: 0;
  pointer-events: none;
  opacity: 0.35;
  background-image: radial-gradient(circle at 1px 1px, rgba(148,163,184,0.18) 1px, transparent 0);
  background-size: 22px 22px;
  mask-image: linear-gradient(180deg, black, transparent 85%);
}}
.wrap {{
  max-width: 420px;
  margin: 0 auto;
  position: relative;
  z-index: 1;
}}
.topbar {{
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 1.1rem;
}}
.brand {{
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 0;
}}
.brand-icon {{
  flex-shrink: 0;
  width: 2.75rem;
  height: 2.75rem;
  border-radius: 15px;
  display: grid;
  place-items: center;
  background: linear-gradient(145deg, #2dd4bf 0%, #0d9488 55%, #0f766e 100%);
  color: #fff;
  box-shadow:
    0 12px 28px rgba(13, 148, 136, 0.32),
    inset 0 1px 0 rgba(255,255,255,0.25);
}}
.brand-icon svg {{ width: 1.35rem; height: 1.35rem; }}
.brand-text {{
  display: flex;
  flex-direction: column;
  gap: 0.1rem;
  min-width: 0;
}}
.brand-title {{
  font-size: 1.05rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  line-height: 1.15;
}}
.brand-sub {{
  font-size: 0.76rem;
  color: var(--muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
.topbar-meta {{
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 0.4rem;
  flex-shrink: 0;
}}
.mode-pill {{
  font-size: 0.68rem;
  font-weight: 800;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  padding: 0.36rem 0.68rem;
  border-radius: 999px;
  border: 1px solid var(--line);
  background: var(--card);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  box-shadow: var(--shadow-sm);
}}
.mode-live {{
  color: #0369a1;
  background: rgba(224, 242, 254, 0.9);
  border-color: rgba(14, 165, 233, 0.22);
}}
.mode-cached {{ color: var(--muted); }}
@media (prefers-color-scheme: dark) {{
  .mode-live {{ background: rgba(12, 74, 110, 0.65); color: #7dd3fc; }}
}}
.updated-pill {{
  font-size: 0.68rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.card {{
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: var(--radius);
  padding: 1.2rem 1.15rem;
  margin-bottom: 0.9rem;
  box-shadow: var(--shadow);
  backdrop-filter: blur(18px) saturate(1.2);
  -webkit-backdrop-filter: blur(18px) saturate(1.2);
}}
.hero {{
  text-align: center;
  padding: 1.5rem 1rem 1.25rem;
  overflow: hidden;
  position: relative;
}}
.hero::before {{
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(circle at 50% 0%, var(--hero-tint, transparent), transparent 68%);
  pointer-events: none;
}}
.hero-allow {{ --hero-tint: rgba(16, 185, 129, 0.12); }}
.hero-block {{ --hero-tint: rgba(239, 68, 68, 0.1); }}
.hero-unknown {{ --hero-tint: rgba(148, 163, 184, 0.1); }}
.status-stage {{
  position: relative;
  width: 9.5rem;
  height: 9.5rem;
  margin: 0 auto 1rem;
}}
.status-glow {{
  position: absolute;
  inset: -0.35rem;
  border-radius: 50%;
  background: var(--glow-color, rgba(148,163,184,0.2));
  filter: blur(14px);
  opacity: 0.85;
}}
@media (prefers-reduced-motion: no-preference) {{
  .status-glow {{
    animation: breathe 3.2s ease-in-out infinite;
  }}
}}
@keyframes breathe {{
  0%, 100% {{ transform: scale(0.96); opacity: 0.7; }}
  50% {{ transform: scale(1.04); opacity: 1; }}
}}
.status-ring-allow {{ --glow-color: var(--allow-glow); }}
.status-ring-block {{ --glow-color: var(--block-glow); }}
.status-ring {{
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background:
    radial-gradient(circle at 28% 22%, rgba(255,255,255,0.5), transparent 52%),
    var(--ring-bg, linear-gradient(145deg, #cbd5e1, #94a3b8));
  box-shadow:
    0 0 0 1px rgba(255,255,255,0.25) inset,
    0 14px 34px rgba(15, 23, 42, 0.14);
}}
.status-ring-allow {{
  --ring-bg: linear-gradient(145deg, #34d399 0%, #059669 100%);
}}
.status-ring-block {{
  --ring-bg: linear-gradient(145deg, #fb7185 0%, #dc2626 100%);
}}
.status-ring-unknown {{
  --ring-bg: linear-gradient(145deg, #e2e8f0, #94a3b8);
}}
.status-word {{
  font-size: 1.55rem;
  font-weight: 900;
  letter-spacing: 0.1em;
  color: #fff;
  text-shadow: 0 2px 8px rgba(0,0,0,0.22);
}}
.relay-badge {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  max-width: 20rem;
  margin: 0;
  padding: 0.55rem 0.9rem;
  border-radius: 999px;
  background: var(--card-solid);
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.84rem;
  font-weight: 600;
  line-height: 1.35;
  box-shadow: var(--shadow-sm);
}}
.metrics {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.6rem;
  margin-bottom: 0.9rem;
}}
.metric {{
  background: var(--card);
  border: 1px solid var(--card-border);
  border-radius: var(--radius-sm);
  padding: 0.85rem 0.5rem 0.75rem;
  text-align: center;
  box-shadow: var(--shadow-sm);
  backdrop-filter: blur(14px);
  -webkit-backdrop-filter: blur(14px);
}}
.metric-icon {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.85rem;
  height: 1.85rem;
  border-radius: 10px;
  margin-bottom: 0.45rem;
  background: rgba(148, 163, 184, 0.12);
  color: var(--muted);
}}
.metric-icon svg {{ width: 1rem; height: 1rem; }}
.metric-rain .metric-icon {{ background: rgba(2, 132, 199, 0.12); color: #0284c7; }}
.metric-irrigation .metric-icon {{ background: rgba(13, 148, 136, 0.12); color: #0d9488; }}
.metric-deficit .metric-icon {{ background: rgba(234, 88, 12, 0.12); color: #ea580c; }}
.metric-label {{
  display: block;
  font-size: 0.62rem;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.28rem;
}}
.metric-value {{
  display: block;
  font-size: 1.02rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}}
.metric-rain .metric-value {{ color: #0284c7; }}
.metric-irrigation .metric-value {{ color: #0d9488; }}
.metric-deficit .metric-value {{ color: #ea580c; }}
.section-head {{
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}}
.section-head h2 {{
  margin: 0;
  font-size: 0.92rem;
  font-weight: 800;
  letter-spacing: -0.02em;
}}
.section-head span {{
  font-size: 0.72rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  text-align: right;
}}
.detail-list {{
  margin: 0;
  padding: 0;
  list-style: none;
}}
.detail-row {{
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
  padding: 0.58rem 0;
  border-bottom: 1px solid var(--line);
}}
.detail-row:last-child {{ border-bottom: none; }}
.detail-row-highlight {{
  margin: 0.15rem -0.55rem 0;
  padding: 0.65rem 0.55rem;
  border-bottom: none;
  border-radius: 12px;
  background: rgba(148, 163, 184, 0.08);
}}
.detail-label {{
  color: var(--muted);
  font-size: 0.86rem;
}}
.detail-value {{
  font-size: 0.86rem;
  font-weight: 700;
  text-align: right;
  font-variant-numeric: tabular-nums;
}}
.chip {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 800;
  letter-spacing: 0.04em;
  border-radius: 999px;
  font-size: 0.64rem;
  padding: 0.2rem 0.52rem;
}}
.chip-allow {{ background: var(--allow-soft); color: var(--allow); }}
.chip-block {{ background: var(--block-soft); color: var(--block); }}
.chip-unknown {{ background: var(--unknown-soft); color: var(--muted); }}
.history-list {{
  list-style: none;
  padding: 0;
  margin: 0;
}}
.history-item {{
  display: grid;
  grid-template-columns: 1.1rem 1fr;
  gap: 0.7rem;
  padding: 0.75rem 0;
}}
.history-item + .history-item {{
  border-top: 1px solid var(--line);
}}
.history-track {{
  position: relative;
  display: flex;
  justify-content: center;
}}
.history-track::before {{
  content: "";
  position: absolute;
  top: 1.1rem;
  bottom: -0.75rem;
  width: 2px;
  background: linear-gradient(180deg, var(--line), transparent);
}}
.history-item:last-child .history-track::before {{ display: none; }}
.history-dot {{
  width: 0.72rem;
  height: 0.72rem;
  border-radius: 50%;
  margin-top: 0.38rem;
  background: #94a3b8;
  box-shadow: 0 0 0 4px rgba(148, 163, 184, 0.14);
  position: relative;
  z-index: 1;
}}
.dot-allow {{
  background: #10b981;
  box-shadow: 0 0 0 4px var(--allow-glow);
}}
.dot-block {{
  background: #ef4444;
  box-shadow: 0 0 0 4px var(--block-glow);
}}
.history-top {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem 0.5rem;
}}
.history-time {{
  font-size: 0.76rem;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.history-credit {{
  margin-left: auto;
  font-size: 0.8rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  color: var(--text);
}}
.history-details {{
  margin: 0.32rem 0 0;
  font-size: 0.78rem;
  color: var(--muted);
  line-height: 1.45;
}}
.alert {{
  margin-top: 0.85rem;
  padding: 0.75rem 0.85rem;
  border-radius: 14px;
  font-size: 0.84rem;
  line-height: 1.45;
  border: 1px solid transparent;
}}
.alert strong {{
  display: block;
  margin-bottom: 0.15rem;
  font-size: 0.68rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
}}
.alert-error {{
  background: var(--block-soft);
  color: var(--block);
  border-color: rgba(239, 68, 68, 0.18);
}}
.alert-warn {{
  background: rgba(251, 191, 36, 0.14);
  color: #92400e;
  border-color: rgba(251, 191, 36, 0.22);
}}
@media (prefers-color-scheme: dark) {{
  .alert-warn {{ background: rgba(251, 191, 36, 0.12); color: #fcd34d; }}
}}
.empty-state {{
  margin: 0;
  padding: 1.25rem 0 0.35rem;
  text-align: center;
  color: var(--muted);
  font-size: 0.88rem;
}}
footer {{
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 10;
  padding:
    0.65rem max(1rem, env(safe-area-inset-right))
    max(0.85rem, env(safe-area-inset-bottom))
    max(1rem, env(safe-area-inset-left));
  background: linear-gradient(180deg, transparent, rgba(248, 250, 252, 0.82) 28%);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
}}
@media (prefers-color-scheme: dark) {{
  footer {{
    background: linear-gradient(180deg, transparent, rgba(15, 23, 42, 0.88) 28%);
  }}
}}
.footer-inner {{
  max-width: 420px;
  margin: 0 auto;
}}
.footer-btn {{
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 0.55rem;
  width: 100%;
  padding: 0.95rem 1rem;
  border-radius: 16px;
  border: none;
  background: linear-gradient(180deg, #2dd4bf 0%, #0d9488 52%, #0f766e 100%);
  color: #fff;
  font-size: 0.94rem;
  font-weight: 800;
  letter-spacing: -0.01em;
  text-decoration: none;
  box-shadow:
    0 14px 32px rgba(13, 148, 136, 0.34),
    inset 0 1px 0 rgba(255,255,255,0.22);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
}}
.footer-btn:active {{
  transform: scale(0.985);
  box-shadow: 0 8px 20px rgba(13, 148, 136, 0.28);
}}
.footer-btn span {{
  font-size: 1.08rem;
  line-height: 1;
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="topbar">
    <div class="brand">
      <div class="brand-icon">{brand_icon}</div>
      <div class="brand-text">
        <span class="brand-title">Rain Bypass</span>
        <span class="brand-sub">{esc(view.location)}</span>
      </div>
    </div>
    <div class="topbar-meta">
      <span class="mode-pill {mode_class}">{esc(mode_label)}</span>
      <span class="updated-pill">{esc(updated_meta)}</span>
    </div>
  </header>

  <section class="card hero hero-{esc(view.verdict_class)}">
    <div class="status-stage">
      <div class="status-glow status-ring-{esc(view.verdict_class)}" aria-hidden="true"></div>
      <div class="status-ring status-ring-{esc(view.verdict_class)}" aria-hidden="true">
        <span class="status-word">{esc(view.verdict_label)}</span>
      </div>
    </div>
    <p class="relay-badge">{esc(view.relay_text)}</p>
  </section>

  <div class="metrics">
    {_metric_tile("Rain MTD", view.rain_mtd, esc, accent="rain", icon=rain_icon)}
    {
        _metric_tile(
            "Irrigation", view.irrigation_mtd, esc, accent="irrigation", icon=irrigation_icon
        )
    }
    {_metric_tile("Deficit", view.deficit, esc, accent="deficit", icon=deficit_icon)}
  </div>

  <section class="card">
    <div class="section-head">
      <h2>Status</h2>
      <span>{esc(view.local_time)}</span>
    </div>
    <ul class="detail-list">
      {_detail_row("Next check", view.next_check, esc)}
      {_detail_row("Forecast", view.forecast, esc)}
      {_detail_row("Would decide", view.would_decide, esc, highlight=True)}
    </ul>
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
    <a class="footer-btn" href="{footer_href}"><span>{footer_icon}</span>{esc(footer_label)}</a>
  </div>
</footer>
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
