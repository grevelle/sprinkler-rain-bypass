from __future__ import annotations

import calendar
import html
from collections.abc import Callable
from dataclasses import dataclass

from rain_bypass.config import FailMode, Settings
from rain_bypass.dashboard_html import favicon_link_tags, load_dashboard_css

_MONTH_LABELS = tuple(calendar.month_abbr[m] for m in range(1, 13))
_MONTH_NAMES = tuple(calendar.month_name[m] for m in range(1, 13))


@dataclass(frozen=True, slots=True)
class ConfigFormValues:
    zip_code: str
    inches_per_cycle: str
    check_time: str
    forecast_days: str
    freeze_temp_f: str
    event_lookback_days: str
    event_inches: str
    sewer_start_month: str
    sewer_start_day: str
    sewer_end_month: str
    sewer_end_day: str
    fail_mode: str
    monthly: tuple[str, ...]


def config_values_from_settings(settings: Settings) -> ConfigFormValues:
    watering = settings.watering
    sewer = settings.sewer
    monthly = tuple(
        f"{settings.balance.monthly[month].target_inches_per_month:g}" for month in range(1, 13)
    )
    return ConfigFormValues(
        zip_code=settings.location.zip_code,
        inches_per_cycle=f"{settings.balance.inches_per_cycle:g}",
        check_time=f"{watering.check_hour:02d}:{watering.check_minute:02d}",
        forecast_days=str(settings.balance.forecast_days),
        freeze_temp_f=f"{watering.freeze_temp_f:g}",
        event_lookback_days=str(watering.event_lookback_days),
        event_inches=f"{watering.event_inches:g}",
        sewer_start_month=str(sewer.start_month),
        sewer_start_day=str(sewer.start_day),
        sewer_end_month=str(sewer.end_month),
        sewer_end_day=str(sewer.end_day),
        fail_mode=settings.runtime.fail_mode.value,
        monthly=monthly,
    )


def config_values_from_form(form: dict[str, str]) -> ConfigFormValues:
    monthly = tuple(form.get(f"monthly_{month}", "").strip() for month in range(1, 13))
    return ConfigFormValues(
        zip_code=form.get("zip_code", "").strip(),
        inches_per_cycle=form.get("inches_per_cycle", "").strip(),
        check_time=form.get("check_time", "").strip(),
        forecast_days=form.get("forecast_days", "").strip(),
        freeze_temp_f=form.get("freeze_temp_f", "").strip(),
        event_lookback_days=form.get("event_lookback_days", "").strip(),
        event_inches=form.get("event_inches", "").strip(),
        sewer_start_month=form.get("sewer_start_month", "").strip(),
        sewer_start_day=form.get("sewer_start_day", "").strip(),
        sewer_end_month=form.get("sewer_end_month", "").strip(),
        sewer_end_day=form.get("sewer_end_day", "").strip(),
        fail_mode=form.get("fail_mode", "").strip(),
        monthly=monthly,
    )


def _field(
    *,
    name: str,
    label: str,
    value: str,
    esc: Callable[[str], str],
    field_type: str = "text",
    hint: str | None = None,
    attrs: str = "",
    extra_class: str = "",
    autocomplete: str | None = None,
) -> str:
    field_id = f"field-{name}"
    hint_id = f"hint-{name}"
    described_by = f' aria-describedby="{hint_id}"' if hint else ""
    auto = f' autocomplete="{esc(autocomplete)}"' if autocomplete else ""
    hint_html = f'<p class="field-hint" id="{hint_id}">{esc(hint)}</p>' if hint else ""
    field_class = f"field {extra_class}".strip()
    return (
        f'<div class="{field_class}">'
        f'<label class="field-label" for="{field_id}">{esc(label)}</label>'
        f'<input class="field-input" id="{field_id}" type="{esc(field_type)}" '
        f'name="{esc(name)}" value="{esc(value)}"{auto}{described_by} {attrs}>'
        f"{hint_html}"
        f"</div>"
    )


def _month_select(
    *,
    name: str,
    label: str,
    selected: str,
    esc: Callable[[str], str],
) -> str:
    field_id = f"field-{name}"
    options: list[str] = []
    for month in range(1, 13):
        sel = " selected" if str(month) == selected else ""
        options.append(f'<option value="{month}"{sel}>{esc(_MONTH_NAMES[month - 1])}</option>')
    return (
        f'<div class="field">'
        f'<label class="field-label" for="{field_id}">{esc(label)}</label>'
        f'<select class="field-input" id="{field_id}" name="{esc(name)}" required>'
        f"{''.join(options)}"
        f"</select>"
        f"</div>"
    )


