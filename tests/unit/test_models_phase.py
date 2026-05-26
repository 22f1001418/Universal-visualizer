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


def test_buildtask_has_embed_url_fields():
    t = BuildTask(id="b1", topic_id="t1")
    # New fields default to empty strings; publisher populates them on success.
    assert t.embed_url == ""
    assert t.repo_edit_url == ""
    assert t.monorepo_name == ""


def test_embed_manifest_entry_has_embed_url():
    from backend.models import EmbedManifestEntry
    e = EmbedManifestEntry(
        section="## S", embed_after_sentence="x.", topic="T",
        why_visual_helps="y", viz_title="v", viz_brief="b", project_dir="/p",
    )
    assert e.embed_url == ""
    assert e.repo_edit_url == ""
