"""Atomic file writes and corrupt-file recovery helpers."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Write text via temp file + replace so power loss cannot truncate the target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


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
