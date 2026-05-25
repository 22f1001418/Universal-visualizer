"""Job lifecycle routes.

GET  /jobs                      — list summaries
GET  /jobs/{job_id}             — full job state
GET  /jobs/{job_id}/topics      — extracted topics only

POST /upload moves here in Task 4 so all job routes live together.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_job
from backend.models import JobState, JobSummary
from backend.store import job_store

router = APIRouter(tags=["jobs"])


@router.get("/jobs", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
    """Return a summary of every known job."""
    return [
        JobSummary(
            job_id=j.job_id,
            script_name=j.script_name,
            status=j.status,
            created_at=j.created_at,
            topic_count=len(j.topics),
            build_count=len(j.builds),
        )
        for j in job_store.list_summaries()
    ]


@router.get("/jobs/{job_id}", response_model=JobState)
def get_job_state(job: JobState = Depends(get_job)) -> JobState:
    """Return full job state — the polling endpoint."""
    return job


@router.get("/jobs/{job_id}/topics")
def get_topics(job: JobState = Depends(get_job)) -> dict:
    """Return just the extracted topics for a job."""
    return {"job_id": job.job_id, "topics": [t.model_dump() for t in job.topics]}


# ── POST /upload (Task 4) ─────────────────────────────────────
import asyncio
import logging
import uuid

from fastapi import File, Form, HTTPException, UploadFile

from agents import topic_extraction_agent
from backend.models import (
    JobStatus,
    UploadResponse,
)

_logger = logging.getLogger("hackmd-orch")

ALLOWED_TRACKS = [
    "Academy DSA", "Academy Fullstack", "Academy Backend",
    "DSML DA", "DSML DS", "AIML", "DevOps",
]

MAX_FILE_SIZE = 5 * 1024 * 1024

# Module-level cache — script text isn't part of JobState because it's large
# and we don't need to serialize it. Keyed by job_id; cleared in purge_stale path.
_job_script_cache: dict[str, str] = {}


@router.post("/upload", response_model=UploadResponse)
async def upload_script(
    track: str = Form(...),
    file: UploadFile = File(...),
) -> UploadResponse:
    """Accept a HackMD .md file, extract viz topics with Agent A.

    Topic extraction is fast (~one LLM call) so we do it inline. The user
    sees the topics list immediately when this returns.
    """
    if track not in ALLOWED_TRACKS:
        raise HTTPException(400, f"Invalid track: {track}")
    if not file.filename or not file.filename.lower().endswith((".md", ".txt", ".markdown")):
        raise HTTPException(400, "Upload only .md / .markdown / .txt files.")

    raw = await file.read()
    if len(raw) > MAX_FILE_SIZE:
        raise HTTPException(400, "File too large. Max 5 MB.")
    try:
        script_text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "File must be UTF-8 encoded.")

    job_id = uuid.uuid4().hex[:12]

    job_store.purge_stale()

    job = JobState(
        job_id=job_id,
        script_name=file.filename,
        track=track,
        status=JobStatus.UPLOADED,
    )
    job_store.add(job)
    job_store.append_log(job_id, f"Uploaded {file.filename} ({len(raw)} bytes)")

    # Run Agent A in a worker thread so the event loop stays free.
    try:
        result = await asyncio.to_thread(
            topic_extraction_agent, script_text, file.filename, track, job_id,
        )
    except RuntimeError as e:
        _logger.exception("[Upload] Agent A failed for job=%s", job_id)
        job_store.update(
            job_id,
            status=JobStatus.FAILED,
            error=f"Topic extraction failed: {e}",
        )
        raise HTTPException(500, f"Topic extraction failed: {e}")

    # Persist topics on the job + stash the script text for later context queries.
    job_store.update(
        job_id,
        topics=result.topics,
        status=JobStatus.TOPICS_EXTRACTED,
    )
    job_store.append_log(job_id, f"Agent A extracted {len(result.topics)} topics")
    if result.extraction_note:
        job_store.append_log(job_id, f"Agent A note: {result.extraction_note}")

    # Hold the script text on the job (out-of-band — not in the Pydantic model)
    # so /suggestions can rebuild context if the embed_after_sentence search misses.
    _job_script_cache[job_id] = script_text

    return UploadResponse(
        job_id=job_id,
        script_name=file.filename,
        char_count=len(script_text),
        status=JobStatus.TOPICS_EXTRACTED,
    )
