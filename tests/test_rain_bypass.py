from contextlib import contextmanager
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import requests
import responses

from rain_bypass.app import (
    ARCHIVE_URL,
    FORECAST_URL,
    decide,
    fetch_precip,
    in_season,
    main,
    run,
)
from rain_bypass.config import ConfigError, FailMode, Gpio, Season, State, load_settings
from rain_bypass.gpio import MockPins, PiPins, watering_pins


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def _weather_error(_settings):
    raise requests.RequestException("x")


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(41.8781)
    assert settings.watering.past_days == 7
    assert settings.watering.interval_seconds == pytest.approx(86400 / 2)
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.runtime.weather_timeout_seconds == 15


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("mutator",),
    [
        (lambda text: text.replace("latitude = 41.8781", "latitude = not_a_number"),),
        (lambda text: text.replace("past_days = 7", "past_days = 0"),),
    ],
)
def test_invalid_settings(settings_path, mutator):
    settings_path.write_text(mutator(settings_path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_state_load_missing(tmp_path):
    assert State.load(tmp_path / "missing.json") == State()


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(last_weather_update=123.0, watering_required=True, rainfall_inches=0.25)
    state.save(path)
    assert State.load(path).rainfall_inches == pytest.approx(0.25)


def test_in_season(settings):
    season = settings.season
    assert in_season(season, date(2024, 6, 1)) is True
    assert in_season(season, date(2024, 2, 1)) is False
    assert in_season(season, date(2024, 3, 19)) is True


def test_out_of_season(settings):
    winter = settings.model_copy(
        update={"season": Season(start_month=12, start_day=1, end_month=12, end_day=31)}
    )
    required, rainfall, in_season_flag, error = decide(winter, State())
    assert required is False and rainfall is None and in_season_flag is False and error is None


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(watering_required=True, rainfall_inches=0.1)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    required, _, _, _ = decide(settings, state)
    assert required is True


def test_fail_safe(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    required, _, _, error = decide(settings, State(watering_required=True, rainfall_inches=0.1))
    assert required is False
    assert error == "x"


def test_watering_required(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.1)
    required, rainfall, _, error = decide(settings, State())
    assert required is True
    assert rainfall == pytest.approx(0.1)
    assert error is None


def test_watering_blocked(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 2.0)
    required, _, _, _ = decide(settings, State())
    assert required is False


@responses.activate
def test_fetch_precip_forecast(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    payload = {
        "daily": {
            "time": [(today - timedelta(days=2)).isoformat(), today.isoformat()],
            "precipitation_sum": [2.0, 3.0],
        }
    }
    responses.add(responses.GET, FORECAST_URL, json=payload, status=200)
    assert fetch_precip(settings) == pytest.approx(5 / 25.4)


@responses.activate
def test_fetch_precip_forecast_falls_back_to_archive(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    archive_payload = {
        "daily": {
            "time": [(today - timedelta(days=2)).isoformat(), today.isoformat()],
            "precipitation_sum": [1.0, 2.0],
        }
    }
    responses.add(responses.GET, FORECAST_URL, json={"daily": {}}, status=200)
    responses.add(responses.GET, ARCHIVE_URL, json=archive_payload, status=200)
    assert fetch_precip(settings) == pytest.approx(3 / 25.4)


@responses.activate
def test_fetch_precip_archive(settings, monkeypatch):
    fixed = date(2020, 1, 3)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    payload = {"daily": {"time": ["2020-01-01", "2020-01-02"], "precipitation_sum": [1.0, 2.0]}}
    responses.add(responses.GET, ARCHIVE_URL, json=payload, status=200)
    assert fetch_precip(settings) == pytest.approx(3 / 25.4)


@responses.activate
def test_fetch_precip_http_error(settings, monkeypatch):
    fixed = date(2020, 1, 3)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    responses.add(responses.GET, ARCHIVE_URL, status=500)
    with pytest.raises(requests.RequestException):
        fetch_precip(settings)


def test_run_once(tmp_path, settings_path):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8")
        .replace('state_path = "state.json"', f'state_path = "{state_path.as_posix()}"')
        .replace("start_month = 3", "start_month = 12"),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.0)

    def _stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.app.time.sleep", _stop)
    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins)


def test_mock_pins():
    MockPins().apply(True)
    MockPins().apply(False)


def test_watering_pins_mock():
    gpio = Gpio(relay=1, watering_enabled_led=2, watering_disabled_led=3, mock=True)
    with watering_pins(gpio) as driver:
        driver.apply(True)


def test_pi_pins(fake_rpi, pi_gpio):
    fake_rpi_mod, fake_gpio = fake_rpi
    with patch.dict("sys.modules", {"RPi": fake_rpi_mod, "RPi.GPIO": fake_gpio}):
        driver = PiPins(pi_gpio)
        driver.apply(True)
        driver.apply(False)
        driver.cleanup()
        assert fake_gpio.setup.call_count == 3
        assert fake_gpio.output.call_count == 6


def test_watering_pins_requires_gpio_extra(pi_gpio):
    with pytest.raises(RuntimeError, match="gpio.mock"):
        with watering_pins(pi_gpio):
            pass


def test_watering_pins_pi_cleanup(fake_rpi, pi_gpio):
    fake_rpi_mod, fake_gpio = fake_rpi
    with patch.dict("sys.modules", {"RPi": fake_rpi_mod, "RPi.GPIO": fake_gpio}):
        with watering_pins(pi_gpio) as driver:
            driver.apply(False)
        fake_gpio.cleanup.assert_called_once()


def test_main_once(settings_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.0)
    assert main(["--config", str(settings_path), "--once"]) == 0


def test_main_keyboard_interrupt(settings_path):
    with patch("rain_bypass.app.run", side_effect=KeyboardInterrupt):
        assert main(["--config", str(settings_path)]) == 0


def test_main_fatal_error(settings_path):
    with patch("rain_bypass.app.run", side_effect=RuntimeError("boom")):
        assert main(["--config", str(settings_path)]) == 1
