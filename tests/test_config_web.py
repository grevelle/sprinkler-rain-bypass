from __future__ import annotations

import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from conftest import mock_gather_status, status_snapshot

from rain_bypass.config import ConfigError, FailMode, load_settings, write_settings
from rain_bypass.config_html import config_values_from_settings, render_config_html
from rain_bypass.web import (
    DashboardHTTPServer,
    _make_handler,
    apply_config_form,
    build_config_page,
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, _newurl):
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _monthly_fields(settings) -> dict[str, str]:
    return {
        f"monthly_{month}": f"{settings.balance.monthly[month].target_inches_per_month:g}"
        for month in range(1, 13)
    }


def _form_from_settings(settings_path: Path, **overrides: str) -> dict[str, str]:
    settings = load_settings(settings_path)
    values = config_values_from_settings(settings)
    form = {
        "zip_code": values.zip_code,
        "api_key": "",
        "inches_per_cycle": values.inches_per_cycle,
        "check_time": values.check_time,
        "forecast_days": values.forecast_days,
        "freeze_temp_f": values.freeze_temp_f,
        "event_lookback_days": values.event_lookback_days,
        "event_inches": values.event_inches,
        "sewer_start_month": values.sewer_start_month,
        "sewer_start_day": values.sewer_start_day,
        "sewer_end_month": values.sewer_end_month,
        "sewer_end_day": values.sewer_end_day,
        "fail_mode": values.fail_mode,
        **_monthly_fields(settings),
    }
    form.update(overrides)
    return form


def test_render_config_html_sections(settings_path: Path) -> None:
    html = build_config_page(settings_path)
    assert "Configuration" in html
    assert 'action="/config"' in html
    assert "Location &amp; weather" in html
    assert "Monthly water targets" in html
    assert 'name="monthly_6"' in html
    assert "Sewer lockout" in html
    assert "Fail mode" in html
    assert 'href="/"' in html
    assert "Save settings" in html
    assert 'name="api_key"' in html
    assert 'autocomplete="postal-code"' in html
    assert 'aria-describedby="hint-api_key"' in html
    assert 'type="time"' in html
    assert "<fieldset" in html
    assert 'id="field-zip_code"' in html
    assert 'minlength="10"' not in html
    assert 'value="' in html


def test_render_config_saved_and_error_banners(settings) -> None:
    values = config_values_from_settings(settings)
    saved = render_config_html(values, saved=True)
    assert "Saved" in saved
    assert "alert-ok" in saved
    errored = render_config_html(values, error="bad zip")
    assert "Could not save" in errored
    assert "bad zip" in errored


def test_apply_config_form_keeps_blank_api_key_and_gpio(settings_path: Path, monkeypatch) -> None:
    base = load_settings(settings_path)
    form = _form_from_settings(
        settings_path,
        inches_per_cycle="0.45",
        check_time="01:30",
        forecast_days="3",
        fail_mode=FailMode.KEEP_LAST_STATE.value,
        sewer_start_month="11",
        sewer_start_day="1",
        sewer_end_month="2",
        sewer_end_day="28",
    )
    monkeypatch.setattr(
        "rain_bypass.web.resolve_location",
        lambda *_a, **_k: pytest.fail("should not resolve when zip/key unchanged"),
    )
    updated = apply_config_form(base, form)
    assert updated.weather.api_key == base.weather.api_key
    assert updated.balance.inches_per_cycle == pytest.approx(0.45)
    assert updated.watering.check_hour == 1
    assert updated.watering.check_minute == 30
    assert updated.balance.forecast_days == 3
    assert updated.runtime.fail_mode is FailMode.KEEP_LAST_STATE
    assert updated.sewer.start_month == 11
    assert updated.gpio == base.gpio
    assert updated.web == base.web
    assert updated.runtime.state_path == base.runtime.state_path
    assert updated.runtime.log_level == base.runtime.log_level


