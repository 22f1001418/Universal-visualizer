"""FastAPI orchestrator — HackMD lecture script -> visualizations.

Endpoints:
  POST /upload                         — upload .md file, extract topics
  GET  /jobs/{job_id}                  — poll full job state
  GET  /jobs/{job_id}/topics           — extracted topics
  POST /jobs/{job_id}/topics/{tid}/suggestions  — get 5 viz suggestions
  POST /jobs/{job_id}/topics/{tid}/build        — user picks + custom notes
  GET  /jobs/{job_id}/manifest         — final embed manifest
  GET  /preview/{path:path}            — serve screenshots / generated files
  GET  /healthz                        — sanity check
  GET  /                               — serve the React frontend
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

# Load .env BEFORE importing any module that reads env vars
load_dotenv()

from agents import (                         # noqa: E402
    topic_extraction_agent,
    viz_suggestion_agent,
)
from backend.llm import is_reasoning_model, token_tracker  # noqa: E402
from models import (                         # noqa: E402
    EmbedManifestEntry,
    HealthResponse,
    JobState,
    JobStatus,
    JobSummary,
    UploadResponse,
    VizSuggestionsResult,
)
from orchestrator import (                   # noqa: E402
    FIXED_MAIN_PATH,
    VIZ_OUTPUT_DIR,
)
from store import job_store                   # noqa: E402
from backend.config import settings              # noqa: E402
from backend.api import builds as _builds            # noqa: E402
from backend.api import health as _health        # noqa: E402
from backend.api import jobs as _jobs            # noqa: E402
from backend.api import manifest as _manifest        # noqa: E402
from backend.api import preview as _preview      # noqa: E402
from backend.api import spa as _spa              # noqa: E402
from backend.api import suggestions as _suggestions  # noqa: E402


# ─────────────────────────────────────────────
# Logging setup — visible status markers in the terminal
# ─────────────────────────────────────────────

class _StructuredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        std = {
            "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
            "created", "msecs", "relativeCreated", "thread", "threadName",
            "processName", "process", "message", "asctime", "taskName",
        }
        extras = {k: v for k, v in record.__dict__.items() if k not in std}
        if extras:
            base += "  " + "  ".join(f"{k}={v}" for k, v in extras.items())
        return base


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_StructuredFormatter("%(asctime)s %(levelname)s %(name)s | %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)

logger = logging.getLogger("hackmd-orch")


def status(stage: str, detail: str = "") -> None:
    bar = "─" * 4
    if detail:
        logger.info("%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        logger.info("%s [STATUS] %s %s", bar, stage, bar)


# ─────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────

ALLOWED_TRACKS = [
    "Academy DSA", "Academy Fullstack", "Academy Backend",
    "DSML DA", "DSML DS", "AIML", "DevOps",
]

MAX_FILE_SIZE = 5 * 1024 * 1024

# When True (default if GITHUB_TOKEN is set), every successful build pushes the
# generated viz to its own standalone GitHub repo. One repo per build.
PUBLISH_TO_GITHUB = settings.publish_to_github
GITHUB_INCLUDE_DIST = settings.github_include_dist
GITHUB_REPOS_PRIVATE = settings.github_repos_private


# ─────────────────────────────────────────────
# App + CORS
# ─────────────────────────────────────────────

app = FastAPI(
    title="HackMD Visualization Orchestrator",
    description="Extract viz opportunities from a HackMD lecture script and build them.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.include_router(_health.router)
app.include_router(_jobs.router)
app.include_router(_suggestions.router)
app.include_router(_manifest.router)
app.include_router(_builds.router)
app.include_router(_preview.router)
app.include_router(_spa.router)


# ─────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────

@app.on_event("startup")
def _on_startup() -> None:
    VIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    status("SERVER STARTUP", f"text_model={settings.openai_text_model}")
    logger.info("[Startup] OPENAI_API_KEY present: %s",
                "yes" if settings.openai_api_key else "NO")
    logger.info("[Startup] FIXED_MAIN_PATH = %s   exists=%s",
                FIXED_MAIN_PATH, FIXED_MAIN_PATH.exists())
    logger.info("[Startup] VIZ_OUTPUT_DIR = %s", VIZ_OUTPUT_DIR)
    logger.info("[Startup] TOKEN_BUDGET_PER_JOB = %d", settings.token_budget_per_job)
    if is_reasoning_model(settings.openai_text_model):
        logger.info("[Startup] Reasoning model — REASONING_EFFORT=%s", settings.reasoning_effort)
    if not FIXED_MAIN_PATH.exists():
        logger.warning(
            "[Startup] WARNING: fixed_main_v6.py not found. The /build endpoint will fail. "
            "Set FIXED_MAIN_PATH in .env to its absolute path."
        )


# ─────────────────────────────────────────────
# 3. Suggestions for one topic
# ─────────────────────────────────────────────



# ─────────────────────────────────────────────
# 4. Trigger a build for one topic — moved to backend/api/builds.py (Task 6)
# ─────────────────────────────────────────────


# ─────────────────────────────────────────────
# 5. Final manifest — moved to backend/api/manifest.py (Task 7)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 6. Preview screenshots / static files — moved to backend/api/preview.py (Task 8)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# 7. Frontend (single-file React) — moved to backend/api/spa.py (Task 9)
# ─────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=False)
