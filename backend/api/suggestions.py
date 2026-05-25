"""POST /jobs/{job_id}/topics/{topic_id}/suggestions — Agent B (cached)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from agents import viz_suggestion_agent
from backend.api.deps import get_job
from backend.models import JobState, VizSuggestionsResult
from backend.store import job_store

router = APIRouter(tags=["suggestions"])
logger = logging.getLogger("hackmd-orch")


def status(stage: str, detail: str = "") -> None:
    """Log status updates during suggestion generation."""
    bar = "─" * 4
    if detail:
        logger.info("%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        logger.info("%s [STATUS] %s %s", bar, stage, bar)


@router.post("/jobs/{job_id}/topics/{topic_id}/suggestions")
async def get_topic_suggestions(
    job_id: str,
    topic_id: str,
    job: JobState = Depends(get_job),
) -> dict:
    """Run Agent B for one extracted topic. Returns 5 viz suggestions.

    Cached on the JobState — calling twice for the same topic_id reuses the
    first response so the user can flip back and forth in the UI for free.
    """
    topic = next((t for t in job.topics if t.id == topic_id), None)
    if topic is None:
        raise HTTPException(404, f"Topic {topic_id} not in job {job_id}")

    # Cache hit?
    if topic_id in job.suggestions and job.suggestions[topic_id]:
        return {
            "job_id": job_id,
            "topic_id": topic_id,
            "topic": topic.model_dump(),
            "suggestions": [s.model_dump() for s in job.suggestions[topic_id]],
            "cached": True,
        }

    status("SUGGEST", f"job_id={job_id}  topic_id={topic_id}  topic={topic.topic[:60]}")

    try:
        result: VizSuggestionsResult = await asyncio.to_thread(
            viz_suggestion_agent, topic, job.track, job_id,
        )
    except RuntimeError as e:
        logger.exception("[Suggest] Agent B failed")
        raise HTTPException(500, f"Viz suggestion agent failed: {e}")

    # Persist
    job.suggestions[topic_id] = result.suggestions
    job_store.update(job_id, suggestions=job.suggestions)
    job_store.append_log(
        job_id, f"Agent B produced {len(result.suggestions)} suggestions for {topic_id}",
    )

    return {
        "job_id": job_id,
        "topic_id": topic_id,
        "topic": topic.model_dump(),
        "suggestions": [s.model_dump() for s in result.suggestions],
        "cached": False,
    }
