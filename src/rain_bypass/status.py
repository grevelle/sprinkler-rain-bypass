from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import typer

from rain_bypass.config import Settings, State, format_sewer_range, load_settings
from rain_bypass.logic import preview
from rain_bypass.models import Evaluation, Preview
from rain_bypass.windows import local_now, seconds_until_next_check


@dataclass(frozen=True, slots=True)
class StatusSnapshot:
    settings: Settings
    state: State
    local_time: datetime
    next_check_seconds: float
    preview: Preview
    fetch_live: bool


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


def format_updated(timestamp: float | None, timezone: str, *, now: datetime | None = None) -> str:
    if timestamp is None:
        return "never"
    when = format_timestamp(timestamp, timezone)
    if now is None:
        return when
    age_seconds = max(0.0, now.timestamp() - timestamp)
    return f"{when} ({format_duration(age_seconds)} ago)"


def relay_label(allowed: bool | None) -> str:
    if allowed is None:
        return "unknown (waiting for first check)"
    if allowed:
        return "ALLOW watering (relay open / dry sensor)"
    return "BLOCK watering (relay closed / wet sensor)"


def format_deficit_formula(
    evaluation: Evaluation, irrigation_mtd: float, inches_per_cycle: float
) -> str:
    return (
        f"{evaluation.target_to_date:.2f} − {evaluation.rain_mtd:.2f} − "
        f"{irrigation_mtd:.2f} − {evaluation.forecast_inches:.2f} = "
        f"{evaluation.deficit:.2f} in (need ≥ {inches_per_cycle:.2f} to allow)"
    )


def format_safety_gate(evaluation: Evaluation, *, safety_known: bool) -> str:
    if not safety_known:
        return "unknown (not saved — run without --cached)"
    if not evaluation.safety_ok:
        if evaluation.freeze_block:
            return "block (freeze)"
        return "block (storm / heavy rain)"
    return "pass"


def format_would_decide_now(
    would_water: bool | None, *, safety_known: bool, balance_ok: bool | None
) -> str:
    if would_water is None:
        if safety_known is False and balance_ok is True:
            return "ALLOW by balance (safety unverified — run without --cached)"
        return "unknown (need live weather or a completed cycle)"
    if would_water:
        return "ALLOW watering if the panel runs today"
    return "BLOCK watering (skip today's cycle)"


def relay_mismatch_note(relay: bool | None, projected: bool | None) -> str | None:
    if relay is None or projected is None:
        return None
    if relay == projected:
        return None
    relay_word = "ALLOW" if relay else "BLOCK"
    projected_word = "ALLOW" if projected else "BLOCK"
    return (
        f"Note: saved relay is {relay_word} but saved weather projects {projected_word} "
        "now. A service restart re-evaluates and updates the relay."
    )


def gather_status(settings: Settings, state: State, *, fetch_live: bool = True) -> StatusSnapshot:
    now = local_now(settings)
    return StatusSnapshot(
        settings=settings,
        state=state,
        local_time=now,
        next_check_seconds=seconds_until_next_check(settings, now=now),
        preview=preview(settings, state, fetch_live=fetch_live),
        fetch_live=fetch_live,
    )


def format_inches(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.2f} in"


def _line(label: str, value: str, *, width: int = 16) -> str:
    return f"  {label:<{width}} {value}"


def _append_balance_section(
    lines: list[str],
    *,
    month_name: str,
    evaluation: Evaluation,
    irrigation_mtd: float,
    inches_per_cycle: float,
    safety_known: bool,
) -> None:
    lines.extend(
        [
            "",
            f"Balance ({month_name})",
            _line("Month target", format_inches(evaluation.monthly_target)),
            _line("Target to date", format_inches(evaluation.target_to_date)),
            _line("Deficit", format_inches(evaluation.deficit)),
            _line("Formula", format_deficit_formula(evaluation, irrigation_mtd, inches_per_cycle)),
            _line(
                "Needs / cycle",
                f">= {inches_per_cycle:.2f} in deficit to allow",
            ),
            _line("Balance gate", "pass" if evaluation.balance_ok else "block"),
            _line("Safety gate", format_safety_gate(evaluation, safety_known=safety_known)),
        ]
    )


