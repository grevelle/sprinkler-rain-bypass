"""Atomic file writes and corrupt-file recovery helpers."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text via temp file + replace so power loss cannot truncate the target."""
    path_str = os.fspath(path)
    parent = os.path.dirname(path_str)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_str = os.path.join(
        parent or ".",
        f".{os.path.basename(path_str)}.{os.getpid()}.tmp",
    )
    try:
        with open(tmp_str, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_str, path_str)
    finally:
        if os.path.exists(tmp_str):
            os.unlink(tmp_str)


def quarantine_corrupt(path: Path) -> Path | None:
    """Move a corrupt file aside and return the backup path (if any)."""
    if not path.is_file():
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, backup)
    except OSError:
        logger.exception("could not quarantine corrupt file path=%s", path)
        return None
    logger.warning("quarantined corrupt file path=%s backup=%s", path, backup)
    return backup
