"""Tests for repository maintenance scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_check_dashboard_css_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_dashboard_css.py")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_check_test_fixtures_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_test_fixtures.py")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_check_shell_functions_passes() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "check_shell_functions.py")],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_check_test_fixtures_detects_orphan(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    conftest = tests_dir / "conftest.py"
    conftest.write_text(
        "import pytest\n\n@pytest.fixture\ndef orphan_fixture():\n    return 1\n",
        encoding="utf-8",
    )
    (tests_dir / "test_x.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    script = (REPO / "scripts" / "check_test_fixtures.py").read_text(encoding="utf-8")
    patched = script.replace(
        "REPO = Path(__file__).resolve().parents[1]",
        f"REPO = Path({tmp_path.as_posix()!r})",
    )
    runner = tmp_path / "runner.py"
    runner.write_text(patched, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(runner)],
        cwd=REPO,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert "orphan_fixture" in result.stderr
