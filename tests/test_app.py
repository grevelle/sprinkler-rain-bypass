from contextlib import contextmanager
from datetime import date

import pytest

from rain_bypass.app import decide, in_season, run
from rain_bypass.config import Decision, FailMode, Season, State, load_settings
from rain_bypass.weather import WeatherError, precip_window


def test_precip_window(settings):
    start, end = precip_window(settings)
    assert (end - start).days == settings.watering.past_days - 1


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2024, 6, 1), True), (date(2024, 2, 1), False), (date(2024, 3, 19), True)],
)
def test_in_season(today, expected):
    season = Season(start_month=3, start_day=19, end_month=9, end_day=12)
    assert in_season(season, today) is expected


def test_out_of_season(settings):
    winter = settings.model_copy(
        update={"season": Season(start_month=12, start_day=1, end_month=12, end_day=31)}
    )
    assert decide(winter, State()) == Decision(
        watering_required=False, rainfall_inches=None, in_season=False
    )


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(watering_required=True, rainfall_inches=0.1)

    def _fail(_settings):
        raise WeatherError("offline")

    monkeypatch.setattr("rain_bypass.app.fetch_precip", _fail)
    decision = decide(settings, state)
    assert decision.watering_required is True


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(last_weather_update=123.0, watering_required=True, rainfall_inches=0.25)
    state.save(path)
    assert State.load(path).rainfall_inches == pytest.approx(0.25)


def test_run_persists_state(tmp_path, settings_path):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8")
        .replace('state_path = "state.json"', f'state_path = "{state_path.as_posix()}"')
        .replace("start_month = 3", "start_month = 12"),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)

    @contextmanager
    def noop_pins(_gpio):
        class Driver:
            def apply(self, _required): ...

        yield Driver()

    run(settings, once=True, pin_factory=noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None
