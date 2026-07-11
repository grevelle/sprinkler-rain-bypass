#!/usr/bin/env bash
# Mirror .github/workflows/ci.yml locally before you commit or push.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade pip
python3 -m pip install --upgrade -e ".[dev]"
ruff check .
ruff format --check .
shellcheck install.sh configure.sh scripts/auto-update.sh scripts/lib/common.sh
python3 scripts/check_lf.py
pyright
python3 -m vulture src/rain_bypass vulture_whitelist.py --min-confidence 80
pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
