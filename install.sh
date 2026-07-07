#!/usr/bin/env bash
# Bootstrap venv and run the Python installer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Must match rain_bypass/platform.py (runs before the venv exists).
is_raspberry_pi() {
  if [[ ! -r /proc/device-tree/model ]]; then
    return 1
  fi
  local model
  model="$(tr -d '\0' < /proc/device-tree/model)"
  [[ "${model}" == *[Rr]aspberry* ]]
}

can_create_venv() {
  local probe
  probe="$(mktemp -d "${TMPDIR:-/tmp}/rain-bypass-venv.XXXXXX")"
  if python3 -m venv "${probe}/.venv" >/dev/null 2>&1; then
    rm -rf "${probe}"
    return 0
  fi
  rm -rf "${probe}"
  return 1
}

pkg_installed() {
  dpkg -s "$1" >/dev/null 2>&1
}

collect_missing_packages() {
  MISSING_PACKAGES=()
  if ! can_create_venv; then
    MISSING_PACKAGES+=(python3-venv)
  fi
  if ! command -v git >/dev/null 2>&1; then
    MISSING_PACKAGES+=(git)
  fi
  if is_raspberry_pi; then
    if ! pkg_installed python3-dev; then
      MISSING_PACKAGES+=(python3-dev)
    fi
    if ! pkg_installed python3-lgpio; then
      MISSING_PACKAGES+=(python3-lgpio)
    fi
    if ! command -v gcc >/dev/null 2>&1; then
      MISSING_PACKAGES+=(build-essential)
    fi
  fi
}

prompt_yes() {
  local reply
  read -r -p "$1 [Y/n] " reply
  case "${reply}" in
    [nN] | [nN][oO]) return 1 ;;
    *) return 0 ;;
  esac
}

install_missing_packages() {
  collect_missing_packages
  if ((${#MISSING_PACKAGES[@]} == 0)); then
    return 0
  fi

  if ! command -v apt-get >/dev/null 2>&1; then
    printf 'error: missing required packages: %s\n' "${MISSING_PACKAGES[*]}" >&2
    printf 'Install them with your system package manager, then re-run ./install.sh\n' >&2
    exit 1
  fi

  printf 'Missing apt packages: %s\n' "${MISSING_PACKAGES[*]}"
  printf 'Command: sudo apt-get update && sudo apt-get install -y %s\n' "${MISSING_PACKAGES[*]}"
  if ! prompt_yes "Install these packages now?"; then
    printf 'error: cannot continue without required packages.\n' >&2
    exit 1
  fi

  if ! command -v sudo >/dev/null 2>&1; then
    printf 'error: sudo is required to install packages.\n' >&2
    exit 1
  fi

  sudo apt-get update
  sudo apt-get install -y "${MISSING_PACKAGES[@]}"
}

if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    printf 'error: python3 is not installed.\n' >&2
    printf 'On Raspberry Pi OS run: sudo apt-get update && sudo apt-get install -y python3\n' >&2
  else
    printf 'error: python3 is required.\n' >&2
  fi
  exit 1
fi

install_missing_packages

if [[ ! -d "${ROOT}/.venv" ]]; then
  python3 -m venv "${ROOT}/.venv"
fi

# --no-cache-dir saves SD wear/space on Pi Zero W; --upgrade always pulls latest deps.
"${ROOT}/.venv/bin/python" -m pip install -q --upgrade pip
"${ROOT}/.venv/bin/python" -m pip install -q --upgrade --no-cache-dir -e ".[gpio]"

exec "${ROOT}/.venv/bin/python" -m rain_bypass.install_cli "$@"
