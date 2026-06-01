"""Background build orchestration.

run_build_task() is invoked by FastAPI's BackgroundTasks when the user
picks a viz suggestion. It spawns the vanilla viz generator subprocess,
streams its progress into the JobState, optionally publishes
the result to GitHub, and updates job state through several phases.

Was previously _run_build_task in main.py.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.services.manifest_builder import build_manifest
from backend.github_publisher import publish_viz
from backend.models import JobStatus
from backend.orchestrator import run_viz_build
from backend.store import job_store

logger = logging.getLogger("hackmd-orch.build")

# Serialize the Chromium-heavy generation step. Builds run as FastAPI
# BackgroundTasks (worker threads), so two clicks could otherwise launch two
# browsers at once and OOM a small instance (e.g. Render free, 512MB). A second
# build blocks here until the first finishes generating. Publishing (a light
# GitHub API call) stays outside the lock so it can overlap.
_BUILD_LOCK = threading.Lock()


def _status(stage: str, detail: str = "") -> None:
    bar = "─" * 4
    if detail:
        logger.info("%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        logger.info("%s [STATUS] %s %s", bar, stage, bar)


def run_build_task(job_id: str, topic_id: str) -> None:
    """Run a single viz build end-to-end. Updates the JobState as it progresses.

    Body verbatim from main.py:_run_build_task. The leading underscore is
    dropped because this is now a public service function. os.getenv calls
    for GitHub publish flags are replaced with settings.* accesses.
    """
    job = job_store.get(job_id)
    if job is None:
        return
    task = job.builds.get(topic_id)
    if task is None:
        return

    def on_log(line: str) -> None:
        # Cap the per-task progress log so we don't OOM on a chatty build
        task.progress_log.append(line)
        if len(task.progress_log) > 300:
            task.progress_log = task.progress_log[-300:]

    def on_phase(phase_name: str) -> None:
        # The phase strings from orchestrator already match BuildPhase literals
        task.phase = phase_name  # type: ignore[assignment]
        logger.info("[Build %s] phase -> %s", topic_id, phase_name)

    _status("BUILD START", f"job_id={job_id}  topic_id={topic_id}")

    # Use short_topic (<=60 chars, ASCII-safe) for the subprocess --topic arg,
    # NOT the full brief. Long briefs cause "File name too long" errors when
    # the subprocess tries to slug them into a project directory name.
    topic_arg = task.short_topic or task.final_viz_brief

    try:
        # Only one Chromium-backed generation at a time (see _BUILD_LOCK).
        with _BUILD_LOCK:
            result = run_viz_build(
                topic_brief=topic_arg,
                on_log=on_log,
                on_phase_change=on_phase,
            )
    except Exception as e:                # noqa: BLE001  — never let a build crash the worker
        logger.exception("[Build] subprocess crashed: %s", e)
        task.phase = "failed"
        task.error = f"subprocess crashed: {e}"
        task.completed_at = datetime.utcnow()
        job_store.update(job_id, builds=job.builds, status=JobStatus.FAILED)
        return

    task.completed_at = result.completed_at or datetime.utcnow()
    task.project_dir = result.project_dir
    task.screenshot_path = result.screenshot_path
    task.error = result.error or ""
    task.phase = "done" if result.success else "failed"

    # ── Publish the viz to its program repo (<module>/<viz>/ path) ──
    if result.success and result.project_dir and settings.publish_to_github:
        prog = settings.program_repos.get(job.track)
        if not settings.github_token:
            task.github_status = "skipped"
            task.github_error = "GITHUB_TOKEN not set"
            on_log("[GitHub] skipped — GITHUB_TOKEN not set")
        elif prog is None:
            task.github_status = "skipped"
            task.github_error = f"No program repo configured for track {job.track!r}"
            on_log(f"[GitHub] skipped — no program repo for track {job.track!r}")
        else:
            _status("PUBLISH", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
            task.phase = "publish"  # type: ignore[assignment]
            task.github_status = "publishing"
            try:
                viz_slug = task.short_topic or Path(result.project_dir).name
                pub = publish_viz(
                    project_dir=result.project_dir,
                    repo=prog.repo,
                    vercel_base=prog.vercel_base,
                    module_slug=job.module or "module",
                    viz_slug=viz_slug,
                    description=(task.final_viz_brief or viz_slug)[:300],
                    private=settings.github_repos_private,
                    on_log=on_log,
                )
                task.github_status = "published"
                task.github_repo_url = pub.html_url
                task.github_clone_url = pub.clone_url
                task.github_repo_name = pub.repo_name
                task.github_commit_sha = pub.commit_sha
                task.embed_url = pub.embed_url
                task.repo_edit_url = pub.repo_edit_url
                task.monorepo_name = pub.repo_name
                task.phase = "done"  # type: ignore[assignment]
                logger.info("[Build %s] Published to %s", topic_id, pub.embed_url)
            except Exception as exc:                # noqa: BLE001 — publish must never crash the build
                logger.exception("[Build %s] GitHub publish failed: %s", topic_id, exc)
                task.github_status = "failed"
                task.github_error = str(exc)[:500]
                task.phase = "done"  # type: ignore[assignment]
                on_log(f"[GitHub] FAILED — {exc}")

    # Update overall job status only when ALL builds in the job are done
    all_builds_finished = all(
        b.phase in ("done", "failed") for b in job.builds.values()
    )
    if all_builds_finished:
        any_failed = any(b.phase == "failed" for b in job.builds.values())
        new_status = JobStatus.DONE if not any_failed else JobStatus.FAILED
        # Build the manifest from successful tasks
        manifest = build_manifest(job)
        job_store.update(
            job_id, builds=job.builds, status=new_status, manifest=manifest,
        )
    else:
        job_store.update(job_id, builds=job.builds)

    _status(
        "BUILD DONE" if result.success else "BUILD FAILED",
        f"job_id={job_id}  topic_id={topic_id}  phase={task.phase}",
    )
