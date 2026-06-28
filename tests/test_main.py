from unittest.mock import patch

from rain_bypass.__main__ import main


def test_main_once(settings_path, monkeypatch):
    monkeypatch.setattr("rain_bypass.app.fetch_precip", lambda _s: 0.0)
    assert main(["--config", str(settings_path), "--once"]) == 0


def test_main_keyboard_interrupt(settings_path):
    with patch("rain_bypass.__main__.run", side_effect=KeyboardInterrupt):
        assert main(["--config", str(settings_path)]) == 0


def test_main_fatal_error(settings_path):
    with patch("rain_bypass.__main__.run", side_effect=RuntimeError("boom")):
        assert main(["--config", str(settings_path)]) == 1
