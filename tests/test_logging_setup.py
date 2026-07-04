from __future__ import annotations

import logging

import pytest

from rain_bypass.logging_setup import RedactSecretsFilter, configure_logging, redact_secrets


def test_redact_secrets_query_params():
    url = "https://example.com/timeline?unitGroup=us&key=SECRET123&include=days"
    assert redact_secrets(url) == "https://example.com/timeline?unitGroup=us&key=***&include=days"


def test_redact_secrets_leaves_normal_messages():
    message = "visual_crossing rain_mtd 0.28 in, forecast 0.01 in"
    assert redact_secrets(message) == message


def test_redact_secrets_filter_rewrites_log_record():
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="GET https://example.com?key=SECRET123",
        args=(),
        exc_info=None,
    )
    assert RedactSecretsFilter().filter(record) is True
    assert record.msg == "GET https://example.com?key=***"
    assert record.args == ()


def test_configure_logging_adds_redaction_filter():
    configure_logging("INFO")
    root = logging.getLogger()
    assert root.handlers
    assert any(
        any(isinstance(f, RedactSecretsFilter) for f in handler.filters)
        for handler in root.handlers
    )
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
