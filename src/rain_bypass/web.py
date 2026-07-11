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
from rain_bypass.models import Evaluation
from rain_bypass.status import (
    format_duration,
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
    cycle_threshold: str


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


def _history_details(record: WateringRecord) -> str:
    if record.sewer_lockout:
        return "Sewer lockout — seasonal hold"
    if record.weather_error:
        return "Blocked — weather check failed"
    if record.allowed:
        return f"Irrigation credited {record.inches_credited:.2f} in"
    if record.deficit is not None and record.deficit > 0:
        return f"{record.deficit:.2f} in still needed for balance"
    return "Blocked — balance satisfied or safety hold"


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
            f"{deficit:.2f} in gap before balance allows a cycle (need ≥ {inches_per_cycle:.2f} in)"
        )
    elif deficit < 0:
        deficit_class = "surplus"
        deficit_note = f"{abs(deficit):.2f} in over the monthly target pace"
    else:
        deficit_class = "even"
        deficit_note = "On target — no watering needed for balance"
    return DashboardBalance(
        target=_format_inches(target),
        rain=_format_inches(rain),
        irrigation=_format_inches(irrigation_mtd),
        received=_format_inches(received),
        forecast=_format_inches(forecast),
        forecast_label=f"Forecast credit ({forecast_days} days)",
        deficit_amount=_format_inches(abs(deficit) if deficit != 0 else 0.0),
        deficit_note=deficit_note,
        deficit_class=deficit_class,
        progress_pct=progress_pct,
        cycle_threshold=_format_inches(inches_per_cycle),
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
        sewer_note = (
            f"Sewer lockout active "
            f"({sewer.start_month:02d}/{sewer.start_day:02d}"
            f"-{sewer.end_month:02d}/{sewer.end_day:02d})"
        )
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
  text-align: right;
  max-width: 11rem;
  line-height: 1.35;
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
.hero-subtitle {{
  margin: 0 0 0.65rem;
  font-size: 0.92rem;
  font-weight: 600;
  line-height: 1.45;
  max-width: 21rem;
  margin-inline: auto;
  color: var(--text);
}}
.relay-short {{
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin: 0;
  padding: 0.45rem 0.75rem;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.1);
  border: 1px solid var(--line);
  color: var(--muted);
  font-size: 0.76rem;
  font-weight: 600;
  line-height: 1.35;
}}
.balance-card {{ padding-bottom: 1rem; }}
.balance-track {{
  height: 0.72rem;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.18);
  overflow: hidden;
  margin-bottom: 0.9rem;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.08);
}}
.balance-fill {{
  height: 100%;
  border-radius: inherit;
  min-width: 0.35rem;
  transition: width 0.35s ease;
}}
.balance-need {{ background: linear-gradient(90deg, #38bdf8, #0d9488); }}
.balance-surplus {{ background: linear-gradient(90deg, #34d399, #059669); }}
.balance-even {{ background: linear-gradient(90deg, #2dd4bf, #0f766e); }}
.balance-stats {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.55rem;
  margin-bottom: 0.85rem;
}}
.balance-stat {{
  padding: 0.65rem 0.45rem;
  border-radius: 12px;
  background: rgba(148, 163, 184, 0.08);
  text-align: center;
}}
.balance-stat-total {{
  background: rgba(13, 148, 136, 0.1);
  border: 1px solid rgba(13, 148, 136, 0.16);
}}
.balance-stat-icon {{
  display: block;
  width: 1.05rem;
  height: 1.05rem;
  margin: 0 auto 0.35rem;
  color: var(--muted);
}}
.balance-stat-total .balance-stat-icon {{
  color: var(--accent);
}}
.balance-stat-label {{
  display: block;
  font-size: 0.62rem;
  font-weight: 800;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 0.2rem;
}}
.balance-stat-value {{
  display: block;
  font-size: 0.92rem;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
}}
.balance-foot {{
  padding-top: 0.15rem;
  border-top: 1px solid var(--line);
}}
.balance-foot-row {{
  display: flex;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.45rem 0;
  font-size: 0.84rem;
}}
.balance-foot-label {{ color: var(--muted); }}
.balance-foot-value {{ font-weight: 700; font-variant-numeric: tabular-nums; }}
.balance-gap-need .balance-foot-value {{ color: #ea580c; }}
.balance-gap-surplus .balance-foot-value {{ color: #059669; }}
.balance-gap-even .balance-foot-value {{ color: #0d9488; }}
.balance-note {{
  margin: 0.35rem 0 0;
  font-size: 0.8rem;
  line-height: 1.45;
  color: var(--muted);
}}
.balance-unavailable .empty-state {{ padding-top: 0.35rem; }}
.schedule-card {{ padding-bottom: 1rem; }}
.next-check-value {{
  margin: 0;
  font-size: 1.65rem;
  font-weight: 800;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
}}
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
.footer-btn.is-loading {{
  opacity: 0.92;
  pointer-events: none;
}}
@keyframes spin {{
  to {{ transform: rotate(360deg); }}
}}
.footer-btn.is-loading span {{
  display: inline-block;
  animation: spin 0.8s linear infinite;
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