def _select_fail_mode(selected: str, esc: Callable[[str], str]) -> str:
    field_id = "field-fail_mode"
    hint_id = "hint-fail_mode"
    options: list[str] = []
    labels = {
        FailMode.DISABLE_WATERING: "Block watering on weather failure",
        FailMode.KEEP_LAST_STATE: "Keep last relay state on weather failure",
    }
    for mode in FailMode:
        sel = " selected" if mode.value == selected else ""
        options.append(f'<option value="{esc(mode.value)}"{sel}>{esc(labels[mode])}</option>')
    return (
        f'<div class="field">'
        f'<label class="field-label" for="{field_id}">Fail mode</label>'
        f'<select class="field-input" id="{field_id}" name="fail_mode" '
        f'required aria-describedby="{hint_id}">'
        f"{''.join(options)}"
        f"</select>"
        f'<p class="field-hint" id="{hint_id}">'
        "What to do if the weather API fails at check time."
        "</p>"
        f"</div>"
    )


def _monthly_grid(values: ConfigFormValues, esc: Callable[[str], str]) -> str:
    cells: list[str] = []
    for month in range(1, 13):
        value = values.monthly[month - 1] if month - 1 < len(values.monthly) else ""
        cells.append(
            _field(
                name=f"monthly_{month}",
                label=_MONTH_LABELS[month - 1],
                value=value,
                esc=esc,
                field_type="number",
                attrs=(
                    f'step="0.1" min="0" required inputmode="decimal" '
                    f'aria-label="{esc(_MONTH_NAMES[month - 1])} target inches"'
                ),
                extra_class="field-month",
            )
        )
    return f'<div class="month-grid">{"".join(cells)}</div>'


def _fieldset(title: str, body: str, *, lead: str | None = None) -> str:
    lead_html = f'<p class="section-lead">{lead}</p>' if lead else ""
    return (
        f'<fieldset class="card config-section">'
        f"<legend>{title}</legend>"
        f"{lead_html}"
        f"{body}"
        f"</fieldset>"
    )


