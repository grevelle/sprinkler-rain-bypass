from contextlib import contextmanager
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
import requests
import responses

from rain_bypass.app import (
    TIMELINE_QUERY,
    VISUAL_CROSSING,
    PrecipTotals,
    _max_daily_precip,
    _sum_precip,
    allow_watering,
    decide,
    fetch_precip,
    forecast_window,
    in_season,
    main,
    past_ok,
    past_window,
    precip_window,
    run,
    seconds_until_next_check,
    timeline_window,
    update_blocked_until,
    watering_required,
)
from rain_bypass.config import ConfigError, FailMode, Gpio, Season, State, load_settings
from rain_bypass.gpio import MockPins, PiPins, watering_pins


@contextmanager
def _noop_pins(_gpio):
    yield SimpleNamespace(apply=lambda _required: None)


def _weather_error(_settings):
    raise requests.RequestException("x")


def _totals(past: float, forecast: float = 0.0, max_daily: float = 0.0) -> PrecipTotals:
    return PrecipTotals(past, forecast, max_daily)


def _timeline_url(settings, start: date, end: date) -> str:
    loc = settings.location
    return f"{VISUAL_CROSSING}/{loc.latitude},{loc.longitude}/{start}/{end}"


def test_load_settings(settings):
    assert settings.location.latitude == pytest.approx(43.106)
    assert settings.watering.past_days == 3
    assert settings.watering.forecast_days == 2
    assert settings.watering.forecast_inches_max == pytest.approx(0.5)
    assert settings.watering.event_inches == pytest.approx(0.25)
    assert settings.watering.rain_delay_days == 1
    assert settings.watering.check_hour == 4
    assert settings.watering.check_minute == 30
    assert settings.watering.interval_seconds == pytest.approx(86400 / 2)
    assert settings.runtime.fail_mode is FailMode.DISABLE_WATERING
    assert settings.runtime.weather_timeout_seconds == 15
    assert settings.weather.api_key == "test-key"


def test_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        load_settings(tmp_path / "missing.toml")


@pytest.mark.parametrize(
    ("mutator",),
    [
        (lambda text: text.replace("latitude = 43.106", "latitude = not_a_number"),),
        (lambda text: text.replace("past_days = 3", "past_days = 0"),),
        (lambda text: text.replace("forecast_days = 2", "forecast_days = -1"),),
        (lambda text: text.replace("rain_delay_days = 1", "rain_delay_days = -1"),),
        (lambda text: text.replace('api_key = "test-key"', 'api_key = ""'),),
    ],
)
def test_invalid_settings(settings_path, mutator):
    settings_path.write_text(mutator(settings_path.read_text(encoding="utf-8")), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_invalid_toml(settings_path):
    settings_path.write_text("not = valid toml [[[", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(settings_path)


def test_precip_window(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    start, end = precip_window(settings)
    assert end == fixed
    assert start == fixed - timedelta(days=settings.watering.past_days - 1)
    assert past_window(settings) == (start, end)


def test_forecast_and_timeline_windows(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    assert forecast_window(settings) == (date(2024, 6, 11), date(2024, 6, 12))
    assert timeline_window(settings) == (date(2024, 6, 8), date(2024, 6, 12))

    no_forecast = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"forecast_days": 0})}
    )
    assert forecast_window(no_forecast) is None
    assert timeline_window(no_forecast) == past_window(no_forecast)


def test_allow_watering(settings):
    assert allow_watering(_totals(1.5, 0.5, 0.0), settings) is True
    assert allow_watering(_totals(1.5001, 0.0, 0.0), settings) is False
    assert allow_watering(_totals(0.0, 0.5001, 0.0), settings) is False
    assert allow_watering(_totals(1.5, 0.5001, 0.0), settings) is False
    assert allow_watering(_totals(0.5, 0.0, 0.25), settings) is False


def test_past_ok_event_and_cumulative(settings):
    assert past_ok(_totals(1.5, 0, 0.24), settings) is True
    assert past_ok(_totals(1.5001, 0, 0.0), settings) is False
    assert past_ok(_totals(0.5, 0, 0.25), settings) is False

    no_event = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"event_inches": 0})}
    )
    assert past_ok(_totals(0.5, 0, 0.5), no_event) is True


