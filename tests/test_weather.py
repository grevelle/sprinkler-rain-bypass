from datetime import date

import pytest
import responses

from rain_bypass.weather import OPEN_METEO_ARCHIVE, OPEN_METEO_FORECAST, OpenMeteo


@responses.activate
def test_open_meteo(settings, monkeypatch):
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
    responses.add(responses.GET, OPEN_METEO_FORECAST, json=payload, status=200)
    responses.add(responses.GET, OPEN_METEO_ARCHIVE, json=payload, status=200)

    total = OpenMeteo(5)(settings)
    assert total == pytest.approx(10 / 25.4)
