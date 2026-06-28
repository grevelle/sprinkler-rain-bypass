from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import responses

from rain_bypass.config import load_settings
from rain_bypass.weather.open_meteo import ARCHIVE_URL, FORECAST_URL, OpenMeteoClient


@responses.activate
def test_open_meteo_sums_precipitation(example_config: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "rain_bypass.weather.open_meteo.local_today_in_timezone",
        lambda _timezone: date(2024, 6, 10),
    )
    responses.add(
        responses.GET,
        FORECAST_URL,
        json={
            "daily": {
                "time": ["2024-06-08", "2024-06-09", "2024-06-10"],
                "precipitation_sum": [2.0, 3.0, 5.0],
            }
        },
        status=200,
    )
    responses.add(
        responses.GET,
        ARCHIVE_URL,
        json={
            "daily": {
                "time": ["2024-06-08", "2024-06-09", "2024-06-10"],
                "precipitation_sum": [2.0, 3.0, 5.0],
            }
        },
        status=200,
    )

    settings = load_settings(example_config)
    total = OpenMeteoClient(timeout=5).precipitation_inches(settings, 3)
    assert total == pytest.approx((2.0 + 3.0 + 5.0) / 25.4)