def _base_form(settings, **overrides: str) -> dict[str, str]:
    form = {
        "zip_code": settings.location.zip_code,
        "api_key": "",
        "inches_per_cycle": f"{settings.balance.inches_per_cycle:g}",
        "check_time": (f"{settings.watering.check_hour:02d}:{settings.watering.check_minute:02d}"),
        "forecast_days": str(settings.balance.forecast_days),
        "freeze_temp_f": f"{settings.watering.freeze_temp_f:g}",
        "event_lookback_days": str(settings.watering.event_lookback_days),
        "event_inches": f"{settings.watering.event_inches:g}",
        "sewer_start_month": str(settings.sewer.start_month),
        "sewer_start_day": str(settings.sewer.start_day),
        "sewer_end_month": str(settings.sewer.end_month),
        "sewer_end_day": str(settings.sewer.end_day),
        "fail_mode": settings.runtime.fail_mode.value,
        **_monthly_fields(settings),
    }
    form.update(overrides)
    return form


def test_apply_config_form_blank_key_keeps_secret(settings) -> None:
    updated = apply_config_form(settings, _base_form(settings))
    assert updated.weather.api_key == settings.weather.api_key


def test_apply_config_form_rejects_bad_zip(settings) -> None:
    with pytest.raises(ConfigError, match=r"5 characters|zip_code|ZIP|digit"):
        apply_config_form(settings, _base_form(settings, zip_code="12"))


def test_apply_config_form_rejects_short_api_key(settings) -> None:
    with pytest.raises(ConfigError, match=r"short|API"):
        apply_config_form(settings, _base_form(settings, api_key="short"))


def test_apply_config_form_rejects_non_numeric(settings) -> None:
    with pytest.raises(ConfigError, match="whole number"):
        apply_config_form(settings, _base_form(settings, forecast_days="x"))


def test_apply_config_form_rejects_float_as_int(settings) -> None:
    with pytest.raises(ConfigError, match="whole number"):
        apply_config_form(settings, _base_form(settings, forecast_days="1.5"))


def test_apply_config_form_rejects_bad_check_time(settings) -> None:
    with pytest.raises(ConfigError, match=r"HH:MM|Check time"):
        apply_config_form(settings, _base_form(settings, check_time="noon"))
    with pytest.raises(ConfigError, match=r"less than or equal to 23"):
        apply_config_form(settings, _base_form(settings, check_time="25:00"))


def test_apply_config_form_rejects_empty_and_bad_numbers(settings) -> None:
    with pytest.raises(ConfigError, match="required"):
        apply_config_form(settings, _base_form(settings, zip_code=""))
    with pytest.raises(ConfigError, match="must be a number"):
        apply_config_form(settings, _base_form(settings, event_inches="abc"))
    with pytest.raises(ConfigError, match="month must be 1-12"):
        apply_config_form(settings, _base_form(settings, sewer_start_month="0"))


def test_apply_config_form_rejects_invalid_sewer_day(settings) -> None:
    with pytest.raises(ConfigError, match=r"not valid for month"):
        apply_config_form(
            settings,
            _base_form(settings, sewer_start_month="2", sewer_start_day="30"),
        )


def test_apply_config_form_rejects_negative_monthly(settings) -> None:
    with pytest.raises(ConfigError, match=r"cannot be negative"):
        apply_config_form(settings, _base_form(settings, monthly_6="-1"))


def test_apply_config_form_updates_monthly_targets(settings) -> None:
    updated = apply_config_form(settings, _base_form(settings, monthly_6="8.5"))
    assert updated.balance.monthly[6].target_inches_per_month == pytest.approx(8.5)
    assert updated.balance.monthly[7].target_inches_per_month == pytest.approx(
        settings.balance.monthly[7].target_inches_per_month
    )


def test_apply_config_form_rejects_bad_fail_mode(settings) -> None:
    with pytest.raises(ConfigError, match="Fail mode"):
        apply_config_form(settings, _base_form(settings, fail_mode="nope"))


def test_apply_config_form_weather_error(settings, monkeypatch) -> None:
    from rain_bypass.exceptions import WeatherError

    monkeypatch.setattr(
        "rain_bypass.web.resolve_location",
        lambda *_a, **_k: (_ for _ in ()).throw(WeatherError("lookup failed")),
    )
    with pytest.raises(ConfigError, match="lookup failed"):
        apply_config_form(settings, _base_form(settings, zip_code="53186"))


def test_apply_config_form_validation_error(settings) -> None:
    with pytest.raises(ConfigError):
        apply_config_form(settings, _base_form(settings, event_lookback_days="0"))


