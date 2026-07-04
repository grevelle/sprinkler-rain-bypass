#!/usr/bin/env bash
# Re-prompt API key and ZIP; skip apt/pip (fast path after initial ./install.sh).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "${PYTHON}" ]]; then
  printf 'error: %s not found. Run ./install.sh once to create the venv.\n' "${PYTHON}" >&2
  exit 1
fi

exec "${PYTHON}" -m rain_bypass.install_cli configure "$@"