def test_rain_delay_helpers(settings):
    today = date(2024, 6, 10)
    assert update_blocked_until(
        today, past_ok_flag=False, rain_delay_days=1, blocked_until=None
    ) == date(2024, 6, 11)
    assert update_blocked_until(
        today, past_ok_flag=False, rain_delay_days=1, blocked_until=date(2024, 6, 12)
    ) == date(2024, 6, 12)
    assert update_blocked_until(
        date(2024, 6, 13), past_ok_flag=True, rain_delay_days=1, blocked_until=date(2024, 6, 12)
    ) is None
    assert (
        update_blocked_until(
            today, past_ok_flag=True, rain_delay_days=0, blocked_until=date(2024, 6, 12)
        )
        is None
    )
    assert watering_required(today, True, date(2024, 6, 11)) is False
    assert watering_required(date(2024, 6, 12), True, date(2024, 6, 11)) is True


def test_seconds_until_next_check(settings):
    scheduled = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"updates_per_day": 1})}
    )
    tz = ZoneInfo("America/Chicago")
    before = datetime(2024, 6, 10, 3, 0, tzinfo=tz)
    assert seconds_until_next_check(scheduled, now=before) == pytest.approx(90 * 60)
    after = datetime(2024, 6, 10, 5, 0, tzinfo=tz)
    assert seconds_until_next_check(scheduled, now=after) == pytest.approx(23.5 * 3600)
    interval = settings.watering.interval_seconds
    assert seconds_until_next_check(settings, now=before) == pytest.approx(interval)
    naive = datetime(2024, 6, 10, 3, 0)
    assert seconds_until_next_check(scheduled, now=naive) == pytest.approx(90 * 60)


def test_max_daily_precip():
    days = [
        {"datetime": "2024-06-01", "precip": 0.1},
        {"datetime": "2024-06-02", "precip": 0.4},
        {"precip": 9.0},
    ]
    assert _max_daily_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.4)


def test_sum_precip_sums_inches():
    days = [{"datetime": "2024-06-01", "precip": 1.0}, {"datetime": "2024-06-02", "precip": 0.5}]
    assert _sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(1.5)


def test_sum_precip_treats_null_as_zero():
    days = [{"datetime": "2024-06-01", "precip": None}, {"datetime": "2024-06-02", "precip": 0.25}]
    assert _sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(0.25)


def test_sum_precip_excludes_outside_window():
    days = [
        {"datetime": "2024-06-01", "precip": 1.0},
        {"datetime": "2024-06-02", "precip": 0.5},
        {"datetime": "2024-06-03", "precip": 9.0},
    ]
    assert _sum_precip(days, date(2024, 6, 1), date(2024, 6, 2)) == pytest.approx(1.5)


def test_sum_precip_empty_days_raises():
    with pytest.raises(requests.RequestException, match="no daily rows"):
        _sum_precip([], date(2024, 6, 1), date(2024, 6, 2))


def test_sum_precip_missing_datetime_raises():
    days = [{"precip": 1.0}]
    with pytest.raises(requests.RequestException, match="missing datetime"):
        _sum_precip(days, date(2024, 6, 1), date(2024, 6, 1))


def test_sum_precip_day_count_mismatch_logs_warning(settings, caplog):
    days = [{"datetime": "2024-06-01", "precip": 0.5}]
    with caplog.at_level("WARNING"):
        total = _sum_precip(days, date(2024, 6, 1), date(2024, 6, 7))
    assert total == pytest.approx(0.5)
    assert "expected 7" in caplog.text


def test_decide_at_threshold(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(1.5, 0.5, 0.0))
    required, rainfall, forecast, blocked_until, _, _ = decide(settings, State())
    assert rainfall == pytest.approx(1.5)
    assert forecast == pytest.approx(0.5)
    assert blocked_until is None
    assert required is True


def test_decide_just_over_threshold(settings, monkeypatch):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(1.5001, 0.0, 0.0))
    required, _, _, blocked_until, _, _ = decide(settings, State())
    assert required is False
    assert blocked_until == date(2024, 6, 11)


def test_decide_blocked_by_event_only(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.5, 0.0, 0.25))
    required, rainfall, _, blocked_until, _, _ = decide(settings, State())
    assert rainfall == pytest.approx(0.5)
    assert required is False
    assert blocked_until is not None


def test_decide_rain_delay_overrides_dry_weather(settings, monkeypatch, caplog):
    fixed = date(2024, 6, 10)
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: fixed)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.0, 0.0, 0.0))
    state = State(blocked_until=date(2024, 6, 11))
    with caplog.at_level("INFO"):
        required, _, _, blocked_until, _, _ = decide(settings, state)
    assert required is False
    assert blocked_until == date(2024, 6, 11)
    assert "rain delay active" in caplog.text


def test_decide_blocked_by_forecast_only(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.0, 0.51, 0.0))
    required, rainfall, forecast, blocked_until, _, _ = decide(settings, State())
    assert rainfall == pytest.approx(0.0)
    assert forecast == pytest.approx(0.51)
    assert required is False
    assert blocked_until is None


