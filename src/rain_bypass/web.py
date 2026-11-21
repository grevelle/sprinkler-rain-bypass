from __future__ import annotations

import calendar
import logging
import time
from collections.abc import Mapping
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import typer
from pydantic import ValidationError

from rain_bypass.balance import balance_display
from rain_bypass.config import (
    BalanceMonth,
    ConfigError,
    FailMode,
    Settings,
    SewerLockout,
    State,
    Watering,
    format_sewer_range,
    load_settings,
    validate_model,
    write_settings,
)
from rain_bypass.config_html import (
    ConfigFormValues,
    config_values_from_form,
    config_values_from_settings,
    render_config_html,
)
from rain_bypass.dashboard_html import (
    DashboardBalance,
    DashboardHistoryRow,
    DashboardView,
    render_dashboard_html,
)
from rain_bypass.exceptions import WeatherError
from rain_bypass.history import (
    WateringRecord,
    history_path,
    load_records,
    verdict_badge,
    watering_record_details,
    watering_verdict,
)
from rain_bypass.install_prompts import (
    parse_check_time,
    parse_inches_per_cycle,
    parse_zip_code,
    validate_api_key,
)
from rain_bypass.models import Evaluation
from rain_bypass.status import (
    format_duration,
    format_inches,
    format_updated,
    gather_status,
)
from rain_bypass.weather import resolve_location
from rain_bypass.windows import missed_check_message

logger = logging.getLogger(__name__)

LIVE_MIN_INTERVAL_SECONDS = 300.0
_last_live_fetch_mono = 0.0
_pending_live_note: str | None = None


def reset_live_fetch_gate() -> None:
    """Clear the /live rate-limit gate (tests and rare admin use)."""
    global _last_live_fetch_mono, _pending_live_note
    _last_live_fetch_mono = 0.0
    _pending_live_note = None


_BIND_LOW_PORT_HINT = (
    "Binding to port {port} failed ({exc}). On Linux, grant CAP_NET_BIND_SERVICE "
    "in the systemd unit or use a higher port (e.g. rain-bypass serve --port 8080)."
)


def _prompt_error(exc: Exception) -> ConfigError:
    return ConfigError(str(exc))


def _require(data: Mapping[str, str], key: str, *, label: str) -> str:
    value = data.get(key, "").strip()
    if not value:
        raise ConfigError(f"{label} is required.")
    return value


def _parse_int(data: Mapping[str, str], key: str, *, label: str) -> int:
    raw = _require(data, key, label=label)
    if any(ch in raw for ch in ".eE"):
        raise ConfigError(f"{label} must be a whole number.")
    try:
        return int(raw, 10)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a whole number.") from exc


def _parse_float(data: Mapping[str, str], key: str, *, label: str) -> float:
    raw = _require(data, key, label=label)
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{label} must be a number.") from exc


def _validate_calendar_day(month: int, day: int, *, label: str) -> None:
    if month < 1 or month > 12:
        raise ConfigError(f"{label} month must be 1-12.")
    # Leap year so Feb 29 is allowed for recurring lockout windows.
    last_day = calendar.monthrange(2024, month)[1]
    if day < 1 or day > last_day:
        raise ConfigError(f"{label}: day {day} is not valid for month {month}.")


def apply_config_form(base: Settings, form: Mapping[str, str]) -> Settings:
    """Merge day-to-day web form fields onto existing settings."""
    data = {key: value.strip() for key, value in form.items()}

    try:
        zip5 = parse_zip_code(_require(data, "zip_code", label="ZIP code"))
        inches = parse_inches_per_cycle(
            _require(data, "inches_per_cycle", label="Inches per cycle")
        )
        check_hour, check_minute = parse_check_time(
            _require(data, "check_time", label="Daily check time")
        )
    except typer.BadParameter as exc:
        raise _prompt_error(exc) from None

    api_raw = data.get("api_key", "").strip()
    if api_raw:
        try:
            api_key = validate_api_key(api_raw)
        except typer.BadParameter as exc:
            raise _prompt_error(exc) from None
    else:
        api_key = base.weather.api_key

    forecast_days = _parse_int(data, "forecast_days", label="Forecast days")
    event_lookback_days = _parse_int(data, "event_lookback_days", label="Lookback days")
    freeze_temp_f = _parse_float(data, "freeze_temp_f", label="Freeze °F")
    event_inches = _parse_float(data, "event_inches", label="Event rain")
    sewer_start_month = _parse_int(data, "sewer_start_month", label="Sewer start month")
    sewer_start_day = _parse_int(data, "sewer_start_day", label="Sewer start day")
    sewer_end_month = _parse_int(data, "sewer_end_month", label="Sewer end month")
    sewer_end_day = _parse_int(data, "sewer_end_day", label="Sewer end day")
    _validate_calendar_day(sewer_start_month, sewer_start_day, label="Sewer start")
    _validate_calendar_day(sewer_end_month, sewer_end_day, label="Sewer end")

    monthly: dict[int, BalanceMonth] = {}
    for month in range(1, 13):
        inches_month = _parse_float(data, f"monthly_{month}", label=f"Month {month} target")
        if inches_month < 0:
            raise ConfigError(f"Month {month} target cannot be negative.")
        monthly[month] = BalanceMonth(target_inches_per_month=inches_month)

    fail_raw = _require(data, "fail_mode", label="Fail mode")
    try:
        fail_mode = FailMode(fail_raw)
    except ValueError as exc:
        raise ConfigError("Fail mode must be disable_watering or keep_last_state.") from exc

    sewer = validate_model(
        SewerLockout,
        {
            "start_month": sewer_start_month,
            "start_day": sewer_start_day,
            "end_month": sewer_end_month,
            "end_day": sewer_end_day,
        },
    )
    watering = validate_model(
        Watering,
        {
            "check_hour": check_hour,
            "check_minute": check_minute,
            "event_lookback_days": event_lookback_days,
            "event_inches": event_inches,
            "freeze_temp_f": freeze_temp_f,
        },
    )

    if zip5 != base.location.zip_code or api_key != base.weather.api_key:
        try:
            location = resolve_location(zip5, api_key)
        except WeatherError as exc:
            raise ConfigError(str(exc)) from None
    else:
        location = base.location

    try:
        return base.model_copy(
            update={
                "location": location,
                "weather": base.weather.model_copy(update={"api_key": api_key}),
                "balance": base.balance.model_copy(
                    update={
                        "inches_per_cycle": inches,
                        "forecast_days": forecast_days,
                        "monthly": monthly,
                    }
                ),
                "watering": watering,
                "sewer": sewer,
                "runtime": base.runtime.model_copy(update={"fail_mode": fail_mode}),
            }
        )
    except ValidationError as exc:
        raise ConfigError(str(exc.errors()[0]["msg"])) from None


