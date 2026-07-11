"""Vulture false positives in tests — pytest autouse fixtures and collection markers."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

import test_install_cli
import test_live

test_install_cli._stub_resolve_location
test_live.pytestmark