def test_state_load_missing(tmp_path):
    assert State.load(tmp_path / "missing.json") == State()


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    state = State(
        last_weather_update=123.0,
        watering_required=True,
        rainfall_inches=0.25,
        forecast_inches=0.1,
        blocked_until=date(2024, 6, 15),
    )
    state.save(path)
    loaded = State.load(path)
    assert loaded.rainfall_inches == pytest.approx(0.25)
    assert loaded.forecast_inches == pytest.approx(0.1)
    assert loaded.blocked_until == date(2024, 6, 15)


def test_in_season(settings):
    season = settings.season
    assert in_season(season, date(2024, 6, 1)) is True
    assert in_season(season, date(2024, 2, 1)) is False
    assert in_season(season, date(2024, 5, 15)) is True


def test_out_of_season(settings):
    winter = settings.model_copy(
        update={"season": Season(start_month=12, start_day=1, end_month=12, end_day=31)}
    )
    required, rainfall, _, blocked_until, in_season_flag, error = decide(winter, State())
    assert required is False and rainfall is None and blocked_until is None
    assert in_season_flag is False and error is None


def test_fail_mode_keep_last_state(settings, monkeypatch):
    settings = settings.model_copy(
        update={
            "runtime": settings.runtime.model_copy(update={"fail_mode": FailMode.KEEP_LAST_STATE})
        }
    )
    state = State(watering_required=True, rainfall_inches=0.1, forecast_inches=0.05)
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    required, _, forecast, blocked_until, _, _ = decide(settings, state)
    assert required is True
    assert forecast == pytest.approx(0.05)
    assert blocked_until is None


def test_fail_safe(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", _weather_error)
    required, _, _, blocked_until, _, error = decide(
        settings, State(watering_required=True, rainfall_inches=0.1)
    )
    assert required is False
    assert blocked_until is None
    assert error == "x"


def test_watering_required(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.1))
    required, rainfall, _, blocked_until, _, error = decide(settings, State())
    assert required is True
    assert rainfall == pytest.approx(0.1)
    assert blocked_until is None
    assert error is None


def test_watering_blocked(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(2.0, 0.0, 2.0))
    required, _, _, blocked_until, _, _ = decide(settings, State())
    assert required is False
    assert blocked_until is not None


def _timeline_days(
    settings, today: date, *, past: list[float], forecast: list[float]
) -> list[dict]:
    past_start = today - timedelta(days=settings.watering.past_days - 1)
    days: list[dict] = []
    cursor = past_start
    for amount in past:
        days.append({"datetime": cursor.isoformat(), "precip": amount})
        cursor += timedelta(days=1)
    cursor = today + timedelta(days=1)
    for amount in forecast:
        days.append({"datetime": cursor.isoformat(), "precip": amount})
        cursor += timedelta(days=1)
    return days


@responses.activate
def test_fetch_precip(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    past_start, past_end = past_window(settings)
    forecast_start, forecast_end = forecast_window(settings)
    assert forecast_start is not None and forecast_end is not None
    payload = {
        "queryCost": 7,
        "timezone": settings.location.timezone,
        "days": _timeline_days(settings, today, past=[0.25, 0.0, 0.35], forecast=[0.1, 0.05]),
    }
    url = _timeline_url(settings, api_start, api_end)
    responses.add(responses.GET, url, json=payload, status=200)
    totals = fetch_precip(settings)
    assert totals.past_inches == pytest.approx(
        _sum_precip(payload["days"], past_start, past_end)
    )
    assert totals.forecast_inches == pytest.approx(
        _sum_precip(payload["days"], forecast_start, forecast_end)
    )
    assert totals.past_inches == pytest.approx(0.6)
    assert totals.forecast_inches == pytest.approx(0.15)
    assert totals.max_daily_inches == pytest.approx(0.35)
    params = responses.calls[0].request.params
    assert params["key"] == settings.weather.api_key
    assert params["unitGroup"] == TIMELINE_QUERY["unitGroup"]
    assert params["elements"] == TIMELINE_QUERY["elements"]
    assert params["include"] == TIMELINE_QUERY["include"]


@responses.activate
def test_fetch_precip_logs_query_cost_at_debug(settings, monkeypatch, caplog):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = _timeline_url(settings, api_start, api_end)
    responses.add(
        responses.GET,
        url,
        json={
            "queryCost": 7,
            "timezone": settings.location.timezone,
            "days": [{"datetime": today.isoformat(), "precip": 0.0}],
        },
        status=200,
    )
    with caplog.at_level("DEBUG"):
        fetch_precip(settings)
    assert "queryCost=7" in caplog.text


@responses.activate
def test_fetch_precip_timezone_mismatch_warns(settings, monkeypatch, caplog):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = _timeline_url(settings, api_start, api_end)
    responses.add(
        responses.GET,
        url,
        json={
            "timezone": "America/New_York",
            "days": [{"datetime": today.isoformat(), "precip": 0.0}],
        },
        status=200,
    )
    with caplog.at_level("WARNING"):
        fetch_precip(settings)
    assert "differs from visual crossing" in caplog.text


@responses.activate
def test_fetch_precip_missing_days(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = _timeline_url(settings, api_start, api_end)
    responses.add(responses.GET, url, json={"days": "bad"}, status=200)
    with pytest.raises(requests.RequestException, match="missing daily data"):
        fetch_precip(settings)


@responses.activate
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (401, "unauthorized"),
        (429, "rate limit"),
        (500, "HTTP 500"),
    ],
)
def test_fetch_precip_http_errors(settings, monkeypatch, status, message):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = _timeline_url(settings, api_start, api_end)
    responses.add(responses.GET, url, status=status)
    with pytest.raises(requests.RequestException, match=message):
        fetch_precip(settings)