def _history_row(record: WateringRecord, timezone: str) -> DashboardHistoryRow:
    when = (
        datetime.fromtimestamp(record.checked_at, ZoneInfo(timezone))
        .strftime("%b %d")
        .replace(" 0", " ")
    )
    verdict, verdict_class = watering_verdict(record)
    credit = f"+{record.inches_credited:.2f} in" if record.inches_credited else "—"
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
        return "Sprinklers can run tonight"
    if watering_required is False:
        return "Sprinklers are blocked tonight"
    return "Waiting on today's weather check"


def _hero_subtitle(*, sewer_lockout: bool, decision_short: str) -> str:
    if sewer_lockout:
        return "Sewer lockout — sprinklers blocked"
    return decision_short


def _updated_meta(last_updated: str) -> str:
    if last_updated == "never":
        return "Awaiting weather"
    if "(" in last_updated and last_updated.endswith("ago)"):
        return last_updated.rsplit("(", 1)[-1].rstrip(")")
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
        deficit_class=summary.deficit_class,
        progress_pct=summary.progress_pct,
    )


def _stale_check_message(settings: Settings, state: State, *, now: datetime) -> str | None:
    message = missed_check_message(settings, state, now=now)
    if message is None:
        return None
    stamp = state.last_weather_update
    if stamp is None:
        return f"{message}. No successful check on record."
    age = max(0.0, now.timestamp() - stamp)
    return f"{message}. Last successful check: {format_duration(age)} ago."


def build_dashboard_view(
    settings_path: Path | str,
    *,
    fetch_live: bool = False,
    history_limit: int = 7,
) -> DashboardView:
    global _last_live_fetch_mono, _pending_live_note
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
            _pending_live_note = live_note
        else:
            _last_live_fetch_mono = time.monotonic()
            _pending_live_note = None
    if (not fetch_live) and _pending_live_note:
        live_note = _pending_live_note
        _pending_live_note = None
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
        local_time=snapshot.local_time.strftime("%H:%M %Z"),
        next_check=format_duration(snapshot.next_check_seconds),
        last_error=snapshot.state.last_error,
        stale_check=_stale_check_message(settings, snapshot.state, now=snapshot.local_time),
        live_note=live_note,
        sewer_lockout=sewer_note,
        balance=balance,
        history_rows=rows,
        refresh_seconds=None if effective_live else 60,
    )


def build_config_page(
    settings_path: Path | str,
    *,
    values: ConfigFormValues | None = None,
    error: str | None = None,
    saved: bool = False,
) -> str:
    settings = load_settings(settings_path)
    form_values = values if values is not None else config_values_from_settings(settings)
    return render_config_html(form_values, error=error, saved=saved)


class DashboardHTTPServer(ThreadingHTTPServer):
    settings_path: Path


def _parse_form(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: (values[0] if values else "") for key, values in parsed.items()}


def _make_handler() -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def _send_html(self, body: str, *, status: int = 200) -> None:
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _send_redirect(self, location: str) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            dashboard_server = cast(DashboardHTTPServer, self.server)
            settings_path = dashboard_server.settings_path
            if path == "/config":
                saved = "saved" in parse_qs(parsed.query)
                self._send_html(build_config_page(settings_path, saved=saved))
                return
            if path == "/":
                fetch_live = False
            elif path == "/live":
                fetch_live = True
            else:
                self.send_error(404)
                return
            view = build_dashboard_view(settings_path, fetch_live=fetch_live)
            if fetch_live and view.live_note and not view.live_mode:
                self._send_redirect("/")
                return
            self._send_html(render_dashboard_html(view))

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            if path != "/config":
                self.send_error(404)
                return
            dashboard_server = cast(DashboardHTTPServer, self.server)
            settings_path = dashboard_server.settings_path
            length = int(self.headers.get("Content-Length", "0") or "0")
            form = _parse_form(self.rfile.read(max(0, length)))
            values = config_values_from_form(form)
            try:
                base = load_settings(settings_path)
                updated = apply_config_form(base, form)
                write_settings(settings_path, updated)
            except ConfigError as exc:
                self._send_html(
                    build_config_page(settings_path, values=values, error=str(exc)),
                    status=400,
                )
                return
            self._send_redirect("/config?saved=1")

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
