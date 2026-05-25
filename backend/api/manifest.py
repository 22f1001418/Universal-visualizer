"""GET /jobs/{job_id}/manifest — final embed manifest."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_job
from backend.llm import token_tracker
from backend.services.manifest_builder import build_manifest
from backend.models import JobState
from backend.store import job_store

router = APIRouter(tags=["manifest"])


@router.get("/jobs/{job_id}/manifest")
def get_manifest(job_id: str, job: JobState = Depends(get_job)) -> dict:
    """Return the final embed manifest + token usage totals."""
    # If the job hasn't built anything yet, return an empty manifest with a hint.
    if not job.builds:
        return {
            "job_id": job_id,
            "ready": False,
            "message": "No builds have been triggered yet.",
            "manifest": [],
            "token_usage": token_tracker.job_summary(job_id),
        }

    pending = [tid for tid, b in job.builds.items() if b.phase not in ("completed", "failed")]
    manifest = job.manifest or build_manifest(job)

    return {
        "job_id": job_id,
        "ready": len(pending) == 0,
        "pending_builds": pending,
        "status": job.status,
        "manifest": [m.model_dump() for m in manifest],
        "token_usage": token_tracker.job_summary(job_id),
    }
