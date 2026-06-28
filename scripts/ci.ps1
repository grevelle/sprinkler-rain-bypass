# Mirror .github/workflows/ci.yml locally before you commit or push.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

python -m pip install -q -e ".[dev]"
python -m pre_commit run --all-files
python -m pyright
python -m pytest -q -m "not live" --cov=rain_bypass --cov-fail-under=100
