#!/usr/bin/env bash
# Mirror .github/workflows/ci.yml locally before you commit or push.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

python3 -m pip install --upgrade pip
python3 -m pip install --upgrade -e ".[dev]"
ruff check .
ruff format --check .
shellcheck install.sh configure.sh scripts/auto-update.sh scripts/lib/common.sh scripts/check_shell_functions.sh
python3 scripts/check_lf.py
python3 scripts/check_dashboard_css.py
python3 scripts/check_test_fixtures.py
python scripts/check_shell_functions.py
pyright
python3 -m vulture src/rain_bypass vulture_whitelist.py --min-confidence 80
python3 -m vulture tests vulture_tests_whitelist.py --min-confidence 80
pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
