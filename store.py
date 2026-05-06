"""In-memory job store. Thread-safe, with TTL eviction and a hard cap.

Same pattern as the JOBS dict in image_gen_main.py — proven OK for local
development and small-team server use. For multi-process deployment swap
this for Redis or a small SQLite store.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

from models import JobState, JobStatus

logger = logging.getLogger("hackmd-orch.store")


JOB_TTL_HOURS = 24
MAX_JOBS = 200


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, JobState] = {}
        self._lock = Lock()

    # ── basic CRUD ──────────────────────────────────

    def add(self, job: JobState) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def get(self, job_id: str) -> Optional[JobState]:
        with self._lock:
            return self._jobs.get(job_id)

    def get_or_404(self, job_id: str) -> JobState:
        job = self.get(job_id)
        if job is None:
            raise KeyError(f"Job {job_id} not found")
        return job

    def update(self, job_id: str, **fields) -> Optional[JobState]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for k, v in fields.items():
                setattr(job, k, v)
            return job

    def append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.logs.append(f"{datetime.utcnow().isoformat()}  {line}")
            # Keep log bounded to last 500 lines per job
            if len(job.logs) > 500:
                job.logs = job.logs[-500:]

    def list_summaries(self) -> list[JobState]:
        with self._lock:
            return list(self._jobs.values())

    # ── eviction ────────────────────────────────────

    def purge_stale(self) -> int:
        now = datetime.utcnow()
        cutoff = now - timedelta(hours=JOB_TTL_HOURS)
        purged = 0
        with self._lock:
            stale = [jid for jid, j in self._jobs.items() if j.created_at < cutoff]
            for jid in stale:
                del self._jobs[jid]
                purged += 1
            # Hard cap — drop oldest if over
            if len(self._jobs) > MAX_JOBS:
                ordered = sorted(self._jobs.items(), key=lambda kv: kv[1].created_at)
                for jid, _ in ordered[: len(self._jobs) - MAX_JOBS]:
                    del self._jobs[jid]
                    purged += 1
        if purged:
            logger.info("[JobStore] purged %d stale job(s)", purged)
        return purged


# singleton
job_store = JobStore()
