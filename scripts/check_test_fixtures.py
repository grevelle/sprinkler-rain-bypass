#!/usr/bin/env python3
"""Fail CI if conftest.py defines pytest fixtures that nothing references."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFTEST = REPO / "tests" / "conftest.py"
TESTS_DIR = REPO / "tests"


def _is_pytest_fixture(dec: ast.expr) -> bool:
    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
        return dec.func.attr == "fixture"
    return isinstance(dec, ast.Attribute) and dec.attr == "fixture"


def fixture_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        if any(_is_pytest_fixture(dec) for dec in node.decorator_list):
            names.append(node.name)
    return names


def reference_count(name: str) -> int:
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    count = 0
    for path in TESTS_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if path == CONFTEST and f"def {name}" in line:
                continue
            if pattern.search(line):
                count += 1
    return count


def main() -> int:
    orphans = [name for name in fixture_names(CONFTEST) if reference_count(name) == 0]
    if orphans:
        print("Unused pytest fixtures in tests/conftest.py:", file=sys.stderr)
        for name in orphans:
            print(f"  {name}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
