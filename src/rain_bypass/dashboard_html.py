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


_dashboard_css_cache: str | None = None


def _load_dashboard_css() -> str:
    global _dashboard_css_cache
    if _dashboard_css_cache is None:
        _dashboard_css_cache = (
            files("rain_bypass").joinpath("static/dashboard.css").read_text(encoding="utf-8")
        )
    return _dashboard_css_cache


def _history_item_html(row: DashboardHistoryRow, esc: Callable[[str], str]) -> str:
    return (
        f'<li class="history-item">'
        f'<span class="history-dot dot-{esc(row.verdict_class)}" aria-hidden="true"></span>'
        f'<div class="history-main">'
        f'<time class="history-time">{esc(row.timestamp)}</time>'
        f'<details class="history-more">'
        f'<summary aria-label="Details for {esc(row.timestamp)}">Why</summary>'
        f'<p class="history-details">{esc(row.details)}</p>'
        f"</details>"
        f"</div>"
        f'<span class="chip chip-{esc(row.verdict_class)}">{esc(row.verdict)}</span>'
        f'<span class="history-credit">{esc(row.credit)}</span>'
        f"</li>"
    )


def _balance_card_html(balance: DashboardBalance, esc: Callable[[str], str]) -> str:
    pace = {
        "need": "Behind pace",
        "surplus": "Ahead of pace",
        "even": "On pace",
    }[balance.deficit_class]
    gap_label = {
        "need": "Still need",
        "surplus": "Ahead by",
        "even": "Gap",
    }[balance.deficit_class]
    return f"""
  <section class="card balance-card">
    <div class="section-head">
      <h2>Water budget</h2>
      <span class="balance-pace balance-pace-{esc(balance.deficit_class)}">{esc(pace)}</span>
    </div>
    <p class="balance-headline">
      <span class="balance-received">{esc(balance.received)}</span>
      <span class="balance-of">of</span>
      <span class="balance-target">{esc(balance.target)}</span>
    </p>
    <div class="balance-track" role="progressbar"
         aria-valuenow="{balance.progress_pct}" aria-valuemin="0" aria-valuemax="100"
         aria-label="Water received toward monthly target pace">
      <div class="balance-fill balance-{esc(balance.deficit_class)}"
           style="width:{balance.progress_pct}%"></div>
    </div>
    <p class="balance-split">
      Rain {esc(balance.rain)}
      <span aria-hidden="true">·</span>
      Irrigation {esc(balance.irrigation)}
    </p>
    <div class="balance-foot">
      <div class="balance-foot-row">
        <span class="balance-foot-label">{esc(balance.forecast_label)}</span>
        <span class="balance-foot-value">{esc(balance.forecast)}</span>
      </div>
      <div class="balance-foot-row balance-gap balance-gap-{esc(balance.deficit_class)}">
        <span class="balance-foot-label">{esc(gap_label)}</span>
        <span class="balance-foot-value">{esc(balance.deficit_amount)}</span>
      </div>
    </div>
  </section>"""


def render_dashboard_html(view: DashboardView) -> str:
    esc = html.escape
    mode_class = "mode-live" if view.live_mode else "mode-cached"
    refresh_attr = ""
    if view.refresh_seconds is not None:
        refresh_attr = f' data-refresh="{view.refresh_seconds}"'

    if view.history_rows:
        items = "".join(_history_item_html(row, esc) for row in view.history_rows)
        history_html = f'<ul class="history-list">{items}</ul>'
    else:
        history_html = '<p class="empty-state">No watering history yet.</p>'

    alert_parts: list[str] = []
    if view.last_error:
        alert_parts.append(
            f'<div class="alert alert-error" role="alert">'
            f"<strong>Last error</strong> {esc(view.last_error)}"
            f"</div>"
        )
    if view.stale_check:
        alert_parts.append(
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Missed check</strong> {esc(view.stale_check)}"
            f"</div>"
        )
    if view.live_note:
        alert_parts.append(
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Live weather</strong> {esc(view.live_note)}"
            f"</div>"
        )
    if view.sewer_lockout:
        alert_parts.append(
            f'<div class="alert alert-warn" role="alert">'
            f"<strong>Sewer lockout</strong> {esc(view.sewer_lockout)}"
            f"</div>"
        )
    alerts_html = f'<div class="alert-stack">{"".join(alert_parts)}</div>' if alert_parts else ""

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
<title>Sprinkler Rain Bypass</title>
<style>
{dashboard_css}
</style>
</head>
<body{refresh_attr}>
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

  <section class="hero hero-{esc(view.verdict_class)}" aria-labelledby="verdict-label">
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

  {alerts_html}

  <div class="meta-strip" aria-label="Schedule">
    <div class="meta-strip-item">
      <span class="meta-strip-label">Next check</span>
      <span class="meta-strip-value">{esc(view.next_check)}</span>
    </div>
    <div class="meta-strip-item meta-strip-item-end">
      <span class="meta-strip-label">Local</span>
      <span class="meta-strip-value meta-strip-time">{esc(view.local_time)}</span>
    </div>
  </div>

  {balance_html}

  <section class="card history-card">
    <div class="section-head">
      <h2>Recent history</h2>
      <span>{len(view.history_rows)} days</span>
    </div>
    {history_html}
  </section>
</div>

<div class="ux-toast" id="ux-toast" role="status" aria-live="polite" hidden></div>

<footer>
  <div class="footer-inner">
    <a class="footer-btn" href="{footer_href}" data-loading="{footer_loading}">
      <span>{footer_icon}</span>{esc(footer_label)}
    </a>
  </div>
</footer>
<script>
(function () {{
  var toast = document.getElementById('ux-toast');
  var toastTimer = null;

  function showToast(message, isError) {{
    if (!toast) return;
    toast.textContent = message;
    toast.hidden = false;
    toast.classList.toggle('is-error', !!isError);
    toast.classList.add('is-visible');
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {{
      toast.classList.remove('is-visible');
    }}, 4200);
  }}

  function softNavigate(url, loadingLabel) {{
    var btn = document.querySelector('.footer-btn');
    if (btn) {{
      btn.classList.add('is-loading');
      btn.setAttribute('aria-busy', 'true');
      if (loadingLabel) btn.innerHTML = '<span>↻</span>' + loadingLabel;
    }}
    return fetch(url, {{ credentials: 'same-origin', redirect: 'follow' }})
      .then(function (response) {{
        if (!response.ok) throw new Error('HTTP ' + response.status);
        return response.text();
      }})
      .then(function (html) {{
        document.open();
        document.write(html);
        document.close();
      }})
      .catch(function () {{
        if (btn) {{
          btn.classList.remove('is-loading');
          btn.removeAttribute('aria-busy');
        }}
        showToast('Could not refresh. Check Wi-Fi and try again.', true);
      }});
  }}

  var btn = document.querySelector('.footer-btn');
  if (btn) {{
    btn.addEventListener('click', function (event) {{
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      var href = btn.getAttribute('href') || '/';
      var live = btn.getAttribute('data-loading') === 'true';
      softNavigate(href, live ? 'Fetching weather…' : 'Updating…');
    }});
  }}

  var refreshSeconds = parseInt(document.body.getAttribute('data-refresh') || '0', 10);
  if (refreshSeconds > 0) {{
    setInterval(function () {{
      if (document.hidden) return;
      softNavigate(location.pathname || '/', null);
    }}, refreshSeconds * 1000);
  }}
}})();
</script>
</body>
</html>"""
