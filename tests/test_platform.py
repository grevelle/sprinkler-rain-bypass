from __future__ import annotations

from rain_bypass.platform import is_pi_zero, is_raspberry_pi, read_pi_model


def test_read_pi_model_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing"
    monkeypatch.setattr("rain_bypass.platform._PI_MODEL", missing)
    assert read_pi_model() is None
    assert is_raspberry_pi() is False
    assert is_pi_zero() is False


def test_is_raspberry_pi(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.write_text("Raspberry Pi 4 Model B", encoding="utf-8")
    monkeypatch.setattr("rain_bypass.platform._PI_MODEL", model)
    assert read_pi_model() == "Raspberry Pi 4 Model B"
    assert is_raspberry_pi() is True
    assert is_pi_zero() is False


def test_is_raspberry_pi_not_pi(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.write_text("Not a Pi", encoding="utf-8")
    monkeypatch.setattr("rain_bypass.platform._PI_MODEL", model)
    assert is_raspberry_pi() is False


def test_is_pi_zero(monkeypatch, tmp_path):
    model = tmp_path / "model"
    model.write_text("Raspberry Pi Zero W Rev 1.1", encoding="utf-8")
    monkeypatch.setattr("rain_bypass.platform._PI_MODEL", model)
    assert is_pi_zero() is True
