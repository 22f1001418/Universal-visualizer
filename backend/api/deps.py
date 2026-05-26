"""FastAPI dependency helpers shared across routers.

Use these via `Depends(...)` in route signatures to centralize cross-cutting
concerns (job lookup with 404, settings access).
"""
from __future__ import annotations

from fastapi import HTTPException

from backend.config import settings as _settings
from backend.models import JobState
from backend.store import job_store


def get_settings():
    """Provide the singleton Settings as a FastAPI dependency.

    Returning the singleton (not a fresh Settings()) is intentional — env
    is read once at process start, and per-request rebuilds would be wasteful.
    """
    return _settings


def get_job(job_id: str) -> JobState:
    """Resolve a job_id or raise HTTPException(404)."""
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return job
