#!/usr/bin/env bash
# Bootstrap venv and run the Python installer.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  printf 'error: python3 is required.\n' >&2
  exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  printf 'error: Python 3.11+ required (Raspberry Pi OS Bookworm or newer).\n' >&2
  exit 1
fi

if [[ -r /proc/device-tree/model ]]; then
  MODEL="$(tr -d '\0' < /proc/device-tree/model)"
  if [[ "${MODEL}" == *[Zz]ero* ]]; then
    printf 'Pi Zero detected — pip install may take 10–20 minutes.\n'
  fi
fi

if [[ ! -d "${ROOT}/.venv" ]]; then
  python3 -m venv "${ROOT}/.venv"
fi

# --no-cache-dir saves SD wear/space on Pi Zero W; install is infrequent.
"${ROOT}/.venv/bin/python" -m pip install -q --upgrade pip
"${ROOT}/.venv/bin/python" -m pip install -q --no-cache-dir -e ".[gpio]"

exec "${ROOT}/.venv/bin/python" -m rain_bypass.install_cli "$@"
