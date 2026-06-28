from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from rain_bypass.models import FailMode, RuntimeState, SeasonSettings, Settings, WateringDecision
from rain_bypass.weather import WeatherError, build_weather_client, fetch_precipitation_inches

logger = logging.getLogger(__name__)


def is_in_watering_season(season: SeasonSettings, today: date) -> bool:
    start = date(today.year, season.start_month, season.start_day)
    end = date(today.year, season.end_month, season.end_day)
    if start <= end:
        return start <= today <= end
    # Season crosses year boundary (e.g. Nov–Feb).
    return today >= start or today <= end


def evaluate_watering(settings: Settings, state: RuntimeState) -> WateringDecision:
    timezone = ZoneInfo(settings.location.timezone)
    today = datetime.now(timezone).date()
    in_season = is_in_watering_season(settings.season, today)

    if not in_season:
        logger.info("Outside watering season (%s)", today.isoformat())
        return WateringDecision(
            watering_required=False,
            rainfall_inches=None,
            in_season=False,
            source="season",
        )

    client = build_weather_client(settings)
    try:
        rainfall = fetch_precipitation_inches(client, settings)
    except WeatherError as exc:
        logger.warning("Weather lookup failed; applying fail_mode=%s", settings.runtime.fail_mode.value)
        return _decision_from_failure(settings, state, str(exc))

    watering_required = rainfall <= settings.watering.inches_required
    logger.info(
        "Rainfall %.2f in (threshold %.2f in) -> watering %s",
        rainfall,
        settings.watering.inches_required,
        "required" if watering_required else "not required",
    )
    return WateringDecision(
        watering_required=watering_required,
        rainfall_inches=rainfall,
        in_season=True,
        source=settings.weather.provider.value,
    )


def _decision_from_failure(settings: Settings, state: RuntimeState, message: str) -> WateringDecision:
    if settings.runtime.fail_mode is FailMode.KEEP_LAST_STATE and state.watering_required is not None:
        return WateringDecision(
            watering_required=state.watering_required,
            rainfall_inches=state.rainfall_inches,
            in_season=True,
            source="last_state",
            error=message,
        )

    return WateringDecision(
        watering_required=False,
        rainfall_inches=state.rainfall_inches,
        in_season=True,
        source="fail_safe",
        error=message,
    )
