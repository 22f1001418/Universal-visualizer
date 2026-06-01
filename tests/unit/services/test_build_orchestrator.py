"""Unit tests for backend.services.build_orchestrator.run_build_task.

External systems (run_viz_build, github_publisher) are mocked. The goal is to
verify that phase transitions and JobState mutations happen correctly under each
branch (success, build failure, github disabled).

Mocking strategy:
  - backend.services.build_orchestrator.run_viz_build  — the subprocess wrapper
  - backend.services.build_orchestrator.publish_viz — GitHub publishing
  - backend.services.build_orchestrator.settings  — to control publish_to_github flag

run_build_task is a plain synchronous function so no async runner is needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services.build_orchestrator import run_build_task
from backend.models import (
    BuildTask,
    ExtractedTopic,
    JobState,
    JobStatus,
)
from backend.store import job_store


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_topic(topic_id: str = "topic_1") -> ExtractedTopic:
    return ExtractedTopic(
        id=topic_id,
        section="## Section",
        topic="Binary Search",
        embed_after_sentence="Consider the following.",
        why_visual_helps="It shows the algorithm step by step.",
        surrounding_context="",
    )


def _make_task(topic_id: str = "topic_1") -> BuildTask:
    return BuildTask(
        id=f"build_{uuid.uuid4().hex[:8]}",
        topic_id=topic_id,
        short_topic="binary-search",
        final_viz_brief="Show binary search on a sorted array",
        phase="queued",
    )


def _seed_job(topic_id: str = "topic_1") -> tuple[str, str]:
    """Seed a JobState with one topic + one queued BuildTask. Return (job_id, topic_id)."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    topic = _make_topic(topic_id)
    task = _make_task(topic_id)
    job = JobState(
        job_id=job_id,
        script_name="test_script.md",
        status=JobStatus.BUILDING,
        topics=[topic],
        builds={topic_id: task},
    )
    job_store.add(job)
    return job_id, topic_id


def _make_success_result(project_dir: str = "/tmp/fake-proj") -> SimpleNamespace:
    """A minimal BuildResult-like namespace that looks like a successful build."""
    return SimpleNamespace(
        success=True,
        project_dir=project_dir,
        screenshot_path=f"{project_dir}/screenshot.png",
        error="",
        completed_at=datetime.utcnow(),
    )


def _make_failed_result() -> SimpleNamespace:
    """A BuildResult-like namespace representing a failed subprocess run."""
    return SimpleNamespace(
        success=False,
        project_dir="",
        screenshot_path="",
        error="exit code 1",
        completed_at=datetime.utcnow(),
    )


# ─────────────────────────────────────────────
# Patch paths (all relative to the module under test)
# ─────────────────────────────────────────────

_MOD = "backend.services.build_orchestrator"

_PATCH_RUN_VIZ    = f"{_MOD}.run_viz_build"
_PATCH_PUBLISH    = f"{_MOD}.publish_viz"
_PATCH_SETTINGS   = f"{_MOD}.settings"


# ─────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────

def test_successful_build_transitions_to_done():
    """A clean successful run sets phase='done' and job status=DONE."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = False   # skip GitHub branch

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.phase == "done", f"Expected done, got {task.phase!r}"
    assert job.status == JobStatus.DONE


def test_subprocess_failure_marks_failed():
    """When run_viz_build returns success=False the task phase should be 'failed'."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = False

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_failed_result()),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.phase == "failed", f"Expected failed, got {task.phase!r}"
    assert task.error != ""
    assert job.status == JobStatus.FAILED


def test_subprocess_exception_marks_failed():
    """When run_viz_build raises an exception the task phase is set to 'failed'
    and the exception message is captured in task.error."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = False

    with (
        patch(_PATCH_RUN_VIZ, side_effect=RuntimeError("subprocess crashed badly")),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.phase == "failed"
    assert "subprocess crashed" in task.error
    assert job.status == JobStatus.FAILED


def test_github_publish_called_on_success_when_enabled():
    """When settings.publish_to_github=True and settings.github_token is set,
    publish_viz should be called once."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = True
    mock_settings.github_token = "ghp_faketoken"
    mock_settings.github_repos_private = False
    mock_settings.program_repos = {
        "Academy DSA": SimpleNamespace(
            repo="viz-acad", vercel_base="https://viz-acad.vercel.app"
        )
    }

    fake_pub = SimpleNamespace(
        html_url="https://github.com/user/monorepo",
        clone_url="https://github.com/user/monorepo.git",
        repo_name="monorepo",
        commit_sha="abc",
        file_count=2,
        embed_url="https://user.github.io/monorepo/slug/",
        repo_edit_url="https://github.com/user/monorepo/tree/main/slug",
    )

    mock_publish = MagicMock(return_value=fake_pub)

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
        patch(_PATCH_PUBLISH, mock_publish),
    ):
        run_build_task(job_id, topic_id)

    mock_publish.assert_called_once()

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.github_status == "published"
    assert task.github_repo_url == "https://github.com/user/monorepo"