def _append_weather_metrics(
    lines: list[str],
    *,
    rain_mtd: float,
    forecast_inches: float,
    forecast_days: int,
    max_daily_inches: float | None,
    freeze_block: bool | None,
    saved: bool,
) -> None:
    prefix = "Rain MTD (saved)" if saved else "Rain MTD"
    forecast_label = "Forecast (saved)" if saved else "Forecast"
    lines.append(_line(prefix, format_inches(rain_mtd)))
    lines.append(_line(forecast_label, f"{forecast_inches:.2f} in ({forecast_days} days)"))
    if max_daily_inches is None:
        lines.append(_line("Max day", "n/a (not saved)"))
    else:
        lines.append(_line("Max day", f"{max_daily_inches:.2f} in (lookback)"))
    if freeze_block is None:
        lines.append(_line("Freeze block", "unknown (not saved)"))
    else:
        lines.append(_line("Freeze block", "yes" if freeze_block else "no"))


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
        _line(
            "Updated",
            format_updated(
                snapshot.state.last_weather_update,
                loc.timezone,
                now=snapshot.local_time,
            ),
        ),
        _line("Rain (saved)", format_inches(snapshot.state.rainfall_inches)),
        _line("Forecast (saved)", format_inches(snapshot.state.forecast_inches)),
        _line("Irrigation MTD", format_inches(pv.irrigation_mtd)),
    ]
    if snapshot.state.last_error:
        lines.append(_line("Last error", snapshot.state.last_error))

    if pv.sewer_lockout:
        sewer = settings.sewer
        lines.extend(
            [
                "",
                "Evaluation",
                _line(
                    "Sewer lockout",
                    f"ACTIVE ({format_sewer_range(sewer)}) — watering blocked",
                ),
            ]
        )
    elif snapshot.fetch_live:
        lines.extend(["", "Live evaluation"])
        lines.append(_line("Sewer lockout", "inactive"))
        if pv.live_error:
            lines.append(_line("Weather API", f"error — {pv.live_error}"))
        elif pv.live is not None:
            live = pv.live
            _append_weather_metrics(
                lines,
                rain_mtd=live.rain_mtd,
                forecast_inches=live.forecast_inches,
                forecast_days=b.forecast_days,
                max_daily_inches=live.max_daily_inches,
                freeze_block=live.freeze_block,
                saved=False,
            )
        if pv.evaluation is not None:
            _append_balance_section(
                lines,
                month_name=month_name,
                evaluation=pv.evaluation,
                irrigation_mtd=pv.irrigation_mtd,
                inches_per_cycle=b.inches_per_cycle,
                safety_known=True,
            )
    elif pv.from_saved_weather and pv.evaluation is not None:
        evaluation = pv.evaluation
        lines.extend(["", "Projected decision (saved weather, no API)"])
        _append_weather_metrics(
            lines,
            rain_mtd=evaluation.rain_mtd,
            forecast_inches=evaluation.forecast_inches,
            forecast_days=b.forecast_days,
            max_daily_inches=snapshot.state.max_daily_inches,
            freeze_block=snapshot.state.freeze_block,
            saved=True,
        )
        _append_balance_section(
            lines,
            month_name=month_name,
            evaluation=evaluation,
            irrigation_mtd=pv.irrigation_mtd,
            inches_per_cycle=b.inches_per_cycle,
            safety_known=pv.safety_known,
        )
    else:
        lines.extend(
            [
                "",
                "Projected decision",
                _line("Weather", "no saved values — run without --cached"),
            ]
        )

    would_water = pv.would_water
    balance_ok = pv.evaluation.balance_ok if pv.evaluation is not None else None
    lines.extend(
        [
            "",
            _line(
                "Would decide now",
                format_would_decide_now(
                    would_water,
                    safety_known=pv.safety_known,
                    balance_ok=balance_ok,
                ),
            ),
        ]
    )
    mismatch = relay_mismatch_note(snapshot.state.watering_required, would_water)
    if mismatch:
        lines.extend(["", f"  {mismatch}"])
    return "\n".join(lines)


def print_status(config_path: Path | str, *, fetch_live: bool = True) -> None:
    settings = load_settings(config_path)
    state_path = settings.runtime.state_path
    state = State.load(state_path)
    snapshot = gather_status(settings, state, fetch_live=fetch_live)
    typer.echo(format_status(snapshot))
