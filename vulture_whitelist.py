"""Vulture false positives — Typer entry points, Pydantic hooks, HTTP handlers."""

import logging

import rain_bypass.cli as cli
import rain_bypass.config as config
import rain_bypass.install_cli as install_cli
import rain_bypass.weather as weather

cli.history
cli.serve
install_cli.install
install_cli.configure
install_cli.setup_autoupdate
config.Settings.normalize_zip_code
config.BalanceSettings.merge_monthly_overrides
config.RuntimeSettings.normalize_log_level
weather.timeline_request_params
logging.LogRecord.msg  # filter API on logging.LogRecord
