"""Structured logging setup.

Provides a JSON-ish single-line formatter used by every backend module.
Was previously defined inline at the top of main.py.
"""
from __future__ import annotations

import logging
import sys


class StructuredFormatter(logging.Formatter):
    """Single-line log formatter with timestamp, level, logger name, and message.

    Body verbatim from main.py's pre-Stage-3 _StructuredFormatter. The leading
    underscore is dropped because this is now a public-surface class on a
    dedicated module.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        std = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime", "taskName",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in std}
        if extras:
            base += "  " + "  ".join(f"{k}={v}" for k, v in extras.items())
        return base


def configure_root_logger(level: int = logging.INFO) -> None:
    """Install StructuredFormatter on stdout and set the root logger level.

    Idempotent — re-running replaces existing handlers attached by this
    function. Other handlers (e.g., file handlers added by uvicorn) are
    left alone.
    """
    root = logging.getLogger()
    # Remove only handlers we installed previously
    for h in list(root.handlers):
        if getattr(h, "_added_by", None) == "configure_root_logger":
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    handler._added_by = "configure_root_logger"  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
