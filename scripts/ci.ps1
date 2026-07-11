# Mirror .github/workflows/ci.yml locally before you commit or push.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function Invoke-Step {
    param([string]$Label, [scriptblock]$Command)
    & @Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

Invoke-Step "pip install" { python -m pip install --upgrade pip }
Invoke-Step "editable install" { python -m pip install --upgrade -e ".[dev]" }
Invoke-Step "ruff check" { python -m ruff check . }
Invoke-Step "ruff format" { python -m ruff format --check . }
Invoke-Step "shellcheck" { shellcheck install.sh configure.sh scripts/auto-update.sh scripts/lib/common.sh scripts/check_shell_functions.sh }
Invoke-Step "check LF" { python scripts/check_lf.py }
Invoke-Step "check dashboard CSS" { python scripts/check_dashboard_css.py }
Invoke-Step "check test fixtures" { python scripts/check_test_fixtures.py }
Invoke-Step "check shell functions" { python scripts/check_shell_functions.py }
Invoke-Step "pyright" { python -m pyright }
Invoke-Step "vulture src" { python -m vulture src/rain_bypass vulture_whitelist.py --min-confidence 80 }
Invoke-Step "vulture tests" { python -m vulture tests vulture_tests_whitelist.py --min-confidence 80 }
Invoke-Step "pytest" {
    python -m pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
}
