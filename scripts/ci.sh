#!/usr/bin/env bash
# Thin wrapper — task list lives in scripts/ci.py.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec python3 scripts/ci.py "$@"
