from datetime import date

import pytest
import responses

from rain_bypass.weather import ARCHIVE_URL, FORECAST_URL, fetch_precip


@responses.activate
def test_fetch_precip(settings, monkeypatch):
    monkeypatch.setattr(
        "rain_bypass.weather.precip_window",
        lambda _s: (date(2024, 6, 8), date(2024, 6, 10)),
    )
    payload = {
        "daily": {
            "time": ["2024-06-08", "2024-06-09", "2024-06-10"],
            "precipitation_sum": [2.0, 3.0, 5.0],
        }
    }
    responses.add(responses.GET, FORECAST_URL, json=payload, status=200)
    responses.add(responses.GET, ARCHIVE_URL, json=payload, status=200)
    assert fetch_precip(settings) == pytest.approx(10 / 25.4)
