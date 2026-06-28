from contextlib import contextmanager
from datetime import date

import pytest

from rain_bypass.app import Decision, State, decide, in_season, load_state, run, save_state
from rain_bypass.config import FailMode, Season, load_settings
from rain_bypass.weather import WeatherError, mm_to_inches, precip_window


def test_precip_window(settings):
    start, end = precip_window(settings)
    assert end >= start
    assert (end - start).days == settings.watering.past_days - 1


def test_mm_to_inches():
    assert mm_to_inches(25.4) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("today", "expected"),
    [(date(2024, 6, 1), True), (date(2024, 2, 1), False), (date(2024, 3, 19), True)],
)
def test_in_season(today, expected):
    season = Season(start_month=3, start_day=19, end_month=9, end_day=12)
    assert in_season(season, today) is expected


def test_out_of_season(settings):
    winter = settings.model_copy(update={"season": Season(start_month=12, start_day=1, end_month=12, end_day=31)})
    decision = decide(winter, State())
    assert decision == Decision(False, None, False, "season")


def test_fail_mode_keep_last_state(settings, monkeypatch):
    runtime = settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
    settings = settings.model_copy(update={"runtime": runtime})
    state = State(watering_required=True, rainfall_inches=0.1)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: (_ for _ in ()).throw(WeatherError("offline")))
    decision = decide(settings, state)
    assert decision.watering_required is True
    assert decision.source == "last_state"


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(123.0, True, 0.25, None)
    save_state(path, state)
    assert load_state(path).rainfall_inches == pytest.approx(0.25)


def test_run_persists_state(tmp_path, settings_path):
    state_path = tmp_path / "state.json"
    text = settings_path.read_text(encoding="utf-8").replace(
        'state_path = "state.json"', f'state_path = "{state_path.as_posix()}"'
    )
    text = text.replace("start_month = 3", "start_month = 12")
    settings_path.write_text(text, encoding="utf-8")
    settings = load_settings(settings_path)

    @contextmanager
    def noop_pins(_gpio):
        class Driver:
            def apply(self, _required): ...

        yield Driver()

    run(settings, once=True, pin_factory=noop_pins)
    saved = load_state(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None
