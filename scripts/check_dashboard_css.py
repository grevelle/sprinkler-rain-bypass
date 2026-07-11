#!/usr/bin/env python3
"""Fail CI if dashboard.css defines classes or variables not used by the dashboard HTML."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CSS_PATH = REPO / "src" / "rain_bypass" / "static" / "dashboard.css"
HTML_PATH = REPO / "src" / "rain_bypass" / "dashboard_html.py"

VERDICT_SUFFIXES = ("allow", "block", "unknown")
VERDICT_PREFIXES = ("hero-", "status-ring-", "chip-", "dot-")
BALANCE_SUFFIXES = ("need", "surplus", "even")
BALANCE_PREFIXES = ("balance-", "balance-gap-")

STATIC_ALLOW = frozenset(
    {
        "is-loading",
        "mode-live",
        "mode-cached",
    }
)

CSS_CLASS_RE = re.compile(r"\.([a-zA-Z][a-zA-Z0-9_-]*)")
CSS_VAR_DEF_RE = re.compile(r"--([a-zA-Z0-9_-]+)\s*:")
CSS_VAR_USE_RE = re.compile(r"var\(--([a-zA-Z0-9_-]+)")
HTML_CLASS_RE = re.compile(r"""class=["']([^"']+)["']""")


def dynamic_allowlist() -> set[str]:
    allowed = set(STATIC_ALLOW)
    for prefix in VERDICT_PREFIXES:
        for suffix in VERDICT_SUFFIXES:
            allowed.add(f"{prefix}{suffix}")
    for prefix in BALANCE_PREFIXES:
        for suffix in BALANCE_SUFFIXES:
            allowed.add(f"{prefix}{suffix}")
    return allowed


def css_classes(css: str) -> set[str]:
    return set(CSS_CLASS_RE.findall(css))


def python_class_tokens(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    tokens: set[str] = set()
    for match in re.finditer(r"""['"]([a-zA-Z0-9_-]+(?: [a-zA-Z0-9_-]+)*)['"]""", text):
        tokens.update(match.group(1).split())
    for match in re.finditer(r"\bf\"([a-zA-Z0-9_-]+)", text):
        tokens.add(match.group(1))
    return tokens


def html_class_tokens(html: str) -> set[str]:
    tokens: set[str] = set()
    for match in HTML_CLASS_RE.finditer(html):
        for part in match.group(1).split():
            tokens.add(part)
    return tokens


def main() -> int:
    css = CSS_PATH.read_text(encoding="utf-8")
    html = HTML_PATH.read_text(encoding="utf-8")

    referenced = html_class_tokens(html) | python_class_tokens(HTML_PATH) | dynamic_allowlist()
    defined = css_classes(css)
    orphan_classes = sorted(cls for cls in defined if cls not in referenced)

    defined_vars = set(CSS_VAR_DEF_RE.findall(css))
    used_vars = set(CSS_VAR_USE_RE.findall(css))
    orphan_vars = sorted(var for var in defined_vars if var not in used_vars)

    failed = False
    if orphan_classes:
        failed = True
        print("Unused dashboard CSS classes:", file=sys.stderr)
        for cls in orphan_classes:
            print(f"  .{cls}", file=sys.stderr)
    if orphan_vars:
        failed = True
        print("Unused dashboard CSS variables:", file=sys.stderr)
        for var in orphan_vars:
            print(f"  --{var}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
