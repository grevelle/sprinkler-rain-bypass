#!/usr/bin/env bash
# Interactive installer for Sprinkler Rain Bypass (Raspberry Pi / Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="${ROOT}/.venv"
PYTHON="${VENV}/bin/python"
SETTINGS="${ROOT}/settings.toml"
SERVICE_NAME="rain-bypass"

info() { printf '==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

prompt() {
  local __var=$1
  local __text=$2
  local __default=$3
  local __input
  read -r -p "${__text} [${__default}]: " __input
  printf -v "$__var" '%s' "${__input:-$__default}"
}

prompt_yes_no() {
  local __var=$1
  local __text=$2
  local __default=$3
  local __hint="y/N"
  [[ "$__default" =~ ^[Yy] ]] && __hint="Y/n"
  local __input
  read -r -p "${__text} [${__hint}]: " __input
  __input=${__input:-$__default}
  case "${__input,,}" in
    y|yes) printf -v "$__var" '%s' "yes" ;;
    *) printf -v "$__var" '%s' "no" ;;
  esac
}

prompt_secret() {
  local __var=$1
  local __text=$2
  local __input
  read -r -s -p "${__text}: " __input
  printf '\n'
  [[ -n "$__input" ]] || die "API key cannot be empty."
  printf -v "$__var" '%s' "$__input"
}

require_python() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required."
  local version
  version="$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')"
  python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
    || die "Python 3.11+ required (found ${version})."
}

setup_venv() {
  if [[ ! -d "$VENV" ]]; then
    info "Creating virtual environment in .venv"
    python3 -m venv "$VENV"
  fi
  info "Installing package (gpio extra for Raspberry Pi)"
  "$PYTHON" -m pip install -q --upgrade pip
  "$PYTHON" -m pip install -q -e ".[gpio]"
}

