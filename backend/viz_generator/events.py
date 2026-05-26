"""Structured (JSON) event logging for the viz pipeline.

Human-readable progress logs stay on the `viz_agent` logger. Structured
events (phase transitions, error categories, attempt counts) go to
`viz_agent.events` and are emitted as one-line JSON so downstream log
ingestion can index / aggregate them.

Usage:
    from backend.viz_generator.events import log_event
    log_event("draft", "success", attempts=2)
    log_event("validate", "preflight_fail", problems=["missing <body>"])
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("viz_agent.events")


def log_event(phase: str, event: str, **fields: Any) -> None:
    """Emit a single JSON event line on the viz_agent.events logger.

    `default=str` ensures dataclasses / Paths / datetimes serialise without
    raising — log calls must never crash the pipeline.
    """
    payload = {"phase": phase, "event": event, **fields}
    log.info("VIZ_EVENT %s", json.dumps(payload, default=str))
