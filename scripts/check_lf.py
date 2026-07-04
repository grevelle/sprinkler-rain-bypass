#!/usr/bin/env python3
"""Fail pre-commit if any git-tracked text file contains CRLF."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

BINARY_SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
}


def tracked_files(repo: Path) -> list[str]:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo)
    return [part for part in output.decode().split("\0") if part]


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    bad: list[str] = []
    for rel in tracked_files(repo):
        if Path(rel).suffix.lower() in BINARY_SUFFIXES:
            continue
        data = (repo / rel).read_bytes()
        if b"\r\n" in data or b"\r" in data:
            bad.append(rel)
    if bad:
        print("CRLF or bare CR line endings are not allowed (use LF only):", file=sys.stderr)
        for path in bad:
            print(f"  {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
