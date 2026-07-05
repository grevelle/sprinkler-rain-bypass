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
Invoke-Step "shellcheck" { shellcheck install.sh configure.sh }
Invoke-Step "check LF" { python scripts/check_lf.py }
Invoke-Step "pyright" { python -m pyright }
Invoke-Step "pytest" {
    python -m pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
}
