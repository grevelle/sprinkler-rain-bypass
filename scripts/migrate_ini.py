#!/usr/bin/env python3
from __future__ import annotations

import configparser
from pathlib import Path


def migrate(legacy_path: Path, output_path: Path) -> None:
    parser = configparser.ConfigParser()
    parser.read(legacy_path)
    user, pins = parser["UserInput"], parser["GPIO.Pins"]
    output_path.write_text(
        f"""# Migrated from settings.ini — set latitude and longitude before use.

[location]
latitude = 0.0
longitude = 0.0
timezone = "{user.get("timezone", "UTC")}"

[watering]
inches_required = {user.get("inchesrequired", "0.6")}
past_days = {user.get("raindays", "7")}
updates_per_day = {user.get("weatherupdatesperday", "1")}

[season]
start_month = {user.get("firstmonthtowater", "3")}
start_day = {user.get("firstdaytowater", "19")}
end_month = {user.get("lastmonthtowater", "9")}
end_day = {user.get("lastdaytowater", "12")}

[weather]
provider = "visual_crossing"
visual_crossing_api_key = "{user.get("visualcrossingkey", "")}"

[gpio]
relay = {pins.get("relayswitch", "25")}
watering_enabled_led = {pins.get("wateringenabled", "4")}
watering_disabled_led = {pins.get("wateringdisabled", "27")}

[runtime]
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    migrate(Path("settings.ini"), Path("settings.toml"))
    print("Wrote settings.toml — set latitude and longitude before running.")
