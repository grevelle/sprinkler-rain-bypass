from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import tomli_w

from rain_bypass.config import Settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_SETTINGS_PATH = _REPO_ROOT / "settings.example.toml"


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        existing = base.get(key)
        if isinstance(value, Mapping) and isinstance(existing, dict):
            _deep_merge(
                cast(dict[str, Any], existing),
                cast(Mapping[str, Any], value),
            )
        else:
            base[key] = value
    return base


def _load_example_data(**sections: Mapping[str, Any]) -> dict[str, Any]:
    if not EXAMPLE_SETTINGS_PATH.is_file():
        raise FileNotFoundError(f"Example settings not found: {EXAMPLE_SETTINGS_PATH}")
    with EXAMPLE_SETTINGS_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return _deep_merge(data, sections)


def load_example_settings(**sections: Mapping[str, Any]) -> Settings:
    return Settings.model_validate(_load_example_data(**sections))


def settings_to_toml_dict(settings: Settings) -> dict[str, Any]:
    data = settings.model_dump()
    data["runtime"]["state_path"] = str(settings.runtime.state_path)
    return data


def write_settings(path: Path | str, settings: Settings) -> None:
    target = Path(path)
    target.write_text(tomli_w.dumps(settings_to_toml_dict(settings)), encoding="utf-8")
