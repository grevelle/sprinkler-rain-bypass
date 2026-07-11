"""Vulture false positives — Typer entry points, Pydantic hooks, HTTP handlers."""

import logging

import rain_bypass.cli as cli
import rain_bypass.config as config
import rain_bypass.install_cli as install_cli
import rain_bypass.weather as weather
import rain_bypass.web as web

# Typer invokes these by name at runtime.
cli.history
cli.serve
install_cli.install
install_cli.configure
install_cli.setup_autoupdate

# Pydantic validators and model_config hooks.
config.Settings.normalize_zip_code
config.BalanceSettings.merge_monthly_overrides
config.RuntimeSettings.normalize_log_level
config.FrozenModel.model_config
weather.TimelineDay.model_config
weather.TimelineResponse.model_config
weather.timeline_request_params

# stdlib HTTP server invokes handler methods on nested DashboardHandler.
web._make_handler().do_GET
web._make_handler().log_message

logging.LogRecord.msg  # logging filter API on logging.LogRecord
