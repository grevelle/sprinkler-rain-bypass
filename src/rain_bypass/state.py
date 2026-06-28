from __future__ import annotations

import json
from pathlib import Path

from rain_bypass.models import RuntimeState


def load_state(path: Path) -> RuntimeState:
    if not path.is_file():
        return RuntimeState()

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    return RuntimeState(
        last_weather_update=_optional_float(data.get("last_weather_update")),
        watering_required=_optional_bool(data.get("watering_required")),
        rainfall_inches=_optional_float(data.get("rainfall_inches")),
        last_error=_optional_str(data.get("last_error")),
    )


def save_state(path: Path, state: RuntimeState) -> None:
    payload = {
        "last_weather_update": state.last_weather_update,
        "watering_required": state.watering_required,
        "rainfall_inches": state.rainfall_inches,
        "last_error": state.last_error,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
