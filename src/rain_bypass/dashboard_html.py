from __future__ import annotations

import html
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files


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
    stale_check: str | None
    live_note: str | None
    sewer_lockout: str | None
    balance: DashboardBalance | None
    history_rows: tuple[DashboardHistoryRow, ...]
    refresh_seconds: int | None


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

_dashboard_css_cache: str | None = None


def _load_dashboard_css() -> str:
    global _dashboard_css_cache
    if _dashboard_css_cache is None:
        _dashboard_css_cache = (
            files("rain_bypass").joinpath("static/dashboard.css").read_text(encoding="utf-8")
        )
    return _dashboard_css_cache


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

    stale_html = ""
    if view.stale_check:
        stale_html = (
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Missed check</strong> {esc(view.stale_check)}"
            f"</div>"
        )

    live_note_html = ""
    if view.live_note:
        live_note_html = (
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Live weather</strong> {esc(view.live_note)}"
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
    {stale_html}
    {live_note_html}
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
