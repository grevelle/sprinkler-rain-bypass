"""Shared paths for local/CI checks. Keep shell targets in one place."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def shell_scripts() -> list[Path]:
    """Shell files ShellCheck and the orphan-function checker should cover."""
    ordered = [
        REPO / "install.sh",
        REPO / "configure.sh",
        REPO / "scripts" / "lib" / "common.sh",
        *sorted((REPO / "scripts").glob("*.sh")),
    ]
    seen: set[Path] = set()
    out: list[Path] = []
    for path in ordered:
        resolved = path.resolve()
        if path.is_file() and resolved not in seen:
            seen.add(resolved)
            out.append(path)
    return out


def shell_script_args() -> list[str]:
    return [str(path.relative_to(REPO)).replace("\\", "/") for path in shell_scripts()]