@responses.activate
def test_fetch_precip_invalid_json(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    api_start, api_end = timeline_window(settings)
    url = _timeline_url(settings, api_start, api_end)
    responses.add(responses.GET, url, body="not json", status=200)
    with pytest.raises(requests.RequestException, match="invalid JSON"):
        fetch_precip(settings)


@responses.activate
def test_fetch_precip_no_forecast_window(settings, monkeypatch):
    today = date.today()
    monkeypatch.setattr("rain_bypass.app.local_today", lambda _loc: today)
    no_forecast = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"forecast_days": 0})}
    )
    past_start, past_end = past_window(no_forecast)
    api_start, api_end = timeline_window(no_forecast)
    assert api_start == past_start and api_end == past_end
    payload = {
        "timezone": settings.location.timezone,
        "days": _timeline_days(no_forecast, today, past=[0.2, 0.1, 0.3], forecast=[]),
    }
    url = _timeline_url(no_forecast, api_start, api_end)
    responses.add(responses.GET, url, json=payload, status=200)
    totals = fetch_precip(no_forecast)
    assert totals.past_inches == pytest.approx(0.6)
    assert totals.forecast_inches == pytest.approx(0.0)


def test_fetch_precip_connection_error(settings, monkeypatch):
    def _down(*_args, **_kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("rain_bypass.app.requests.get", _down)
    with pytest.raises(requests.ConnectionError, match="down"):
        fetch_precip(settings)


def test_run_once(tmp_path, settings_path):
    state_path = tmp_path / "state.json"
    settings_path.write_text(
        settings_path.read_text(encoding="utf-8")
        .replace('state_path = "state.json"', f'state_path = "{state_path.as_posix()}"')
        .replace("start_month = 5", "start_month = 12"),
        encoding="utf-8",
    )
    settings = load_settings(settings_path)
    run(settings, once=True, pin_factory=_noop_pins)
    saved = State.load(state_path)
    assert saved.watering_required is False
    assert saved.last_weather_update is not None


def test_run_loop(settings, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.0))

    def _stop(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.app.time.sleep", _stop)
    with pytest.raises(KeyboardInterrupt):
        run(settings, pin_factory=_noop_pins)


def test_run_loop_uses_scheduled_check(settings, monkeypatch):
    scheduled = settings.model_copy(
        update={"watering": settings.watering.model_copy(update={"updates_per_day": 1})}
    )
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.0))
    monkeypatch.setattr("rain_bypass.app.seconds_until_next_check", lambda _s: 42.0)

    def _stop(seconds):
        assert seconds == pytest.approx(42.0)
        raise KeyboardInterrupt

    monkeypatch.setattr("rain_bypass.app.time.sleep", _stop)
    with pytest.raises(KeyboardInterrupt):
        run(scheduled, pin_factory=_noop_pins)


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
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: _totals(0.0))
    assert main(["--config", str(settings_path), "--once"]) == 0


def test_main_keyboard_interrupt(settings_path):
    with patch("rain_bypass.app.run", side_effect=KeyboardInterrupt):
        assert main(["--config", str(settings_path)]) == 0


def test_main_fatal_error(settings_path):
    with patch("rain_bypass.app.run", side_effect=RuntimeError("boom")):
        assert main(["--config", str(settings_path)]) == 1
