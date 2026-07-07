from datetime import date

import pytest

from rain_bypass import balance
from rain_bypass.config import State, load_example_settings


@pytest.fixture
def settings():
    return load_example_settings(weather={"api_key": "test-key"}, gpio={"mock": True})


def test_month_start():
    assert balance.month_start(date(2024, 7, 15)) == date(2024, 7, 1)


def test_monthly_target_uses_builtin_table(settings):
    assert balance.monthly_target(date(2024, 6, 1), settings) == pytest.approx(6.5)
    assert balance.monthly_target(date(2024, 1, 15), settings) == pytest.approx(0.0)


def test_monthly_target_custom_override(settings):
    custom = settings.model_copy(
        update={
            "balance": settings.balance.model_copy(
                update={
                    "monthly": {
                        **settings.balance.monthly,
                        6: settings.balance.monthly[6].model_copy(
                            update={"target_inches_per_month": 8.0}
                        ),
                    }
                }
            )
        }
    )
    assert balance.monthly_target(date(2024, 6, 1), custom) == pytest.approx(8.0)


def test_target_to_date_prorates(settings):
    july_15 = date(2024, 7, 15)
    expected = 5.0 * (15 / 31)
    assert balance.target_to_date(july_15, settings) == pytest.approx(expected)


def test_compute_deficit(settings):
    today = date(2024, 7, 15)
    deficit = balance.compute_deficit(
        today, settings, rain_mtd=1.0, irrigation_mtd=0.5, forecast_inches=0.4
    )
    target = balance.target_to_date(today, settings)
    assert deficit == pytest.approx(target - 1.0 - 0.5 - 0.4)


def test_balance_allows_when_deficit_meets_cycle(settings):
    today = date(2024, 7, 15)
    assert balance.balance_allows_watering(today, settings, 0.0, 0.0, 0.0) is True


def test_balance_blocks_when_deficit_below_cycle(settings):
    today = date(2024, 7, 15)
    target = balance.target_to_date(today, settings)
    forecast = target - 0.1
    assert balance.balance_allows_watering(today, settings, 0.0, 0.0, forecast) is False


def test_balance_blocks_dormant_month(settings):
    assert balance.balance_allows_watering(date(2024, 1, 10), settings, 0.0, 0.0, 0.0) is False


def test_ensure_balance_month_resets_irrigation():
    state = State(balance_month=6, irrigation_inches_mtd=1.2)
    updated = balance.ensure_balance_month(state, date(2024, 7, 1))
    assert updated.balance_month == 7
    assert updated.irrigation_inches_mtd == pytest.approx(0.0)


def test_refresh_balance_state_credits_on_switch_on(settings):
    today = date(2024, 7, 10)
    state = State(balance_month=7, irrigation_inches_mtd=0.3)
    updated = balance.refresh_balance_state(state, today, switch_on=True, settings=settings)
    assert updated.irrigation_inches_mtd == pytest.approx(0.6)


def test_refresh_balance_state_no_credit_when_off(settings):
    today = date(2024, 7, 10)
    state = State(balance_month=7, irrigation_inches_mtd=0.3)
    updated = balance.refresh_balance_state(state, today, switch_on=False, settings=settings)
    assert updated.irrigation_inches_mtd == pytest.approx(0.3)
