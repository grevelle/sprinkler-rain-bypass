from __future__ import annotations

import json
import time
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import typer

from rain_bypass import config
from rain_bypass.config import (
    FrozenModel,
    Settings,
    State,
    load_settings,
)
from rain_bypass.models import Decision

HISTORY_RETENTION = timedelta(days=365)


class WateringRecord(FrozenModel):
    checked_at: float
    local_date: str
    allowed: bool
    inches_credited: float
    irrigation_mtd: float
    rain_mtd: float | None = None
    forecast_inches: float | None = None
    deficit: float | None = None
    sewer_lockout: bool = False
    weather_error: str | None = None


def history_path(settings: Settings) -> Path:
    runtime = settings.runtime
    if runtime.history_path is not None:
        return runtime.history_path
    return runtime.state_path.with_name("watering_history.jsonl")


def _month_prefix(today: date) -> str:
    return today.strftime("%Y-%m")


def irrigation_mtd(settings: Settings, today: date, *, path: Path | None = None) -> float:
    """Sum credited inches for the current local month — the single source of truth."""
    prefix = _month_prefix(today)
    records = _load_all_records(path or history_path(settings))
    return sum(record.inches_credited for record in records if record.local_date.startswith(prefix))


def build_record(
    settings: Settings,
    state_before: State,
    decision: Decision,
    *,
    checked_at: float | None = None,
) -> WateringRecord:
    today = config.local_today(settings.location)
    sewer = config.in_sewer_lockout(settings.sewer, today)
    when = checked_at if checked_at is not None else time.time()
    before_mtd = irrigation_mtd(settings, today)
    credited = settings.balance.inches_per_cycle if decision.watering_required else 0.0
    after_mtd = before_mtd + credited
    evaluation = decision.evaluation
    return WateringRecord(
        checked_at=when,
        local_date=today.isoformat(),
        allowed=decision.watering_required,
        inches_credited=credited,
        irrigation_mtd=after_mtd,
        rain_mtd=evaluation.rain_mtd if evaluation is not None else state_before.rainfall_inches,
        forecast_inches=(
            evaluation.forecast_inches if evaluation is not None else state_before.forecast_inches
        ),
        deficit=evaluation.deficit if evaluation is not None else None,
        sewer_lockout=sewer,
        weather_error=decision.error,
    )


def _retention_cutoff(*, now: float | None = None) -> float:
    when = now if now is not None else time.time()
    return when - HISTORY_RETENTION.total_seconds()


def _parse_line(line: str) -> WateringRecord | None:
    text = line.strip()
    if not text:
        return None
    return WateringRecord.model_validate(json.loads(text))


def _load_all_records(path: Path) -> list[WateringRecord]:
    if not path.is_file():
        return []
    records: list[WateringRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        record = _parse_line(line)
        if record is not None:
            records.append(record)
    return records


def _write_records(path: Path, records: Sequence[WateringRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )


def append_record(path: Path, record: WateringRecord, *, now: float | None = None) -> None:
    cutoff = _retention_cutoff(now=now)
    kept = [existing for existing in _load_all_records(path) if existing.checked_at >= cutoff]
    kept.append(record)
    _write_records(path, kept)


def append_watering_history(
    settings: Settings,
    state_before: State,
    decision: Decision,
    *,
    checked_at: float | None = None,
) -> WateringRecord:
    record = build_record(settings, state_before, decision, checked_at=checked_at)
    append_record(history_path(settings), record)
    return record


def migrate_legacy_irrigation(settings: Settings) -> None:
    """Move one-time legacy irrigation MTD from state.json into the history log."""
    state_path = settings.runtime.state_path
    if not state_path.is_file():
        return
    raw_obj = json.loads(state_path.read_text(encoding="utf-8"))
    if not isinstance(raw_obj, dict):
        return
    raw = cast(dict[str, Any], raw_obj)
    legacy = raw.get("irrigation_inches_mtd")
    if not isinstance(legacy, int | float) or legacy <= 0:
        return
    today = config.local_today(settings.location)
    balance_month = raw.get("balance_month")
    if isinstance(balance_month, int) and balance_month != today.month:
        return
    if irrigation_mtd(settings, today) >= float(legacy):
        _strip_legacy_irrigation_fields(state_path, raw)
        return
    credited = float(legacy) - irrigation_mtd(settings, today)
    append_record(
        history_path(settings),
        WateringRecord(
            checked_at=time.time(),
            local_date=today.isoformat(),
            allowed=True,
            inches_credited=credited,
            irrigation_mtd=float(legacy),
        ),
    )
    _strip_legacy_irrigation_fields(state_path, raw)


def _strip_legacy_irrigation_fields(state_path: Path, raw: dict[str, Any]) -> None:
    if "irrigation_inches_mtd" not in raw and "balance_month" not in raw:
        return
    raw.pop("irrigation_inches_mtd", None)
    raw.pop("balance_month", None)
    state_path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")


def load_records(path: Path, *, limit: int = 30) -> list[WateringRecord]:
    if limit <= 0:
        return []
    records = _load_all_records(path)
    if limit >= len(records):
        return records
    return records[-limit:]


def _format_allowed(record: WateringRecord) -> str:
    if record.sewer_lockout:
        return typer.style("BLOCK (sewer)", fg=typer.colors.RED)
    if record.weather_error:
        return typer.style("BLOCK (weather error)", fg=typer.colors.RED)
    if record.allowed:
        return typer.style("ALLOW", fg=typer.colors.GREEN)
    return typer.style("BLOCK", fg=typer.colors.RED)


def print_history(
    settings_path: Path,
    *,
    limit: int = 30,
) -> None:
    settings = load_settings(settings_path)
    path = history_path(settings)
    records = load_records(path, limit=limit)
    typer.echo("Sprinkler Rain Bypass — watering history")
    typer.echo("─" * 40)
    if not records:
        typer.echo(f"No history yet ({path}).")
        typer.echo("Records append after each control cycle (--once or daily check).")
        return
    tz = settings.location.timezone
    for record in reversed(records):
        when = datetime.fromtimestamp(record.checked_at, ZoneInfo(tz)).strftime("%Y-%m-%d %H:%M")
        verdict = _format_allowed(record)
        credit = f"+{record.inches_credited:.2f} in"
        typer.echo(f"  {when}  {verdict}  {credit}  irr_mtd={record.irrigation_mtd:.2f} in")
        if record.rain_mtd is not None:
            rain = f"rain={record.rain_mtd:.2f}"
            forecast = (
                f" fc={record.forecast_inches:.2f}" if record.forecast_inches is not None else ""
            )
            deficit = f" deficit={record.deficit:.2f}" if record.deficit is not None else ""
            typer.echo(f"    {rain}{forecast}{deficit}")
