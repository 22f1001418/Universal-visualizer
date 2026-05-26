"""Locks the new vanilla viz BuildPhase enum members."""
from backend.models import BuildPhase, BuildTask


def test_buildphase_members_match_vanilla_pipeline():
    # Literal members are not introspectable as an iterable in py3.12 without
    # typing.get_args, so use that. Order is part of the contract — orchestrator
    # phase-detection relies on it for the SPA progress bar.
    from typing import get_args
    assert get_args(BuildPhase) == (
        "queued", "draft", "validate", "polish", "publish", "done", "failed",
    )


def test_buildtask_phase_default_is_queued():
    t = BuildTask(id="b1", topic_id="t1")
    assert t.phase == "queued"
