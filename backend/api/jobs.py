"""Job lifecycle routes.

GET  /jobs                      — list summaries
GET  /jobs/{job_id}             — full job state
GET  /jobs/{job_id}/topics      — extracted topics only

POST /upload moves here in Task 4 so all job routes live together.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_job
from models import JobState, JobSummary
from store import job_store

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
