"""Locks the structured-event payload shape used by draft/polish/validator."""
import json
import logging

import pytest

from backend.viz_generator.events import log_event


def test_log_event_emits_parseable_json(caplog):
    caplog.set_level(logging.INFO, logger="viz_agent.events")
    log_event("draft", "success", attempts=2, topic="x")
    records = [r for r in caplog.records if r.name == "viz_agent.events"]
    assert len(records) == 1
    msg = records[0].getMessage()
    assert msg.startswith("VIZ_EVENT ")
    payload = json.loads(msg[len("VIZ_EVENT "):])
    assert payload == {
        "phase": "draft", "event": "success", "attempts": 2, "topic": "x",
    }


def test_log_event_serialises_non_json_values(caplog):
    """dataclasses, Paths, etc. — use default=str to never raise."""
    from pathlib import Path
    caplog.set_level(logging.INFO, logger="viz_agent.events")
    log_event("validate", "preflight_fail", path=Path("/tmp/x"), count=0)
    rec = [r for r in caplog.records if r.name == "viz_agent.events"][-1]
    payload = json.loads(rec.getMessage()[len("VIZ_EVENT "):])
    assert payload["path"] == "/tmp/x"
    assert payload["count"] == 0
