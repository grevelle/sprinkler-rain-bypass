#!/usr/bin/env bash
# Recover Pi Zero W Wi-Fi when brcmfmac hangs and LAN becomes unreachable.
# Soft-reconnect on first failure; reboot after sustained failures (~15 min at 5 min interval).
set -euo pipefail

LOG_TAG="rain-bypass-wifi-watchdog"
IFACE="${RAIN_BYPASS_WIFI_IFACE:-wlan0}"
MAX_FAILURES="${RAIN_BYPASS_WIFI_MAX_FAILURES:-3}"
BOOT_GRACE_SEC="${RAIN_BYPASS_WIFI_BOOT_GRACE_SEC:-300}"
STATE_DIR="${RAIN_BYPASS_WIFI_STATE_DIR:-/var/lib/rain-bypass}"
STATE_FILE="${STATE_DIR}/wifi-watchdog.count"
PING_WAIT_SEC="${RAIN_BYPASS_WIFI_PING_WAIT_SEC:-3}"

log() {
  printf '%s\n' "$*"
  if command -v logger >/dev/null 2>&1; then
    logger -t "${LOG_TAG}" "$*"
  fi
}

uptime_seconds() {
  local secs
  secs="$(cut -d. -f1 /proc/uptime 2>/dev/null || echo 0)"
  printf '%s\n' "${secs}"
}

read_failures() {
  if [[ -f "${STATE_FILE}" ]]; then
    local value
    value="$(tr -d '[:space:]' < "${STATE_FILE}" || true)"
    if [[ "${value}" =~ ^[0-9]+$ ]]; then
      printf '%s\n' "${value}"
      return 0
    fi
  fi
  printf '0\n'
}

write_failures() {
  mkdir -p "${STATE_DIR}"
  printf '%s\n' "$1" > "${STATE_FILE}"
}

default_gateway() {
  ip -4 route show default 2>/dev/null | awk '/default/ { print $3; exit }'
}

lan_ok() {
  local gw
  if ! ip -o link show "${IFACE}" 2>/dev/null | grep -q "state UP"; then
    return 1
  fi
  gw="$(default_gateway)"
  if [[ -z "${gw}" ]]; then
    return 1
  fi
  if command -v ping >/dev/null 2>&1; then
    if ping -c 1 -W "${PING_WAIT_SEC}" "${gw}" >/dev/null 2>&1; then
      return 0
    fi
    return 1
  fi
  # No ping binary — treat having a default route + UP iface as enough.
  return 0
}

soft_recover() {
  log "soft recover: reconnecting ${IFACE}"
  if command -v nmcli >/dev/null 2>&1; then
    nmcli device disconnect "${IFACE}" >/dev/null 2>&1 || true
    sleep 2
    nmcli device connect "${IFACE}" >/dev/null 2>&1 || true
  fi
  local iw
  iw="$(command -v iw || true)"
  if [[ -z "${iw}" && -x /sbin/iw ]]; then
    iw=/sbin/iw
  fi
  if [[ -n "${iw}" ]]; then
    "${iw}" dev "${IFACE}" set power_save off >/dev/null 2>&1 || true
  fi
}

reboot_now() {
  log "hard recover: rebooting after ${MAX_FAILURES} consecutive Wi-Fi failures"
  sleep 2
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl reboot
  else
    sudo systemctl reboot
  fi
}

main() {
  local uptime failures

  uptime="$(uptime_seconds)"
  if ((uptime < BOOT_GRACE_SEC)); then
    log "boot grace (${uptime}s < ${BOOT_GRACE_SEC}s); skipping"
    exit 0
  fi

  if lan_ok; then
    write_failures 0
    exit 0
  fi

  failures="$(read_failures)"
  failures=$((failures + 1))
  write_failures "${failures}"
  log "LAN check failed on ${IFACE} (failure ${failures}/${MAX_FAILURES})"

  if ((failures == 1)); then
    soft_recover
    sleep 5
    if lan_ok; then
      log "soft recover succeeded"
      write_failures 0
      exit 0
    fi
    log "soft recover did not restore LAN"
  fi

  if ((failures >= MAX_FAILURES)); then
    reboot_now
  fi
}

main "$@"
