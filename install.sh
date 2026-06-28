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
  printf 'error: Python 3.11+ required.\n' >&2
  exit 1
fi

if [[ ! -d "${ROOT}/.venv" ]]; then
  python3 -m venv "${ROOT}/.venv"
fi

"${ROOT}/.venv/bin/python" -m pip install -q --upgrade pip
"${ROOT}/.venv/bin/python" -m pip install -q -e ".[gpio,install]"

exec "${ROOT}/.venv/bin/python" -m rain_bypass.install_cli "$@"
