#!/usr/bin/env python3
"""Fail CI if shell scripts define functions that are never called."""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CALLERS: dict[Path, tuple[Path, ...]] = {
    REPO / "scripts/lib/common.sh": (
        REPO / "install.sh",
        REPO / "scripts/auto-update.sh",
    ),
}

SKIP_WORDS = frozenset(
    {
        "if",
        "then",
        "else",
        "fi",
        "local",
        "return",
        "echo",
        "printf",
        "sudo",
        "apt",
        "get",
        "install",
        "command",
        "read",
        "case",
        "esac",
        "do",
        "done",
        "for",
        "in",
        "set",
        "cd",
        "exec",
        "source",
        "true",
        "false",
        "shift",
    }
)

DEF_RE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\(\) \{")
WORD_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")


def function_defs(path: Path) -> list[str]:
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = DEF_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def combined_text(path: Path) -> str:
    chunks = [path.read_text(encoding="utf-8")]
    for extra in CALLERS.get(path, ()):
        chunks.append(extra.read_text(encoding="utf-8"))
    return "\n".join(chunks)


def word_counts(text: str) -> Counter[str]:
    return Counter(WORD_RE.findall(text))


def main() -> int:
    files = [
        REPO / "install.sh",
        REPO / "scripts/auto-update.sh",
        REPO / "scripts/lib/common.sh",
    ]
    failed = False
    for path in files:
        text = combined_text(path)
        counts = word_counts(text)
        for fn in function_defs(path):
            if fn in SKIP_WORDS or counts.get(fn, 0) < 2:
                print(f"unused shell function: {fn} in {path}", file=sys.stderr)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
