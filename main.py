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
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

# Load .env BEFORE importing any module that reads env vars
load_dotenv()

from agents import (                         # noqa: E402
    assemble_viz_brief,
    topic_extraction_agent,
    viz_suggestion_agent,
)
from dev_server import (                     # noqa: E402
    DevServerInfo,
    StaticBuildInfo,
    build_static_viz,
    list_dev_servers,
    shutdown_all,
    start_dev_server,
    stop_dev_server,
)
from llm_client import (                     # noqa: E402
    TEXT_MODEL,
    TOKEN_BUDGET_PER_JOB,
    REASONING_EFFORT,
    is_reasoning_model,
    token_tracker,
)
from models import (                         # noqa: E402
    BuildPhase,
    BuildRequest,
    BuildTask,
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
    run_viz_build,
)
from store import job_store                   # noqa: E402
from github_publisher import (                # noqa: E402
    list_modules as gh_list_modules,
    list_classes as gh_list_classes,
    track_to_repo,
    publish_dist as gh_publish_dist,
    wait_for_pages_live,
    sanitize_path_component,
)


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

# When True (default), every successful build auto-runs npm install + audit fix
# + npm run dev so the user gets a clickable preview URL in the UI.
AUTO_START_DEV_SERVER = os.getenv("AUTO_START_DEV_SERVER", "true").lower() in ("1", "true", "yes")
BUILD_STATIC = os.getenv("BUILD_STATIC", "true").lower() in ("1", "true", "yes")


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


# Serve generated visualization static builds at /viz/<slug>/dist/...
# Must be registered before the wildcard routes but after middleware.
app.mount("/viz", StaticFiles(directory=str(VIZ_OUTPUT_DIR), html=False), name="viz_static")


# ─────────────────────────────────────────────
# Startup / shutdown
# ─────────────────────────────────────────────

@app.on_event("startup")
def _on_startup() -> None:
    VIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    status("SERVER STARTUP", f"text_model={TEXT_MODEL}")
    logger.info("[Startup] OPENAI_API_KEY present: %s",
                "yes" if os.getenv("OPENAI_API_KEY") else "NO")
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
# Health
# ─────────────────────────────────────────────

@app.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    return HealthResponse(
        ok=True,
        fixed_main_path=str(FIXED_MAIN_PATH),
        fixed_main_exists=FIXED_MAIN_PATH.exists(),
        text_model=TEXT_MODEL,
        output_dir=str(VIZ_OUTPUT_DIR),
    )


# ─────────────────────────────────────────────
# 1. Upload — extracts topics inline (synchronous)
# ─────────────────────────────────────────────

@app.post("/upload", response_model=UploadResponse)
async def upload_script(
    track: str = Form(...),
    file: UploadFile = File(...),
):
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
    status("UPLOAD", f"job_id={job_id}  file={file.filename}  size={len(raw)}B  track={track}")

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
        logger.exception("[Upload] Agent A failed for job=%s", job_id)
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

    status("UPLOAD DONE", f"job_id={job_id}  topics={len(result.topics)}")

    return UploadResponse(
        job_id=job_id,
        script_name=file.filename,
        char_count=len(script_text),
        status=JobStatus.TOPICS_EXTRACTED,
    )


# Module-level cache — script text isn't part of JobState because it's large
# and we don't need to serialize it. Keyed by job_id; cleared in purge_stale path.
_job_script_cache: dict[str, str] = {}


# ─────────────────────────────────────────────
# 2. Job listing / detail
# ─────────────────────────────────────────────

