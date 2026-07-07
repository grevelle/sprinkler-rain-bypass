from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from rain_bypass.config import Settings, State, load_settings
from rain_bypass.logic import preview
from rain_bypass.models import Preview
from rain_bypass.windows import local_now, seconds_until_next_check


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    settings: Settings
    state: State
    local_time: datetime
    next_check_seconds: float
    preview: Preview


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
    return StatusSnapshot(
        settings=settings,
        state=state,
        local_time=now,
        next_check_seconds=seconds_until_next_check(settings, now=now),
        preview=preview(settings, state, fetch_live=fetch_live),
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
    pv = snapshot.preview
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
        _line("Irrigation MTD", _inch(pv.effective_state.irrigation_inches_mtd)),
    ]
    if snapshot.state.last_error:
        lines.append(_line("Last error", snapshot.state.last_error))

    lines.extend(["", "Live evaluation"])
    if pv.sewer_lockout:
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

    if pv.live_error:
        lines.append(_line("Weather API", f"error — {pv.live_error}"))
    elif pv.live is None:
        lines.append(_line("Weather API", "skipped (--cached); see saved values above"))
    else:
        live = pv.live
        lines.extend(
            [
                _line("Rain MTD", _inch(live.rain_mtd)),
                _line("Forecast", f"{live.forecast_inches:.2f} in ({b.forecast_days} days)"),
                _line("Max day", f"{live.max_daily_inches:.2f} in (lookback)"),
                _line("Freeze block", "yes" if live.freeze_block else "no"),
            ]
        )

    evaluation = pv.evaluation
    if evaluation is not None:
        lines.extend(
            [
                "",
                f"Balance ({month_name})",
                _line("Month target", _inch(evaluation.monthly_target)),
                _line("Target to date", _inch(evaluation.target_to_date)),
                _line("Deficit", _inch(evaluation.deficit)),
                _line(
                    "Needs / cycle",
                    f">= {b.inches_per_cycle:.2f} in deficit to allow",
                ),
                _line("Balance gate", "pass" if evaluation.balance_ok else "block"),
                _line("Safety gate", "pass" if evaluation.safety_ok else "block"),
            ]
        )

    would_water = pv.would_water
    if would_water is None:
        verdict = "unknown (need live weather or a completed cycle)"
    elif would_water:
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
