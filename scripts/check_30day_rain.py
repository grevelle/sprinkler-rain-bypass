"""One-off: verify Visual Crossing returns 30 days of daily precip."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from rain_bypass.config import load_settings
from rain_bypass.weather import (
    _get_timeline,
    daily_precip_values,
    sum_precip,
    timeline_url_for,
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    settings_path = root / "settings.toml"
    if not settings_path.is_file():
        settings_path = Path("settings.toml")
    settings = load_settings(settings_path)

    today = date.today()
    start = today - timedelta(days=29)
    end = today
    expected = (end - start).days + 1

    url = timeline_url_for(settings, start, end)
    print(f"ZIP: {settings.location.zip_code}")
    print(f"URL: {url}")
    print(f"Window: {start} .. {end} ({expected} days)")

    payload = _get_timeline(
        settings,
        start,
        end,
        api_key=settings.weather.api_key,
        timeout=settings.runtime.weather_timeout_seconds,
    )
    days = payload.get("days")
    if not isinstance(days, list):
        print("FAIL: response has no days list")
        return

    print(f"Rows returned: {len(days)}")
    if days:
        print(f"First day: {str(days[0].get('datetime', ''))[:10]}")
        print(f"Last day:  {str(days[-1].get('datetime', ''))[:10]}")

    values = daily_precip_values(days, start, end)
    total = sum_precip(days, start, end)
    print(f"Matched window: {len(values)}/{expected} days")
    print(f"Total precip: {total:.2f} in")
    print(f"queryCost: {payload.get('queryCost')}")

    if len(values) == expected:
        print("OK: full 30-day daily history received.")
    else:
        print("WARN: day count mismatch — MTD sums could be incomplete.")

    print("\nLast 7 days:")
    for day in days[-7:]:
        dt = str(day.get("datetime", ""))[:10]
        precip = day.get("precip")
        print(f"  {dt}: {precip} in")


if __name__ == "__main__":
    main()
