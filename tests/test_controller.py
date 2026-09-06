from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import patch_local_today, weather_snapshot
from typer.testing import CliRunner

from rain_bypass.cli import app
from rain_bypass.config import State, load_settings
from rain_bypass.controller import run, tick
from rain_bypass.exceptions import WeatherError


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def _weather_error(_settings):
    raise WeatherError("x")


def test_tick_preserves_rainfall_on_weather_error(settings, tmp_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"state_path": state_path}),
        }
    )
    state = State(rainfall_inches=0.42, forecast_inches=0.11)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", _weather_error)
    saved = tick(settings, state, lambda _required: None)
    assert saved.rainfall_inches == pytest.approx(0.42)
    assert saved.forecast_inches == pytest.approx(0.11)
    assert saved.last_error == "x"


def test_tick_persists_balance_state(settings, tmp_path, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    state_path = tmp_path / "state.json"
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"state_path": state_path}),
        }
    )
    monkeypatch.setattr(
        "rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0, 0.0, 0.0)
    )
    tick(settings, State(), lambda _required: None)
    from rain_bypass.history import history_path, irrigation_mtd, load_records

    path = history_path(settings)
    assert path.is_file()
    assert irrigation_mtd(settings, date(2024, 7, 15)) == pytest.approx(0.3)
    records = load_records(path)
    assert len(records) == 1
    assert records[0].inches_credited == pytest.approx(0.3)


def test_run_once(tmp_path, settings_path, monkeypatch) -> None:
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    patch_local_today(monkeypatch, date(2024, 2, 1))
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))

    def _stop(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, sleep=_stop)


def test_run_loop_uses_scheduled_check(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))

    def _stop(seconds: float) -> None:
        assert seconds == pytest.approx(42.0)
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(
            settings,
            pin_factory=_noop_pins,
            seconds_until_check=lambda _s: 42.0,
            sleep=_stop,
        )


def test_run_loop_waits_before_first_tick(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    tick_calls = 0

    def counting_tick(*args, **kwargs):
        nonlocal tick_calls
        tick_calls += 1
        return tick(*args, **kwargs)

    monkeypatch.setattr("rain_bypass.controller.tick", counting_tick)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    monkeypatch.setattr(
        "rain_bypass.windows.daily_check_pending",
        lambda *_a, **_k: False,
    )

    def _interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, sleep=_interrupt_sleep)

    assert tick_calls == 0


def test_run_loop_ticks_after_scheduled_sleep(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    tick_calls = 0

    def counting_tick(*args, **kwargs):
        nonlocal tick_calls
        tick_calls += 1
        return tick(*args, **kwargs)

    monkeypatch.setattr("rain_bypass.controller.tick", counting_tick)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    monkeypatch.setattr(
        "rain_bypass.windows.daily_check_pending",
        lambda *_a, **_k: False,
    )

    sleeps = 0

    def sleep_once(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, seconds_until_check=lambda _s: 1.0, sleep=sleep_once)

    assert sleeps == 2
    assert tick_calls == 1


def test_run_applies_cached_state_before_tick(tmp_path, settings_path, monkeypatch) -> None:
    calls: list[bool] = []

    @contextmanager
    def tracking_pins(_gpio):
        class Driver:
            def apply(self, watering_required: bool) -> None:
                calls.append(watering_required)

        yield Driver()

    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    State(watering_required=True).save(state_path)
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    run(settings, once=True, pin_factory=tracking_pins)
    assert calls[0] is True
    assert calls[-1] is True


def test_run_applies_fail_safe_when_no_cached_state(tmp_path, settings_path, monkeypatch) -> None:
    calls: list[bool] = []

    @contextmanager
    def tracking_pins(_gpio):
        class Driver:
            def apply(self, watering_required: bool) -> None:
                calls.append(watering_required)

        yield Driver()

    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8").replace(
            'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
        ),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    run(settings, once=True, pin_factory=tracking_pins)
    assert calls[0] is False
    assert calls[-1] is True


def test_main_once(settings_path, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    result = CliRunner().invoke(app, ["--config", str(settings_path), "--once"])
    assert result.exit_code == 0


def test_main_keyboard_interrupt(settings_path) -> None:
    with patch("rain_bypass.cli.run", side_effect=KeyboardInterrupt):
        result = CliRunner().invoke(app, ["--config", str(settings_path)])
        assert result.exit_code == 0


def test_main_fatal_error(settings_path) -> None:
    with patch("rain_bypass.cli.run", side_effect=RuntimeError("boom")):
        result = CliRunner().invoke(app, ["--config", str(settings_path)])
        assert result.exit_code == 1


def test_run_loop_catches_up_missed_daily_check(settings, monkeypatch) -> None:
    patch_local_today(monkeypatch, date(2024, 7, 15))
    tick_calls = 0

    def counting_tick(*args, **kwargs):
        nonlocal tick_calls
        tick_calls += 1
        return tick(*args, **kwargs)

    monkeypatch.setattr("rain_bypass.controller.tick", counting_tick)
    monkeypatch.setattr("rain_bypass.logic.fetch_weather", lambda _s: weather_snapshot(0.0))
    monkeypatch.setattr(
        "rain_bypass.windows.daily_check_pending",
        lambda *_a, **_k: True,
    )

    def _interrupt_sleep(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins, sleep=_interrupt_sleep)

    assert tick_calls == 1
