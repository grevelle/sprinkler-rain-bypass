import sys
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from rain_bypass.config import Gpio, State, load_example_settings, load_settings, write_settings
from rain_bypass.history import WateringRecord
from rain_bypass.models import Evaluation, Preview, WeatherSnapshot
from rain_bypass.status import StatusSnapshot
from rain_bypass.weather import TimelineDay
from rain_bypass.web import build_dashboard_view

TEST_SETTINGS_OVERRIDES = {
    "weather": {"api_key": "test-key"},
    "gpio": {"mock": True},
    "runtime": {"weather_timeout_seconds": 15},
}


def weather_snapshot(
    rain_mtd: float = 0.0,
    forecast: float = 0.0,
    max_daily: float = 0.0,
    freeze_block: bool = False,
) -> WeatherSnapshot:
    return WeatherSnapshot(rain_mtd, forecast, max_daily, freeze_block)


def timeline_day(**fields: object) -> TimelineDay:
    return TimelineDay.model_validate(fields)


def patch_local_today(monkeypatch: pytest.MonkeyPatch, fixed: date) -> date:
    monkeypatch.setattr("rain_bypass.config.local_today", lambda _loc: fixed)
    return fixed


def evaluation(**overrides: object) -> Evaluation:
    defaults: dict[str, object] = {
        "watering_required": True,
        "balance_ok": True,
        "safety_ok": True,
        "deficit": 0.5,
        "target_to_date": 0.97,
        "monthly_target": 5.0,
        "rain_mtd": 0.26,
        "forecast_inches": 0.02,
        "max_daily_inches": 0.05,
        "freeze_block": False,
    }
    defaults.update(overrides)
    return Evaluation(**defaults)  # type: ignore[arg-type]


def preview(**overrides: object) -> Preview:
    defaults: dict[str, object] = {
        "irrigation_mtd": 0.63,
        "sewer_lockout": False,
        "live": None,
        "live_error": None,
        "evaluation": evaluation(),
        "cached_verdict": True,
        "from_saved_weather": True,
        "safety_known": True,
    }
    defaults.update(overrides)
    return Preview(**defaults)  # type: ignore[arg-type]


def status_snapshot(
    settings,
    *,
    preview_obj: Preview | None = None,
    state: State | None = None,
    fetch_live: bool = False,
    when: datetime | None = None,
) -> StatusSnapshot:
    if preview_obj is None:
        preview_obj = preview()
    if state is None:
        state = State(watering_required=True, rainfall_inches=0.1, forecast_inches=0.05)
    if when is None:
        when = datetime(2024, 7, 15, 12, 0, tzinfo=ZoneInfo(settings.location.timezone))
    return StatusSnapshot(
        settings=settings,
        state=state,
        local_time=when,
        next_check_seconds=3600.0,
        preview=preview_obj,
        fetch_live=fetch_live,
    )


def mock_gather_status(monkeypatch: pytest.MonkeyPatch, snapshot: StatusSnapshot) -> None:
    monkeypatch.setattr(
        "rain_bypass.web.gather_status",
        lambda *_args, **_kwargs: snapshot,
    )


def watering_record(**overrides: object) -> WateringRecord:
    defaults: dict[str, object] = {
        "checked_at": 1.0,
        "local_date": "2024-07-15",
        "allowed": False,
        "inches_credited": 0.0,
        "irrigation_mtd": 0.3,
    }
    defaults.update(overrides)
    return WateringRecord(**defaults)  # type: ignore[arg-type]


def build_dashboard(
    settings_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preview_obj: Preview | None = None,
    state: State | None = None,
    fetch_live: bool = False,
    history_limit: int | None = None,
    when: datetime | None = None,
):
    settings = load_settings(settings_path)
    patch_local_today(monkeypatch, date(2024, 7, 15))
    snapshot = status_snapshot(
        settings,
        preview_obj=preview_obj,
        state=state,
        fetch_live=fetch_live,
        when=when,
    )
    mock_gather_status(monkeypatch, snapshot)
    kwargs: dict[str, object] = {"fetch_live": fetch_live}
    if history_limit is not None:
        kwargs["history_limit"] = history_limit
    return build_dashboard_view(settings_path, **kwargs)  # type: ignore[arg-type]


@pytest.fixture
def settings_path(tmp_path: Path) -> Path:
    path = tmp_path / "settings.toml"
    overrides = dict(TEST_SETTINGS_OVERRIDES)
    overrides["runtime"] = {
        **TEST_SETTINGS_OVERRIDES["runtime"],
        "state_path": tmp_path / "state.json",
    }
    write_settings(path, load_example_settings(**overrides))
    return path


@pytest.fixture
def settings(settings_path: Path):
    return load_settings(settings_path)


@pytest.fixture
def fake_output_device(monkeypatch):
    devices: list[object] = []

    class FakeDevice:
        def __init__(self, pin: int, initial_value: bool = False) -> None:
            self.pin = pin
            self.value = initial_value
            self.closed = False
            devices.append(self)

        def on(self) -> None:
            self.value = True

        def off(self) -> None:
            self.value = False

        def close(self) -> None:
            self.closed = True

    fake_gpiozero = ModuleType("gpiozero")
    fake_gpiozero.OutputDevice = FakeDevice
    monkeypatch.setitem(sys.modules, "gpiozero", fake_gpiozero)
    return devices


@pytest.fixture
def pi_gpio():
    return Gpio(relay=25, watering_enabled_led=4, watering_disabled_led=27, mock=False)