collect_settings() {
  info "Configuration (Enter accepts the default in brackets)"
  echo

  prompt LATITUDE "Latitude (decimal degrees)" "40.7128"
  prompt LONGITUDE "Longitude (decimal degrees)" "-74.0060"
  prompt TIMEZONE 'Timezone (IANA, e.g. America/Chicago)' "America/New_York"

  echo
  info "Watering thresholds"
  prompt INCHES_REQUIRED "Rain threshold to block watering (inches)" "0.6"
  prompt PAST_DAYS "Lookback window (days)" "7"
  prompt UPDATES_PER_DAY "Weather checks per day" "1"

  echo
  info "Watering season (controller ignores rain bypass outside these dates)"
  prompt SEASON_START_MONTH "Season start month (1-12)" "3"
  prompt SEASON_START_DAY "Season start day (1-31)" "19"
  prompt SEASON_END_MONTH "Season end month (1-12)" "9"
  prompt SEASON_END_DAY "Season end day (1-31)" "12"

  echo
  info "Visual Crossing API (free key: https://www.visualcrossing.com/weather-api)"
  prompt_secret API_KEY "Visual Crossing API key"
  case "$API_KEY" in
    *[\"\\]*) die "API key contains invalid characters for settings.toml." ;;
  esac

  echo
  info "GPIO pins (BCM numbering)"
  prompt GPIO_RELAY "Relay pin" "25"
  prompt GPIO_ENABLED "Green LED pin (watering allowed)" "4"
  prompt GPIO_DISABLED "Red LED pin (watering blocked)" "27"

  local mock_default="false"
  if [[ ! -f /proc/device-tree/model ]] || ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
    mock_default="true"
    warn "Raspberry Pi not detected; defaulting gpio.mock=true for this machine."
  fi
  prompt GPIO_MOCK "Use mock GPIO (no hardware)" "$mock_default"

  echo
  info "Runtime"
  prompt FAIL_MODE "Fail mode (disable_watering | keep_last_state)" "disable_watering"
  prompt LOG_LEVEL "Log level (DEBUG | INFO | WARNING)" "INFO"
  prompt WEATHER_TIMEOUT "Weather API timeout (seconds)" "30"
}

write_settings() {
  if [[ -f "$SETTINGS" ]]; then
    local overwrite
    prompt_yes_no overwrite "settings.toml already exists. Overwrite?" "n"
    [[ "$overwrite" == "yes" ]] || die "Aborted; existing settings.toml kept."
  fi

  cat >"$SETTINGS" <<EOF
[location]
latitude = ${LATITUDE}
longitude = ${LONGITUDE}
timezone = "${TIMEZONE}"

[watering]
inches_required = ${INCHES_REQUIRED}
past_days = ${PAST_DAYS}
updates_per_day = ${UPDATES_PER_DAY}

[season]
start_month = ${SEASON_START_MONTH}
start_day = ${SEASON_START_DAY}
end_month = ${SEASON_END_MONTH}
end_day = ${SEASON_END_DAY}

[weather]
api_key = "${API_KEY}"

[gpio]
relay = ${GPIO_RELAY}
watering_enabled_led = ${GPIO_ENABLED}
watering_disabled_led = ${GPIO_DISABLED}
mock = ${GPIO_MOCK}

[runtime]
state_path = "state.json"
fail_mode = "${FAIL_MODE}"
log_level = "${LOG_LEVEL}"
weather_timeout_seconds = ${WEATHER_TIMEOUT}
EOF
  chmod 600 "$SETTINGS"
  info "Wrote ${SETTINGS}"
}

validate_settings() {
  info "Validating settings.toml"
  "$PYTHON" -c "from rain_bypass.config import load_settings; load_settings('${SETTINGS}')"
}

test_api() {
  info "Testing Visual Crossing API (one fetch)"
  "$PYTHON" <<'PY'
import os
import sys
from rain_bypass.app import fetch_precip, precip_window
from rain_bypass.config import load_settings

settings = load_settings("settings.toml")
try:
    start, end = precip_window(settings)
    inches = fetch_precip(settings)
except Exception as exc:
    print(f"API test failed: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc
print(f"API OK: {inches:.2f} in from {start} to {end}")
PY
}

run_once() {
  info "Running one control cycle (--once)"
  "$PYTHON" -m rain_bypass --config "$SETTINGS" --once
}

install_systemd() {
  command -v systemctl >/dev/null 2>&1 || {
    warn "systemctl not found; skipping service install."
    return
  }

  local unit="/etc/systemd/system/${SERVICE_NAME}.service"
  local do_service
  prompt_yes_no do_service "Install and enable systemd service (${SERVICE_NAME})?" "y"
  [[ "$do_service" == "yes" ]] || return

  local service_user="root"
  if id -u pi >/dev/null 2>&1; then
    prompt service_user "Service user (needs GPIO access on Pi; root is safest)" "root"
  fi

  info "Installing ${unit} (requires sudo)"
  sudo tee "$unit" >/dev/null <<EOF
[Unit]
Description=Sprinkler rain bypass controller
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${service_user}
WorkingDirectory=${ROOT}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON} -m rain_bypass --config ${SETTINGS}
Restart=on-failure
RestartSec=300

[Install]
WantedBy=multi-user.target
EOF

  sudo systemctl daemon-reload
  sudo systemctl enable "$SERVICE_NAME"
  sudo systemctl restart "$SERVICE_NAME"
  info "Service enabled. Status: sudo systemctl status ${SERVICE_NAME}"
}

main() {
  echo "Sprinkler Rain Bypass — installer"
  echo "Install directory: ${ROOT}"
  echo

  require_python
  setup_venv
  collect_settings
  write_settings
  validate_settings
  test_api

  local do_once
  prompt_yes_no do_once "Run a live --once cycle now?" "y"
  [[ "$do_once" == "yes" ]] && run_once

  install_systemd

  echo
  info "Done."
  echo "  Config:  ${SETTINGS}"
  echo "  Manual:  ${PYTHON} -m rain_bypass --once"
  echo "  Loop:    ${PYTHON} -m rain_bypass"
  if [[ -f "/etc/systemd/system/${SERVICE_NAME}.service" ]]; then
    echo "  Service: sudo systemctl status ${SERVICE_NAME}"
  fi
}

main "$@"
