"""Unit tests for backend.logging_setup."""
from __future__ import annotations

import logging

from backend.logging_setup import StructuredFormatter, configure_root_logger


def test_format_record_includes_level_and_message():
    fmt = StructuredFormatter("%(asctime)s %(levelname)s %(name)s | %(message)s")
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    out = fmt.format(record)
    assert "INFO" in out
    assert "hello world" in out
    assert "test.logger" in out


def test_configure_root_logger_idempotent():
    configure_root_logger()
    first_count = len(logging.getLogger().handlers)
    configure_root_logger()
    second_count = len(logging.getLogger().handlers)
    # Idempotent: re-running doesn't multiply handlers we manage
    assert first_count == second_count
