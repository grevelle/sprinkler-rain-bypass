#!/usr/bin/env bash
# Mirror .github/workflows/ci.yml locally before you commit or push.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m pip install -q -e ".[dev]"
pre-commit run --all-files
pyright
pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
