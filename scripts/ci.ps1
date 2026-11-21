# Thin wrapper — task list lives in scripts/ci.py.
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
python scripts/ci.py @args
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
