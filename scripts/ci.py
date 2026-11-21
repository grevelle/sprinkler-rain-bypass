#!/usr/bin/env python3
"""Single CI task list for GitHub Actions, pre-push, and local scripts.

Usage:
  python scripts/ci.py              # install + all checks
  python scripts/ci.py --skip-install
  python scripts/ci.py --fix       # ruff check --fix + ruff format (mutates)

Dependencies stay unpinned — every install uses latest packages.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from _ci_common import REPO, shell_scripts  # noqa: E402


def _run(label: str, argv: Sequence[str]) -> None:
    print(f"==> {label}", flush=True)
    subprocess.run(list(argv), check=True, cwd=REPO)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip pip upgrade / editable install (CI already did setup).",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix with ruff instead of check-only (local convenience).",
    )
    args = parser.parse_args(argv)
    py = sys.executable
    os.chdir(REPO)
    # Prefer this interpreter's scripts dir (ruff, shellcheck-py, pyright) on PATH.
    bin_dir = str(Path(py).resolve().parent)
    os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")

    if not args.skip_install:
        _run("pip install --upgrade pip", [py, "-m", "pip", "install", "--upgrade", "pip"])
        _run(
            "pip install --upgrade -e .[dev]",
            [py, "-m", "pip", "install", "--upgrade", "-e", ".[dev]"],
        )

    if args.fix:
        _run("ruff check --fix", [py, "-m", "ruff", "check", "--fix", "."])
        _run("ruff format", [py, "-m", "ruff", "format", "."])
    else:
        _run("ruff check", [py, "-m", "ruff", "check", "."])
        _run("ruff format --check", [py, "-m", "ruff", "format", "--check", "."])

    shellcheck = shutil.which("shellcheck")
    if shellcheck is None:
        print("error: shellcheck not found on PATH", file=sys.stderr)
        return 1
    shell_args = [str(p.relative_to(REPO)).replace("\\", "/") for p in shell_scripts()]
    _run("shellcheck", [shellcheck, *shell_args])

    _run("check LF", [py, str(REPO / "scripts" / "check_lf.py")])
    _run("check dashboard CSS", [py, str(REPO / "scripts" / "check_dashboard_css.py")])
    _run("check test fixtures", [py, str(REPO / "scripts" / "check_test_fixtures.py")])
    _run("check shell functions", [py, str(REPO / "scripts" / "check_shell_functions.py")])
    _run("pyright", [py, "-m", "pyright"])
    _run(
        "vulture src",
        [
            py,
            "-m",
            "vulture",
            "src/rain_bypass",
            "vulture_whitelist.py",
            "--min-confidence",
            "80",
        ],
    )
    _run(
        "vulture tests",
        [py, "-m", "vulture", "tests", "vulture_tests_whitelist.py", "--min-confidence", "80"],
    )
    # Marker / coverage gates come from pyproject.toml addopts — keep latest tools, no pins.
    _run("pytest", [py, "-m", "pytest", "-q", "--tb=short"])
    print("==> CI OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
