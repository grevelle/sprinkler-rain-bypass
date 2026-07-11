#!/usr/bin/env bash
# Wrapper for check_shell_functions.py (keeps CI shell entrypoint on Linux).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "${ROOT}/scripts/check_shell_functions.py" "$@"
