"""Background build orchestration.

run_build_task() is invoked by FastAPI's BackgroundTasks when the user
picks a viz suggestion. It spawns fixed_main_v6.py (via backend.viz_generator.cli)
as a subprocess, streams its progress into the JobState, optionally publishes
the result to GitHub, and updates job state through several phases.

Was previously _run_build_task in main.py.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from backend.config import settings
from backend.services.manifest_builder import build_manifest
from backend.viz_generator.postprocess import (
    _inject_error_boundary,
    _patch_vite_config_base,
)
from github_publisher import publish_viz_repo
from models import JobStatus
from orchestrator import run_viz_build
from store import job_store

logger = logging.getLogger("hackmd-orch.build")


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
    # fixed_main_v6.py tries to slug them into a project directory name.
    topic_arg = task.short_topic or task.final_viz_brief

    try:
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
    task.phase = "completed" if result.success else "failed"

    # ── Patch vite.config.ts to use relative asset paths (base: './') ──
    # Without this, dist/index.html references /assets/... absolutely, which
    # 404s on any subpath deploy AND looks broken if a host serves the source
    # repo root instead of dist/. Safe to apply unconditionally — `./` works
    # at root and subpath alike.
    if result.success and result.project_dir:
        try:
            _patch_vite_config_base(Path(result.project_dir), on_log=on_log)
        except Exception as exc:                # noqa: BLE001 — patcher must never crash the build
            logger.warning("[Build %s] vite.config patch skipped: %s", topic_id, exc)

    # ── Wrap <App/> in an ErrorBoundary so first-render crashes are visible ──
    # The LLM-generated App.tsx occasionally throws on initial render (bad
    # regex, undefined access, etc.). Without a boundary, React 18 unmounts
    # the tree and the deployed page goes blank with no clue. The boundary
    # renders the actual error + stack inline so the failure is debuggable
    # from the deployed page itself.
    if result.success and result.project_dir:
        try:
            _inject_error_boundary(Path(result.project_dir), on_log=on_log)
        except Exception as exc:                # noqa: BLE001
            logger.warning("[Build %s] ErrorBoundary injection skipped: %s", topic_id, exc)

    # ── Publish the viz to its own standalone GitHub repo ──
    if result.success and result.project_dir and settings.publish_to_github:
        if not settings.github_token:
            task.github_status = "skipped"
            task.github_error = "GITHUB_TOKEN not set"
            on_log("[GitHub] skipped — GITHUB_TOKEN not set")
        else:
            _status("GITHUB PUBLISH", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
            task.github_status = "publishing"
            try:
                slug = task.short_topic or Path(result.project_dir).name
                pub = publish_viz_repo(
                    project_dir=result.project_dir,
                    slug=slug,
                    description=(task.final_viz_brief or slug)[:300],
                    include_dist=settings.github_include_dist,
                    private=settings.github_repos_private,
                    on_log=on_log,
                )
                task.github_status = "published"
                task.github_repo_url = pub.html_url
                task.github_clone_url = pub.clone_url
                task.github_repo_name = pub.repo_name
                task.github_commit_sha = pub.commit_sha
                logger.info("[Build %s] Published to %s (%d files)",
                            topic_id, pub.html_url, pub.file_count)
            except Exception as exc:                # noqa: BLE001 — publish must never crash the build
                logger.exception("[Build %s] GitHub publish failed: %s", topic_id, exc)
                task.github_status = "failed"
                task.github_error = str(exc)[:500]
                on_log(f"[GitHub] FAILED — {exc}")

    # Update overall job status only when ALL builds in the job are done
    all_builds_finished = all(
        b.phase in ("completed", "failed") for b in job.builds.values()
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