def test_apply_config_form_model_copy_validation(settings, monkeypatch) -> None:
    from pydantic import ValidationError

    def boom(*_a, **_k):
        raise ValidationError.from_exception_data(
            "Settings",
            [
                {
                    "type": "greater_than_equal",
                    "loc": ("balance", "forecast_days"),
                    "input": -1,
                    "ctx": {"ge": 0},
                }
            ],
        )

    monkeypatch.setattr(type(settings), "model_copy", boom)
    with pytest.raises(ConfigError):
        apply_config_form(settings, _base_form(settings))


def test_config_http_post_not_found(settings_path: Path) -> None:
    port = _free_port()
    server = DashboardHTTPServer(("127.0.0.1", port), _make_handler())
    server.settings_path = settings_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/nope",
            data=b"a=1",
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_apply_config_form_resolves_new_zip(settings, monkeypatch) -> None:
    from rain_bypass.config import Location

    resolved = Location(
        zip_code="53186",
        latitude=42.99,
        longitude=-88.23,
        timezone="America/Chicago",
    )
    calls: list[tuple[str, str]] = []

    def fake_resolve(zip_code: str, api_key: str, **_k: object) -> Location:
        calls.append((zip_code, api_key))
        return resolved

    monkeypatch.setattr("rain_bypass.web.resolve_location", fake_resolve)
    updated = apply_config_form(settings, _base_form(settings, zip_code="53186"))
    assert calls == [("53186", settings.weather.api_key)]
    assert updated.location == resolved


def test_config_http_get_and_post(settings, settings_path: Path, monkeypatch) -> None:
    mock_gather_status(monkeypatch, status_snapshot(settings))
    monkeypatch.setattr(
        "rain_bypass.web.resolve_location",
        lambda *_a, **_k: pytest.fail("unexpected resolve"),
    )
    port = _free_port()
    server = DashboardHTTPServer(("127.0.0.1", port), _make_handler())
    server.settings_path = settings_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/config") as response:
            body = response.read().decode("utf-8")
        assert response.status == 200
        assert "Save settings" in body
        assert 'href="/"' in body

        form = _form_from_settings(settings_path, inches_per_cycle="0.55")
        data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/config",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.build_opener(_NoRedirect).open(request)
        assert exc_info.value.code == 303
        assert exc_info.value.headers.get("Location") == "/config?saved=1"

        reloaded = load_settings(settings_path)
        assert reloaded.balance.inches_per_cycle == pytest.approx(0.55)
        assert reloaded.gpio.mock is True
        assert reloaded.weather.api_key == settings.weather.api_key
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_config_http_post_validation_error(settings_path: Path) -> None:
    port = _free_port()
    server = DashboardHTTPServer(("127.0.0.1", port), _make_handler())
    server.settings_path = settings_path
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        form = _form_from_settings(settings_path, zip_code="bad")
        data = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/config",
            data=data,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request)
        assert exc_info.value.code == 400
        body = exc_info.value.read().decode("utf-8")
        assert "Could not save" in body
        assert 'value="bad"' in body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_dashboard_links_to_config(settings, settings_path: Path, monkeypatch) -> None:
    from rain_bypass.dashboard_html import render_dashboard_html
    from rain_bypass.web import build_dashboard_view

    mock_gather_status(monkeypatch, status_snapshot(settings))
    html = render_dashboard_html(build_dashboard_view(settings_path))
    assert 'href="/config"' in html
    assert "site-nav" in html


def test_write_settings_preserves_round_trip_after_apply(settings_path: Path, monkeypatch) -> None:
    base = load_settings(settings_path)
    form = _form_from_settings(settings_path, event_inches="0.4", monthly_6="9.0")
    monkeypatch.setattr(
        "rain_bypass.web.resolve_location",
        lambda *_a, **_k: pytest.fail("unexpected resolve"),
    )
    updated = apply_config_form(base, form)
    write_settings(settings_path, updated)
    reloaded = load_settings(settings_path)
    assert reloaded.watering.event_inches == pytest.approx(0.4)
    assert reloaded.balance.monthly[6].target_inches_per_month == pytest.approx(9.0)
    assert reloaded.location.zip_code == base.location.zip_code