def test_github_skipped_when_token_not_set(monkeypatch):
    """When settings.github_token is None/empty, github_status should be 'skipped'."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = True
    mock_settings.github_token = None   # simulate missing GITHUB_TOKEN
    mock_settings.program_repos = {}    # token check fires first regardless

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.github_status == "skipped"
    assert "GITHUB_TOKEN" in task.github_error


def test_github_skipped_when_no_program_repo_configured(monkeypatch):
    """When no program repo is configured for the job's track, github_status
    should be 'skipped'."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = True
    mock_settings.github_token = "ghp_faketoken"
    mock_settings.program_repos = {}   # .get("Academy DSA") -> None

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    assert task.github_status == "skipped"
    assert "No program repo configured for track" in task.github_error
    assert "Academy DSA" in task.github_error


def test_github_failure_does_not_crash_build():
    """If publish_viz raises, the build should still complete (phase=done)
    and github_status should be 'failed'."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = True
    mock_settings.github_token = "ghp_faketoken"
    mock_settings.github_repos_private = False
    mock_settings.program_repos = {
        "Academy DSA": SimpleNamespace(
            repo="viz-acad", vercel_base="https://viz-acad.vercel.app"
        )
    }

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
        patch(_PATCH_PUBLISH, side_effect=Exception("GitHub API 500")),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    task = job.builds[topic_id]
    # The build itself succeeded; only GitHub failed
    assert task.phase == "done"
    assert task.github_status == "failed"
    assert "GitHub API 500" in task.github_error


def test_no_op_when_job_not_found():
    """run_build_task should silently return when job_id doesn't exist in the store."""
    # No seed — job doesn't exist
    run_build_task("nonexistent_job_id", "topic_1")  # should not raise


def test_no_op_when_task_not_found():
    """run_build_task should silently return when the topic has no BuildTask."""
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    job = JobState(
        job_id=job_id,
        script_name="test.md",
        status=JobStatus.BUILDING,
        topics=[_make_topic("topic_1")],
        builds={},  # No build for the topic
    )
    job_store.add(job)
    run_build_task(job_id, "topic_1")  # should not raise


def test_manifest_built_when_all_builds_finish():
    """After all builds in the job complete, the manifest should be populated."""
    job_id, topic_id = _seed_job()

    mock_settings = MagicMock()
    mock_settings.publish_to_github = False

    with (
        patch(_PATCH_RUN_VIZ, return_value=_make_success_result()),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    # manifest should have been computed (one entry per completed topic)
    assert len(job.manifest) == 1
    assert job.manifest[0].status == "ok"


def test_progress_log_capped_at_300_lines():
    """Progress log should be trimmed to at most 300 entries."""
    job_id, topic_id = _seed_job()

    # Pre-fill the progress log with 350 entries
    job = job_store.get(job_id)
    assert job is not None
    job.builds[topic_id].progress_log = [f"line {i}" for i in range(350)]

    mock_settings = MagicMock()
    mock_settings.publish_to_github = False

    # Return 20 extra lines so on_log fires 20 more times
    extra_lines = [f"extra {i}" for i in range(20)]

    def fake_run_viz_build(topic_brief, on_log=None, on_phase_change=None, extra_env=None):
        if on_log:
            for line in extra_lines:
                on_log(line)
        return _make_success_result()

    with (
        patch(_PATCH_RUN_VIZ, side_effect=fake_run_viz_build),
        patch(_PATCH_SETTINGS, mock_settings),
    ):
        run_build_task(job_id, topic_id)

    job = job_store.get(job_id)
    assert job is not None
    assert len(job.builds[topic_id].progress_log) <= 300