@app.get("/jobs", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
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


@app.get("/jobs/{job_id}", response_model=JobState)
def get_job(job_id: str) -> JobState:
    try:
        return job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")


@app.get("/jobs/{job_id}/topics")
def get_topics(job_id: str) -> dict:
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")
    return {"job_id": job_id, "topics": [t.model_dump() for t in job.topics]}


# ─────────────────────────────────────────────
# 3. Suggestions for one topic
# ─────────────────────────────────────────────

@app.post("/jobs/{job_id}/topics/{topic_id}/suggestions")
async def get_topic_suggestions(job_id: str, topic_id: str) -> dict:
    """Run Agent B for one extracted topic. Returns 5 viz suggestions.

    Cached on the JobState — calling twice for the same topic_id reuses the
    first response so the user can flip back and forth in the UI for free.
    """
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")

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


# ─────────────────────────────────────────────
# 4. Trigger a build for one topic (calls fixed_main_v6.py)
# ─────────────────────────────────────────────

@app.post("/jobs/{job_id}/topics/{topic_id}/build")
async def build_topic_viz(
    job_id: str,
    topic_id: str,
    request: BuildRequest,
    background_tasks: BackgroundTasks,
) -> dict:
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")

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

    # Derive GitHub repo from job track; empty string = skip GitHub publishing
    github_repo = ""
    if os.getenv("GITHUB_TOKEN") and request.github_module.strip() and request.github_class.strip():
        try:
            github_repo = track_to_repo(job.track)
        except ValueError:
            logger.warning("[Build] Unknown track for GitHub repo: %s", job.track)

    task = BuildTask(
        id=f"build_{uuid.uuid4().hex[:8]}",
        topic_id=topic_id,
        selected_suggestion_id=request.suggestion_id,
        custom_notes=request.custom_notes,
        short_topic=short_topic,        # what subprocess gets via --topic
        final_viz_brief=full_brief,     # full brief stored for traceability / UI
        phase="queued",
        github_repo=github_repo,
        github_module=sanitize_path_component(request.github_module),
        github_class=sanitize_path_component(request.github_class),
    )
    job.builds[topic_id] = task
    job_store.update(job_id, builds=job.builds, status=JobStatus.BUILDING)
    job_store.append_log(
        job_id,
        f"Queued build for {topic_id}: short_topic='{short_topic}'  "
        f"brief='{full_brief[:120]}'",
    )

    status(
        "BUILD QUEUED",
        f"job_id={job_id}  topic_id={topic_id}  short_topic='{short_topic}'",
    )

    background_tasks.add_task(_run_build_task, job_id, topic_id)

    return {
        "job_id": job_id,
        "topic_id": topic_id,
        "build_id": task.id,
        "phase": task.phase,
        "short_topic": short_topic,
        "final_viz_brief": full_brief,
    }


def _poll_github_pages(job_id: str, topic_id: str, url: str) -> None:
    """Daemon thread: poll until GitHub Pages is live, then update task status."""
    live = wait_for_pages_live(url)
    job = job_store.get(job_id)
    if job is None:
        return
    task = job.builds.get(topic_id)
    if task is None:
        return
    task.github_pages_status = "live" if live else "failed"
    if not live:
        task.github_pages_error = f"GitHub Pages did not go live within 5 minutes: {url}"
    job_store.update(job_id, builds=job.builds)
    logger.info("[Pages] %s → %s", url, task.github_pages_status)


def _run_build_task(job_id: str, topic_id: str) -> None:
    """Background-task wrapper: runs the subprocess builder, updates job state."""
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

    status("BUILD START", f"job_id={job_id}  topic_id={topic_id}")

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

    # ── Static build — runs npm run build to produce dist/ for production serving ──
    if result.success and result.project_dir and BUILD_STATIC:
        status("STATIC BUILD", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
        try:
            static_info: StaticBuildInfo = build_static_viz(result.project_dir)
            if static_info.status == "ok":
                slug = Path(result.project_dir).name
                task.static_url = f"/viz/{slug}/dist/index.html"
                logger.info("[Build %s] Static viz ready: %s", topic_id, task.static_url)
            else:
                logger.warning("[Build %s] Static build failed: %s", topic_id, static_info.error)
        except Exception as e:                # noqa: BLE001
            logger.exception("[Build %s] static build crashed: %s", topic_id, e)

    # ── Publish dist/ to GitHub Pages ──
    if result.success and result.project_dir and task.github_repo and task.github_module and task.github_class:
        status("GITHUB PUBLISH", f"job_id={job_id}  repo={task.github_repo}  {task.github_module}/{task.github_class}")
        dist_path = str(Path(result.project_dir) / "dist")
        task.github_pages_status = "pending"
        try:
            gh_url = gh_publish_dist(task.github_repo, task.github_module, task.github_class, dist_path)
            task.github_pages_url = gh_url
            logger.info("[Build %s] GitHub push done → %s", topic_id, gh_url)
            import threading as _threading
            _threading.Thread(
                target=_poll_github_pages,
                args=(job_id, topic_id, gh_url),
                daemon=True,
            ).start()
        except Exception as exc:                # noqa: BLE001
            logger.exception("[Build %s] GitHub publish failed: %s", topic_id, exc)
            task.github_pages_status = "failed"
            task.github_pages_error = str(exc)[:300]

    # ── Auto-launch the dev server so the user gets a clickable preview URL ──
    if result.success and result.project_dir and AUTO_START_DEV_SERVER:
        status("DEV SERVER", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
        task.dev_server_status = "starting"
        try:
            info: DevServerInfo = start_dev_server(result.project_dir)
            task.dev_server_status = info.status
            task.dev_server_url = info.url
            task.dev_server_port = info.port
            task.dev_server_error = info.error
            if info.status == "running":
                logger.info("[Build %s] Live preview ready: %s", topic_id, info.url)
            else:
                logger.warning("[Build %s] Dev server failed: %s", topic_id, info.error)
        except Exception as e:                # noqa: BLE001
            logger.exception("[Build %s] dev server crashed: %s", topic_id, e)
            task.dev_server_status = "failed"
            task.dev_server_error = str(e)

    # Update overall job status only when ALL builds in the job are done
    all_builds_finished = all(
        b.phase in ("completed", "failed") for b in job.builds.values()
    )
    if all_builds_finished:
        any_failed = any(b.phase == "failed" for b in job.builds.values())
        new_status = JobStatus.DONE if not any_failed else JobStatus.FAILED
        # Build the manifest from successful tasks
        manifest = _build_manifest(job)
        job_store.update(
            job_id, builds=job.builds, status=new_status, manifest=manifest,
        )
    else:
        job_store.update(job_id, builds=job.builds)

    status(
        "BUILD DONE" if result.success else "BUILD FAILED",
        f"job_id={job_id}  topic_id={topic_id}  phase={task.phase}",
    )


@app.on_event("shutdown")
def _on_shutdown() -> None:
    n = shutdown_all()
    status("SERVER SHUTDOWN", f"stopped {n} dev server(s)")


def _build_manifest(job: JobState) -> list[EmbedManifestEntry]:
    entries: list[EmbedManifestEntry] = []
    for topic in job.topics:
        task = job.builds.get(topic.id)
        if task is None:
            continue
        # Look up the chosen suggestion title (if any)
        viz_title = ""
        if task.selected_suggestion_id:
            sugs = job.suggestions.get(topic.id, [])
            sug = next((s for s in sugs if s.id == task.selected_suggestion_id), None)
            if sug:
                viz_title = sug.title
        if not viz_title and task.custom_notes:
            viz_title = "Custom — " + task.custom_notes[:40]

        entries.append(EmbedManifestEntry(
            section=topic.section,
            embed_after_sentence=topic.embed_after_sentence,
            topic=topic.topic,
            why_visual_helps=topic.why_visual_helps,
            viz_title=viz_title,
            viz_brief=task.final_viz_brief,
            project_dir=task.project_dir,
            screenshot_path=task.screenshot_path,
            dev_server_url=task.dev_server_url if task.dev_server_status == "running" else "",
            static_url=task.static_url,
            status="ok" if task.phase == "completed" else "failed",
        ))
    return entries


# ─────────────────────────────────────────────
# 5b. Dev server controls — start / stop / list
# ─────────────────────────────────────────────

@app.post("/jobs/{job_id}/topics/{topic_id}/dev-server/start")
def start_topic_dev_server(job_id: str, topic_id: str) -> dict:
    """Manually start (or restart) the dev server for a built topic."""
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")
    task = job.builds.get(topic_id)
    if task is None or not task.project_dir:
        raise HTTPException(400, f"No completed build found for topic {topic_id}")

    info = start_dev_server(task.project_dir)
    task.dev_server_status = info.status
    task.dev_server_url = info.url
    task.dev_server_port = info.port
    task.dev_server_error = info.error
    job_store.update(job_id, builds=job.builds)
    return {
        "status": info.status,
        "url": info.url,
        "port": info.port,
        "error": info.error,
    }


@app.post("/jobs/{job_id}/topics/{topic_id}/dev-server/stop")
def stop_topic_dev_server(job_id: str, topic_id: str) -> dict:
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")
    task = job.builds.get(topic_id)
    if task is None or not task.project_dir:
        raise HTTPException(400, f"No build found for topic {topic_id}")

    stopped = stop_dev_server(task.project_dir)
    task.dev_server_status = "stopped"
    task.dev_server_url = ""
    job_store.update(job_id, builds=job.builds)
    return {"stopped": stopped}


@app.get("/dev-servers")
def list_running_dev_servers() -> dict:
    """List every dev server the orchestrator currently knows about."""
    servers = list_dev_servers()
    return {
        "servers": [
            {
                "project_dir": s.project_dir,
                "port": s.port,
                "url": s.url,
                "status": s.status,
                "started_at": s.started_at,
                "error": s.error,
            }
            for s in servers
        ]
    }


# ─────────────────────────────────────────────
# 5a. GitHub repo / module / class listing
# ─────────────────────────────────────────────

@app.get("/github/repos/{repo}/modules")
def github_list_modules(repo: str) -> dict:
    """List existing module folders in a track repo."""
    if not os.getenv("GITHUB_TOKEN"):
        return {"repo": repo, "modules": [], "error": "GITHUB_TOKEN not configured"}
    try:
        return {"repo": repo, "modules": gh_list_modules(repo)}
    except Exception as e:
        logger.warning("[GitHub] list_modules error: %s", e)
        return {"repo": repo, "modules": [], "error": str(e)[:200]}


@app.get("/github/repos/{repo}/modules/{module}/classes")
def github_list_classes(repo: str, module: str) -> dict:
    """List existing class folders inside a module."""
    if not os.getenv("GITHUB_TOKEN"):
        return {"repo": repo, "module": module, "classes": [], "error": "GITHUB_TOKEN not configured"}
    try:
        return {"repo": repo, "module": module, "classes": gh_list_classes(repo, module)}
    except Exception as e:
        logger.warning("[GitHub] list_classes error: %s", e)
        return {"repo": repo, "module": module, "classes": [], "error": str(e)[:200]}


# ─────────────────────────────────────────────
# 5. Final manifest
# ─────────────────────────────────────────────

@app.get("/jobs/{job_id}/manifest")
def get_manifest(job_id: str) -> dict:
    try:
        job = job_store.get_or_404(job_id)
    except KeyError:
        raise HTTPException(404, f"Job {job_id} not found")

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
    manifest = job.manifest or _build_manifest(job)

    return {
        "job_id": job_id,
        "ready": len(pending) == 0,
        "pending_builds": pending,
        "status": job.status,
        "manifest": [m.model_dump() for m in manifest],
        "token_usage": token_tracker.job_summary(job_id),
    }


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
