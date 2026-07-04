from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from rain_bypass import balance, config
from rain_bypass.config import Settings, State, in_sewer_lockout, load_settings
from rain_bypass.exceptions import WeatherError
from rain_bypass.logic import safety_allows_watering
from rain_bypass.models import WeatherSnapshot
from rain_bypass.weather import fetch_weather
from rain_bypass.windows import local_now, seconds_until_next_check


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    settings: Settings
    state: State
    local_time: datetime
    next_check_seconds: float
    sewer_lockout: bool
    live: WeatherSnapshot | None
    live_error: str | None
    effective_state: State
    target_to_date: float | None
    monthly_target: float | None
    deficit: float | None
    balance_ok: bool | None
    safety_ok: bool | None
    would_water: bool | None


def format_duration(seconds: float) -> str:
    total_minutes = int(seconds // 60)
    if total_minutes < 60:
        return f"{total_minutes}m"
    hours, minutes = divmod(total_minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def format_timestamp(timestamp: float | None, timezone: str) -> str:
    if timestamp is None:
        return "never"
    return datetime.fromtimestamp(timestamp, ZoneInfo(timezone)).strftime("%Y-%m-%d %H:%M %Z")


def relay_label(allowed: bool | None) -> str:
    if allowed is None:
        return "unknown (waiting for first check)"
    if allowed:
        return "ALLOW watering (relay open / dry sensor)"
    return "BLOCK watering (relay closed / wet sensor)"


def gather_status(settings: Settings, state: State, *, fetch_live: bool = True) -> StatusSnapshot:
    now = local_now(settings)
    today = config.local_today(settings.location)
    sewer = in_sewer_lockout(settings.sewer, today)
    effective = balance.ensure_balance_month(state, today)

    live: WeatherSnapshot | None = None
    live_error: str | None = None
    target: float | None = None
    month_target: float | None = None
    deficit: float | None = None
    balance_ok: bool | None = None
    safety_ok: bool | None = None
    would_water: bool | None = None

    if sewer:
        would_water = False
    elif fetch_live:
        try:
            live = fetch_weather(settings)
        except WeatherError as exc:
            live_error = str(exc)
        else:
            month_target = balance.monthly_target(today, settings)
            target = balance.target_to_date(today, settings)
            deficit = balance.compute_deficit(
                today,
                settings,
                live.rain_mtd,
                effective.irrigation_inches_mtd,
                live.forecast_inches,
            )
            balance_ok = balance.balance_allows_watering(
                today,
                settings,
                live.rain_mtd,
                effective.irrigation_inches_mtd,
                live.forecast_inches,
            )
            safety_ok = safety_allows_watering(live, settings)
            would_water = bool(balance_ok and safety_ok)
    elif state.watering_required is not None:
        would_water = bool(state.watering_required)

    return StatusSnapshot(
        settings=settings,
        state=state,
        local_time=now,
        next_check_seconds=seconds_until_next_check(settings, now=now),
        sewer_lockout=sewer,
        live=live,
        live_error=live_error,
        effective_state=effective,
        target_to_date=target,
        monthly_target=month_target,
        deficit=deficit,
        balance_ok=balance_ok,
        safety_ok=safety_ok,
        would_water=would_water,
    )


def _line(label: str, value: str, *, width: int = 16) -> str:
    return f"  {label:<{width}} {value}"


def _inch(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} in"


def format_status(snapshot: StatusSnapshot) -> str:
    settings = snapshot.settings
    loc = settings.location
    w = settings.watering
    b = settings.balance
    month_name = calendar.month_name[snapshot.local_time.month]
    lines = [
        "Sprinkler Rain Bypass — status",
        "─" * 40,
        _line("Location", f"{loc.zip_code} ({loc.timezone})"),
        _line("Local time", snapshot.local_time.strftime("%Y-%m-%d %H:%M %Z")),
        _line(
            "Daily check",
            f"{w.check_hour:02d}:{w.check_minute:02d} "
            f"(next in {format_duration(snapshot.next_check_seconds)})",
        ),
        _line("Inches / cycle", f"{b.inches_per_cycle:.2f} in"),
        "",
        "Saved state (last cycle)",
        _line("Relay", relay_label(snapshot.state.watering_required)),
        _line("Updated", format_timestamp(snapshot.state.last_weather_update, loc.timezone)),
        _line("Rain (saved)", _inch(snapshot.state.rainfall_inches)),
        _line("Forecast (saved)", _inch(snapshot.state.forecast_inches)),
        _line("Irrigation MTD", _inch(snapshot.effective_state.irrigation_inches_mtd)),
    ]
    if snapshot.state.last_error:
        lines.append(_line("Last error", snapshot.state.last_error))

    lines.extend(["", "Live evaluation"])
    if snapshot.sewer_lockout:
        sewer = settings.sewer
        lines.append(
            _line(
                "Sewer lockout",
                f"ACTIVE ({sewer.start_month:02d}/{sewer.start_day:02d}"
                f"–{sewer.end_month:02d}/{sewer.end_day:02d}) — watering blocked",
            )
        )
    else:
        lines.append(_line("Sewer lockout", "inactive"))

    if snapshot.live_error:
        lines.append(_line("Weather API", f"error — {snapshot.live_error}"))
    elif snapshot.live is None:
        lines.append(_line("Weather API", "skipped (--cached); see saved values above"))
    else:
        live = snapshot.live
        lines.extend(
            [
                _line("Rain MTD", _inch(live.rain_mtd)),
                _line("Forecast", f"{live.forecast_inches:.2f} in ({b.forecast_days} days)"),
                _line("Max day", f"{live.max_daily_inches:.2f} in (lookback)"),
                _line("Freeze block", "yes" if live.freeze_block else "no"),
            ]
        )

    if snapshot.monthly_target is not None and snapshot.target_to_date is not None:
        lines.extend(
            [
                "",
                f"Balance ({month_name})",
                _line("Month target", _inch(snapshot.monthly_target)),
                _line("Target to date", _inch(snapshot.target_to_date)),
                _line("Deficit", _inch(snapshot.deficit)),
                _line(
                    "Needs / cycle",
                    f">= {b.inches_per_cycle:.2f} in deficit to allow",
                ),
                _line("Balance gate", "pass" if snapshot.balance_ok else "block"),
                _line("Safety gate", "pass" if snapshot.safety_ok else "block"),
            ]
        )

    if snapshot.would_water is None:
        verdict = "unknown (need live weather or a completed cycle)"
    elif snapshot.would_water:
        verdict = "ALLOW watering if the panel runs today"
    else:
        verdict = "BLOCK watering (skip today's cycle)"
    lines.extend(["", _line("Would decide", verdict)])
    return "\n".join(lines)


def print_status(config_path: Path | str, *, fetch_live: bool = True) -> None:
    settings = load_settings(config_path)
    state_path = settings.runtime.state_path
    state = State.load(state_path)
    snapshot = gather_status(settings, state, fetch_live=fetch_live)
    typer.echo(format_status(snapshot))
