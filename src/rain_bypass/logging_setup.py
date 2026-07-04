from __future__ import annotations

import logging
import re

_QUERY_SECRET = re.compile(r"([?&](?:key|api_key|token)=)[^&\s\"']+", re.IGNORECASE)
_AUTH_HEADER = re.compile(r"(Authorization:\s*)[^\s]+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    redacted = _QUERY_SECRET.sub(r"\1***", text)
    return _AUTH_HEADER.sub(r"\1***", redacted)


class RedactSecretsFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        force=True,
    )
    secret_filter = RedactSecretsFilter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.addFilter(secret_filter)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
