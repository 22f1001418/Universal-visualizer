"""POST /jobs/{job_id}/topics/{topic_id}/build — queue a viz build."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from agents import assemble_viz_brief
from backend.api.deps import get_job
from backend.services.build_orchestrator import run_build_task
from backend.models import BuildRequest, BuildTask, JobState, JobStatus
from backend.store import job_store

router = APIRouter(tags=["builds"])


@router.post("/jobs/{job_id}/topics/{topic_id}/build")
async def build_topic_viz(
    job_id: str,
    topic_id: str,
    request: BuildRequest,
    background_tasks: BackgroundTasks,
    job: JobState = Depends(get_job),
) -> dict:
    """Pick a suggestion (or custom notes) and queue a build."""
    topic = next((t for t in job.topics if t.id == topic_id), None)
    if topic is None:
        raise HTTPException(404, f"Topic {topic_id} not in job {job_id}")

    suggestion = None
    if request.suggestion_id:
        sugs = job.suggestions.get(topic_id, [])
        suggestion = next((s for s in sugs if s.id == request.suggestion_id), None)
        if suggestion is None:
            raise HTTPException(
                400,
                f"suggestion_id={request.suggestion_id} not found. "
                "Call POST /suggestions first.",
            )
    elif not request.custom_notes.strip():
        raise HTTPException(
            400,
            "Either suggestion_id or custom_notes must be provided.",
        )

    # Compose the short filename-safe topic + the full LLM-prompt brief.
    # short_topic <= 60 chars (becomes the project directory name).
    # full_brief  packs in the suggestion + custom notes for the LLM prompt.
    short_topic, full_brief = assemble_viz_brief(topic, suggestion, request.custom_notes)

    # GitHub publishing is now per-viz-repo and decided at publish time, not here.
    # Old request.github_module / github_class fields are ignored for backwards-compat.

    task = BuildTask(
        id=f"build_{uuid.uuid4().hex[:8]}",
        topic_id=topic_id,
        selected_suggestion_id=request.suggestion_id,
        custom_notes=request.custom_notes,
        short_topic=short_topic,        # what subprocess gets via --topic
        final_viz_brief=full_brief,     # full brief stored for traceability / UI
        phase="queued",
    )
    job.builds[topic_id] = task
    job_store.update(job_id, builds=job.builds, status=JobStatus.BUILDING)
    job_store.append_log(
        job_id,
        f"Queued build for {topic_id}: short_topic='{short_topic}'  "
        f"brief='{full_brief[:120]}'",
    )

    background_tasks.add_task(run_build_task, job_id, topic_id)

    return {
        "job_id": job_id,
        "topic_id": topic_id,
        "build_id": task.id,
        "phase": task.phase,
        "short_topic": short_topic,
        "final_viz_brief": full_brief,
    }
