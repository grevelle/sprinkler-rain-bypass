from __future__ import annotations

from pathlib import Path

_PI_MODEL = Path("/proc/device-tree/model")


def read_pi_model() -> str | None:
    if not _PI_MODEL.is_file():
        return None
    return _PI_MODEL.read_text(encoding="utf-8", errors="ignore").strip("\0")


def is_raspberry_pi() -> bool:
    model = read_pi_model()
    if model is None:
        return False
    return "raspberry" in model.lower()


def is_pi_zero() -> bool:
    model = read_pi_model()
    if model is None:
        return False
    return "zero" in model.lower()
