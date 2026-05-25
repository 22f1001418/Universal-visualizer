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
import os
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
from llm_client import (                     # noqa: E402
    TEXT_MODEL,
    TOKEN_BUDGET_PER_JOB,
    REASONING_EFFORT,
    is_reasoning_model,
    token_tracker,
)
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
from backend.api import health as _health        # noqa: E402
from backend.api import jobs as _jobs            # noqa: E402
from backend.api import suggestions as _suggestions  # noqa: E402
from backend.api import manifest as _manifest        # noqa: E402
from backend.api import builds as _builds            # noqa: E402


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
PUBLISH_TO_GITHUB = os.getenv("PUBLISH_TO_GITHUB", "true").lower() in ("1", "true", "yes")
GITHUB_INCLUDE_DIST = os.getenv("GITHUB_INCLUDE_DIST", "true").lower() in ("1", "true", "yes")
GITHUB_REPOS_PRIVATE = os.getenv("GITHUB_REPOS_PRIVATE", "false").lower() in ("1", "true", "yes")


# ─────────────────────────────────────────────
# App + CORS
# ─────────────────────────────────────────────

app = FastAPI(
    title="HackMD Visualization Orchestrator",
    description="Extract viz opportunities from a HackMD lecture script and build them.",
    version="1.0.0",
)

origins_raw = os.getenv("ALLOWED_ORIGINS", "http://127.0.0.1:8001,http://localhost:8001")
origins = [o.strip() for o in origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

app.include_router(_health.router)
app.include_router(_jobs.router)
app.include_router(_suggestions.router)
app.include_router(_manifest.router)
app.include_router(_builds.router)


# ─────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────

@app.on_event("startup")
def _on_startup() -> None:
    VIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    status("SERVER STARTUP", f"text_model={TEXT_MODEL}")
    logger.info("[Startup] OPENAI_API_KEY present: %s",
                "yes" if settings.openai_api_key else "NO")
    logger.info("[Startup] FIXED_MAIN_PATH = %s   exists=%s",
                FIXED_MAIN_PATH, FIXED_MAIN_PATH.exists())
    logger.info("[Startup] VIZ_OUTPUT_DIR = %s", VIZ_OUTPUT_DIR)
    logger.info("[Startup] TOKEN_BUDGET_PER_JOB = %d", TOKEN_BUDGET_PER_JOB)
    if is_reasoning_model(TEXT_MODEL):
        logger.info("[Startup] Reasoning model — REASONING_EFFORT=%s", REASONING_EFFORT)
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
# 6. Preview screenshots / static files
# ─────────────────────────────────────────────

@app.get("/preview")
def preview_file(path: str) -> FileResponse:
    """Serve a screenshot or generated file by absolute path.

    Locked down to paths inside VIZ_OUTPUT_DIR — no traversal allowed.
    """
    target = Path(path).resolve()
    try:
        target.relative_to(VIZ_OUTPUT_DIR)
    except ValueError:
        raise HTTPException(400, "Path outside VIZ_OUTPUT_DIR")
    if not target.exists():
        raise HTTPException(404, "File not found")
    media_type = "image/png" if target.suffix.lower() == ".png" else None
    return FileResponse(path=str(target), media_type=media_type, filename=target.name)


# ─────────────────────────────────────────────
# 7. Frontend (single-file React)
# ─────────────────────────────────────────────

_FRONTEND_FILE = Path(__file__).resolve().parent / "index.html"


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    if not _FRONTEND_FILE.exists():
        return HTMLResponse(
            "<h1>Frontend not found</h1><p>Expected at: " + str(_FRONTEND_FILE) + "</p>",
            status_code=500,
        )
    return HTMLResponse(_FRONTEND_FILE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run("main:app", host=host, port=port, reload=False)