def render_config_html(
    values: ConfigFormValues,
    *,
    error: str | None = None,
    saved: bool = False,
) -> str:
    esc = html.escape
    if error:
        alert = (
            '<div class="alert alert-error" id="form-status" role="alert" '
            'tabindex="-1">'
            f"<strong>Could not save</strong> {esc(error)}"
            "</div>"
        )
    elif saved:
        alert = (
            '<div class="alert alert-ok" id="form-status" role="status" '
            'tabindex="-1">'
            "<strong>Saved</strong> "
            "Written to settings.toml. New check time applies after restarting "
            "<code>rain-bypass</code>; other fields apply on the next control cycle."
            "</div>"
        )
    else:
        alert = ""

    brand_icon = (
        '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true">'
        '<path d="M12 2.5c0 0-6 6.2-6 10.8a6 6 0 0 0 12 0C18 8.7 12 2.5 12 2.5z"'
        ' fill="currentColor" opacity="0.95"/>'
        '<path d="M12 6.5c0 0-3 3.4-3 5.8a3 3 0 0 0 6 0c0-2.4-3-5.8-3-5.8z"'
        ' fill="#fff" opacity="0.35"/>'
        "</svg>"
    )

    location_body = _field(
        name="zip_code",
        label="ZIP code",
        value=values.zip_code,
        esc=esc,
        autocomplete="postal-code",
        attrs=('inputmode="numeric" required spellcheck="false" pattern="\\d{5}(?:-\\d{4})?"'),
    ) + _field(
        name="api_key",
        label="Visual Crossing API key",
        value="",
        esc=esc,
        field_type="password",
        hint="Leave blank to keep the current key.",
        autocomplete="off",
        attrs='placeholder="Unchanged" spellcheck="false"',
    )
    watering_body = (
        _field(
            name="inches_per_cycle",
            label="Inches per cycle",
            value=values.inches_per_cycle,
            esc=esc,
            field_type="number",
            attrs='step="0.01" min="0.01" required inputmode="decimal"',
            hint="Catch-cup depth for one daily program run.",
        )
        + _field(
            name="check_time",
            label="Daily check time",
            value=values.check_time,
            esc=esc,
            field_type="time",
            attrs='required step="60"',
            hint="Local time before irrigation. Restart rain-bypass after changing.",
        )
        + '<div class="field-row">'
        + _field(
            name="forecast_days",
            label="Forecast days",
            value=values.forecast_days,
            esc=esc,
            field_type="number",
            attrs='min="0" step="1" required inputmode="numeric"',
        )
        + _field(
            name="event_lookback_days",
            label="Lookback days",
            value=values.event_lookback_days,
            esc=esc,
            field_type="number",
            attrs='min="1" step="1" required inputmode="numeric"',
        )
        + "</div>"
        + _field(
            name="event_inches",
            label="Event rain (in)",
            value=values.event_inches,
            esc=esc,
            field_type="number",
            attrs='step="0.01" min="0" required inputmode="decimal"',
            hint="Skip watering at this lookback total.",
        )
        + _field(
            name="freeze_temp_f",
            label="Freeze °F",
            value=values.freeze_temp_f,
            esc=esc,
            field_type="number",
            attrs='step="0.1" required inputmode="decimal"',
        )
    )
    sewer_body = (
        '<div class="field-row">'
        + _month_select(
            name="sewer_start_month",
            label="Start month",
            selected=values.sewer_start_month,
            esc=esc,
        )
        + _field(
            name="sewer_start_day",
            label="Start day",
            value=values.sewer_start_day,
            esc=esc,
            field_type="number",
            attrs='min="1" max="31" step="1" required inputmode="numeric"',
        )
        + "</div>"
        + '<div class="field-row">'
        + _month_select(
            name="sewer_end_month",
            label="End month",
            selected=values.sewer_end_month,
            esc=esc,
        )
        + _field(
            name="sewer_end_day",
            label="End day",
            value=values.sewer_end_day,
            esc=esc,
            field_type="number",
            attrs='min="1" max="31" step="1" required inputmode="numeric"',
        )
        + "</div>"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0d9488">
<meta name="apple-mobile-web-app-capable" content="yes">
<title>Config — Rain Bypass</title>
{favicon_link_tags()}
<style>
{load_dashboard_css()}
</style>
</head>
<body class="page-config">
<div class="wrap">
  <header class="topbar">
    <div class="brand">
      <div class="brand-icon">{brand_icon}</div>
      <div class="brand-text">
        <span class="brand-title">Rain Bypass</span>
        <span class="brand-sub">Configuration</span>
      </div>
    </div>
  </header>

  {alert}

  <form id="config-form" class="config-form" method="post" action="/config">
    {
        _fieldset(
            "Location &amp; weather",
            location_body,
        )
    }
    {_fieldset("Watering", watering_body)}
    {
        _fieldset(
            "Monthly water targets",
            _monthly_grid(values, esc),
            lead="Inches per calendar month (Kentucky bluegrass pace). "
            "Used for the water-budget balance.",
        )
    }
    {
        _fieldset(
            "Sewer lockout",
            sewer_body,
            lead="Hard block window for winter sewer billing. "
            "Inclusive start and end dates (Feb 29 allowed).",
        )
    }
    {_fieldset("Safety &amp; failures", _select_fail_mode(values.fail_mode, esc))}
    <p class="config-footnote">GPIO pins, web bind address, and log level stay in
    settings.toml.</p>
  </form>
</div>

<footer>
  <div class="footer-inner">
    <nav class="site-nav" aria-label="Pages">
      <a class="site-nav-link" href="/">Status</a>
      <a class="site-nav-link is-active" href="/config" aria-current="page">Config</a>
    </nav>
    <button class="footer-btn" type="submit" form="config-form" id="config-save">
      Save settings
    </button>
  </div>
</footer>
<script>
(function () {{
  var form = document.getElementById('config-form');
  var btn = document.getElementById('config-save');
  var status = document.getElementById('form-status');
  if (status && status.focus) {{
    try {{ status.focus({{ preventScroll: false }}); }} catch (e) {{ status.focus(); }}
  }}
  if (form && btn) {{
    form.addEventListener('submit', function () {{
      btn.disabled = true;
      btn.setAttribute('aria-busy', 'true');
      btn.textContent = 'Saving…';
    }});
  }}
}})();
</script>
</body>
</html>"""
