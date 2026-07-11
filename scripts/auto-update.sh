#!/usr/bin/env bash
# Daily maintenance: OS packages, git pull, Python deps, service restart, reboot if needed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_TAG="rain-bypass-auto-update"
SERVICE_NAME="rain-bypass"
PYTHON="${ROOT}/.venv/bin/python"

log() {
  printf '%s\n' "$*"
  if command -v logger >/dev/null 2>&1; then
    logger -t "${LOG_TAG}" "$*"
  fi
}

restart_service() {
  if command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    if [[ "$(id -u)" -eq 0 ]]; then
      systemctl restart "${SERVICE_NAME}"
    else
      sudo systemctl restart "${SERVICE_NAME}"
    fi
    log "Restarted ${SERVICE_NAME}"
  fi
}

run_dist_upgrade() {
  if ! command -v apt-get >/dev/null 2>&1; then
    log "warning: apt-get not found; skipping OS dist-upgrade"
    return 0
  fi

  local apt_prefix=()
  if [[ "$(id -u)" -ne 0 ]]; then
    apt_prefix=(sudo)
  fi

  log "Running apt update and dist-upgrade"
  if ! "${apt_prefix[@]}" apt-get update -qq; then
    log "warning: apt-get update failed (continuing with application update)"
    return 0
  fi
  if ! DEBIAN_FRONTEND=noninteractive "${apt_prefix[@]}" apt-get dist-upgrade -y -qq; then
    log "warning: apt-get dist-upgrade failed (continuing with application update)"
    return 0
  fi
  log "OS dist-upgrade finished"
}

log "Starting daily auto-update in ${ROOT}"

run_dist_upgrade

if [[ ! -x "${PYTHON}" ]]; then
  log "error: ${PYTHON} not found; run ./install.sh first"
  exit 1
fi

cd "${ROOT}"

before_rev=""
if git rev-parse HEAD >/dev/null 2>&1; then
  before_rev="$(git rev-parse HEAD)"
fi

log "Pulling latest application code"
if git pull --ff-only origin main 2>/dev/null || git pull --ff-only 2>/dev/null; then
  :
else
  log "warning: git pull failed (continuing with local tree)"
fi

after_rev=""
if git rev-parse HEAD >/dev/null 2>&1; then
  after_rev="$(git rev-parse HEAD)"
fi

log "Upgrading Python dependencies"
"${PYTHON}" -m pip install -q --upgrade pip
"${PYTHON}" -m pip install -q --upgrade --no-cache-dir -e ".[gpio]"

if [[ -n "${before_rev}" && -n "${after_rev}" && "${before_rev}" != "${after_rev}" ]]; then
  log "Application updated (${before_rev:0:7} -> ${after_rev:0:7})"
fi

restart_service

if [[ -f /var/run/reboot-required ]]; then
  log "Kernel or libc update requires reboot; rebooting after app auto-update"
  sleep 10
  if [[ "$(id -u)" -eq 0 ]]; then
    systemctl reboot
  else
    sudo systemctl reboot
  fi
fi

log "Daily auto-update finished"
