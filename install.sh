#!/usr/bin/env bash
# Interactive installer for Sprinkler Rain Bypass (Raspberry Pi / Linux).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

VENV="${ROOT}/.venv"
PYTHON="${VENV}/bin/python"
SETTINGS="${ROOT}/settings.toml"
SERVICE_NAME="rain-bypass"

# Watering defaults (research-backed; user can override at prompts):
# - past_days=3: Hunter Hydrawise uses a 3-day cumulative rainfall window.
# - inches_required=1.5: Hydrawise default "skip if >1.5 in over 3 days" (cumulative).
#   UF/IFAS physical rain sensors use ~1/4 in per event; this app sums recent days instead.
# - updates_per_day=1: once daily matches smart-controller weather checks and saves API quota.
# Location defaults: Hartland, WI 53029 (Waukesha County zip centroid).
# Frost dates (Almanac / nearest Waukesha station): last spring ~May 7, first fall ~Oct 7.
# - season May 7–Oct 7: Hartland turf irrigation window aligned to local frost normals.
DEFAULT_LATITUDE="43.106"
DEFAULT_LONGITUDE="-88.351"
DEFAULT_TIMEZONE="America/Chicago"
DEFAULT_INCHES_REQUIRED="1.5"
DEFAULT_PAST_DAYS="3"
DEFAULT_UPDATES_PER_DAY="1"
DEFAULT_SEASON_START_MONTH="5"
DEFAULT_SEASON_START_DAY="7"
DEFAULT_SEASON_END_MONTH="10"
DEFAULT_SEASON_END_DAY="7"

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

  info "Location (53029 Hartland, WI defaults — change if your controller is elsewhere)"
  prompt LATITUDE "Latitude (decimal degrees)" "$DEFAULT_LATITUDE"
  prompt LONGITUDE "Longitude (decimal degrees)" "$DEFAULT_LONGITUDE"
  prompt TIMEZONE 'Timezone (IANA, e.g. America/Chicago)' "$DEFAULT_TIMEZONE"

  echo
  info "Watering thresholds"
  echo "  Defaults follow smart-controller practice: block when recent cumulative rain"
  echo "  exceeds the threshold over the lookback window (Hunter Hydrawise: 1.5 in / 3 days)."
  echo "  UF/IFAS recommends ~1/4 in per rain event on physical sensors; lower values save more water."
  prompt INCHES_REQUIRED "Block watering if total rain exceeds (inches)" "$DEFAULT_INCHES_REQUIRED"
  prompt PAST_DAYS "Sum rain over this many days (lookback window)" "$DEFAULT_PAST_DAYS"
  prompt UPDATES_PER_DAY "Weather checks per day (1 is usually enough)" "$DEFAULT_UPDATES_PER_DAY"

  echo
  info "Watering season (rain bypass active only between these dates)"
  echo "  Default May 7–Oct 7 matches Hartland (53029) average frost dates."
  prompt SEASON_START_MONTH "Season start month (1-12)" "$DEFAULT_SEASON_START_MONTH"
  prompt SEASON_START_DAY "Season start day (1-31)" "$DEFAULT_SEASON_START_DAY"
  prompt SEASON_END_MONTH "Season end month (1-12)" "$DEFAULT_SEASON_END_MONTH"
  prompt SEASON_END_DAY "Season end day (1-31)" "$DEFAULT_SEASON_END_DAY"

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
