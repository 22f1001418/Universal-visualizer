# Modularization Stage 3 — api/ + services/ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the 669-line `main.py` into a thin app factory plus a `backend/api/` package of route handlers and a `backend/services/` package of business logic. Migrate the remaining `os.getenv(...)` sprawl (CORS, GitHub publish flags, host/port, build timeout, dev-server config) into `backend/config.py`. Delete the `llm_client.py` shim after switching its remaining consumers to `backend.llm` direct imports. Add an import-linter contract that locks the new layering.

**Architecture:** Strangler-fig step #3 from the modularization spec (`docs/superpowers/specs/2026-05-25-modularization-design.md`). Routes move one resource at a time. Each move is verified by the **Stage 1 contract baseline** (12 HTTP tests that lock today's request/response shapes) — that's the safety net for this entire stage. No HTTP behavior changes; only the file structure does.

**Tech Stack:** FastAPI, pydantic-settings, import-linter, pytest. One new dev dependency (`import-linter`).

**Spec sections this plan implements:**
- Section 1 — `backend/api/` and `backend/services/` packages (final file layout)
- Section 2 — "Code maintainability" sub-bullets (single-responsibility, public interface = `__init__.py`, configuration consolidated, import-linter contract enforced in CI)
- Section 4 — Stage 3 cutover (move routes one resource at a time; delete `llm_client.py` shim at the end; same env var names → no Railway config change required)

**Out of scope for this plan:**
- Frontend changes (Stage 4)
- Multi-process / horizontal-scale work (would require replacing in-memory `JobStore` — separate future project)
- HTTP API shape changes (the contract baseline is the constraint)
- Performance optimizations from spec Section 2 (npm template cache, deterministic file selection, dev-server reuse) — still scoped to a follow-up Stage-2b plan; this stage only restructures the FastAPI side

---

## File structure (Stage 3 end state)

**Created:**
```
backend/
  api/
    __init__.py                # mounts every router on the FastAPI app
    deps.py                    # FastAPI Depends() helpers (job lookup, settings)
    health.py                  # GET /healthz
    jobs.py                    # POST /upload + GET /jobs + GET /jobs/{id} + GET /jobs/{id}/topics
    suggestions.py             # POST /jobs/{id}/topics/{tid}/suggestions
    builds.py                  # POST /jobs/{id}/topics/{tid}/build
    manifest.py                # GET /jobs/{id}/manifest
    preview.py                 # GET /preview
    spa.py                     # GET / (serves index.html)
  services/
    __init__.py
    build_orchestrator.py      # was _run_build_task in main.py
    manifest_builder.py        # was _build_manifest in main.py
  logging_setup.py             # was _StructuredFormatter inline in main.py
  importlinter.toml            # contract: api/ → services/ → {llm, viz_generator, store, models}
tests/
  unit/
    api/
      __init__.py
      test_deps.py             # FastAPI dependency helpers (~3 tests)
    services/
      __init__.py
      test_manifest_builder.py # 4-6 tests against a fixture JobState
      test_build_orchestrator.py  # ~4 tests with subprocess.run mocked
    logging/
      __init__.py
      test_structured_formatter.py  # 2-3 tests
```

**Modified:**
- `main.py` — reduced to ~100 lines: imports, `create_app()` factory, startup hook, CORS middleware, `app.include_router(...)` calls, `if __name__ == "__main__"` uvicorn entry
- `backend/config.py` — extends pydantic-settings to cover the routing/Github/dev-server env vars
- `orchestrator.py` — replaces `os.getenv("BUILD_TIMEOUT_SECONDS", "1200")` etc. with `settings.*`
- `dev_server.py` — replaces its 5 `os.getenv(...)` calls with `settings.*`
- `github_publisher.py` — replaces `GITHUB_TOKEN` / `GITHUB_OWNER` `os.getenv` with `settings.*`
- `agents.py` — changes `from llm_client import llm_call, LLMTask` to `from backend.llm import llm_call, LLMTask`
- `tests/conftest.py` — keeps the `agents.llm_call` and `llm_client.llm_call` patches as defensive shims, removes references to symbols no longer in the shim

**Deleted at end of stage:**
- `llm_client.py` — the compatibility shim; all consumers migrated

**Unchanged:** `backend/llm/*`, `backend/viz_generator/*`, `store.py`, `models.py`, `index.html` (Stage 4), the Dockerfile, `railway.toml`.

---

## Pre-flight check

```bash
cd /Users/pulkitmangal/Universal-visualizer
git checkout main && git pull --ff-only
.venv/bin/pytest tests/ -q
```

Expected: on `main`, 108 passed. Branch the implementation off main as `feat/modularization-stage-3`.

---

## Conventions used in this plan

- Each route move is a small, reversible commit. The contract baseline (12 tests) gates every commit — if any contract test fails, fix or revert before moving the next route.
- Helper function moves into `services/` happen alongside their callers' route move (so we don't leave dangling imports).
- Each new router in `api/` is mounted in `backend/api/__init__.py`; `main.py` calls `mount_routers(app)` once.
- `services/*` modules never import from `api/*`. `api/*` modules never import from each other. Enforced by import-linter once the contract is in place (Task 13).
- Every code block in this plan is the full file content for new files, or a specific patch for modifications. Complete code in every step — no "fill in the rest".

---

## Task 1 — Scaffold `backend/api/` + `backend/services/` + extend `backend/config.py`

**Files:**
- Create: `backend/api/__init__.py`
- Create: `backend/api/deps.py`
- Create: `backend/services/__init__.py`
- Create: `backend/logging_setup.py`
- Create: `tests/unit/api/__init__.py`
- Create: `tests/unit/services/__init__.py`
- Create: `tests/unit/logging/__init__.py`
- Create: `tests/unit/logging/test_structured_formatter.py`
- Modify: `backend/config.py` (extend with routing/Github/dev-server fields)
- Modify: `tests/unit/config/test_settings.py` (add tests for new fields)

- [ ] **Step 1: Extend `backend/config.py`**

Read the current file. Add the new fields to the `Settings` class. The full extended class:

```python
"""Central env var configuration for the backend.

pydantic-settings reads .env and OS env at import time. Every module that
needs config should import `settings` from here. Stage 3 completes the
migration off os.getenv for FastAPI-side env vars.

Note on test-time mutation: pydantic-settings reads env at construction.
The `settings` singleton at module scope is bound once. Tests that need
to override should construct a fresh Settings() inside the test (after
monkeypatching the env), not mutate `settings` directly.

Note: LLM_PROVIDER and MODEL_NAME (used by backend/viz_generator/llm.py)
remain on os.getenv. Migrating them requires deeper restructuring of the
viz_generator package and is not part of Stage 3.
"""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_csv(raw: str | list[str]) -> list[str]:
    """Pydantic-settings field parser: comma-separated strings → list."""
    if isinstance(raw, list):
        return raw
    return [x.strip() for x in raw.split(",") if x.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── OpenAI (Stage 2) ─────────────────────────────────────
    openai_api_key: str = ""
    openai_text_model: str = "gpt-4o-mini"
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 4096
    llm_client_timeout: float = 600.0

    # Per-task model overrides (Stage 2)
    model_agent_a: str | None = None
    model_agent_b: str | None = None
    model_viz_classify: str | None = None
    model_viz_draft: str | None = None
    model_viz_fix: str | None = None
    model_viz_runtime: str | None = None
    model_viz_polish: str | None = None

    # Budgets (Stage 2)
    token_budget_per_job: int = 300_000

    # ── Server (Stage 3) ─────────────────────────────────────
    port: int = 8001
    host: str = "0.0.0.0"
    allowed_origins: list[str] = [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]

    # ── GitHub publish (Stage 3) ─────────────────────────────
    github_token: str | None = None
    github_owner: str | None = None
    publish_to_github: bool = True
    github_include_dist: bool = True
    github_repos_private: bool = False

    # ── Build orchestration (Stage 3) ────────────────────────
    build_timeout_seconds: int = 1200
    viz_output_dir: str = "viz_outputs"
    fixed_main_path: str = "fixed_main_v6.py"

    # ── Dev server (Stage 3) ─────────────────────────────────
    dev_server_port_start: int = 5180
    dev_server_port_end: int = 5230
    preview_boot_wait: int = 45
    npm_install_timeout: int = 300
    audit_fix_enabled: bool = False


settings = Settings()
```

Note: pydantic-settings parses `ALLOWED_ORIGINS=a,b,c` into a list automatically as long as the field type is `list[str]`. Verify this by running the existing test suite — if it doesn't work for CSV strings, add `field_validator` decorators per field.

- [ ] **Step 2: Run existing config tests to confirm no regression**

```bash
.venv/bin/pytest tests/unit/config/ -v
```

Expected: 4 existing tests still pass.

- [ ] **Step 3: Add tests for the new fields**

Append to `tests/unit/config/test_settings.py`:

```python
def test_server_defaults(monkeypatch):
    for name in ("PORT", "HOST", "ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.port == 8001
    assert s.host == "0.0.0.0"
    assert s.allowed_origins == [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]


def test_allowed_origins_csv_parsing(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://a.example.com,https://b.example.com",
    )
    from backend.config import Settings
    s = Settings()
    assert s.allowed_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_github_defaults(monkeypatch):
    for name in ("GITHUB_TOKEN", "GITHUB_OWNER",
                 "PUBLISH_TO_GITHUB", "GITHUB_INCLUDE_DIST",
                 "GITHUB_REPOS_PRIVATE"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.github_token is None
    assert s.github_owner is None
    assert s.publish_to_github is True
    assert s.github_include_dist is True
    assert s.github_repos_private is False


def test_github_repos_private_truthy(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOS_PRIVATE", "true")
    from backend.config import Settings
    s = Settings()
    assert s.github_repos_private is True


def test_dev_server_defaults(monkeypatch):
    for name in ("DEV_SERVER_PORT_START", "DEV_SERVER_PORT_END",
                 "PREVIEW_BOOT_WAIT", "NPM_INSTALL_TIMEOUT",
                 "AUDIT_FIX_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.dev_server_port_start == 5180
    assert s.dev_server_port_end == 5230
    assert s.preview_boot_wait == 45
    assert s.npm_install_timeout == 300
    assert s.audit_fix_enabled is False


def test_build_timeout_override(monkeypatch):
    monkeypatch.setenv("BUILD_TIMEOUT_SECONDS", "600")
    from backend.config import Settings
    s = Settings()
    assert s.build_timeout_seconds == 600
```

Run them. If `test_allowed_origins_csv_parsing` fails because pydantic-settings doesn't auto-parse the CSV, **add a `@field_validator` to `Settings.allowed_origins`** that calls `_parse_csv` and re-run. Lock the behavior either way.

- [ ] **Step 4: Create `backend/logging_setup.py`**

Extract `_StructuredFormatter` from `main.py:76-97`. Read those lines to get the exact implementation, then create:

```python
"""Structured logging setup.

Provides a JSON-ish single-line formatter used by every backend module.
Was previously defined inline at the top of main.py.
"""
from __future__ import annotations

import logging
import sys


class StructuredFormatter(logging.Formatter):
    """Single-line log formatter with timestamp, level, logger name, and message.

    Body verbatim from main.py's pre-Stage-3 _StructuredFormatter. The leading
    underscore is dropped because this is now a public-surface class on a
    dedicated module.
    """
    # ... paste the body from main.py:_StructuredFormatter verbatim ...


def configure_root_logger(level: int = logging.INFO) -> None:
    """Install StructuredFormatter on stdout and set the root logger level.

    Idempotent — re-running replaces existing handlers attached by this
    function. Other handlers (e.g., file handlers added by uvicorn) are
    left alone.
    """
    root = logging.getLogger()
    # Remove only handlers we installed previously
    for h in list(root.handlers):
        if getattr(h, "_added_by", None) == "configure_root_logger":
            root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    handler._added_by = "configure_root_logger"  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
```

- [ ] **Step 5: Create `tests/unit/logging/test_structured_formatter.py`**

```python
"""Unit tests for backend.logging_setup."""
from __future__ import annotations

import logging

from backend.logging_setup import StructuredFormatter, configure_root_logger


def test_format_record_includes_level_and_message():
    fmt = StructuredFormatter()
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    out = fmt.format(record)
    assert "INFO" in out
    assert "hello world" in out
    assert "test.logger" in out


def test_configure_root_logger_idempotent():
    configure_root_logger()
    first_count = len(logging.getLogger().handlers)
    configure_root_logger()
    second_count = len(logging.getLogger().handlers)
    # Idempotent: re-running doesn't multiply handlers we manage
    assert first_count == second_count
```

Tighten the assertions in `test_format_record_includes_level_and_message` based on the actual output of the moved formatter.

- [ ] **Step 6: Create `backend/api/__init__.py`**

```python
"""HTTP API surface.

Each resource has its own router module. The single public function here
(`mount_routers`) wires every router onto the FastAPI app — main.py calls
it once at startup.

Layering contract (enforced by import-linter in Task 13):
  api/ → services/ → {llm, viz_generator, store, models, config}
"""
from __future__ import annotations

from fastapi import FastAPI


def mount_routers(app: FastAPI) -> None:
    """Mount every API router. Called from main.py during app construction."""
    from backend.api import health, jobs, suggestions, builds, manifest, preview, spa
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(suggestions.router)
    app.include_router(builds.router)
    app.include_router(manifest.router)
    app.include_router(preview.router)
    app.include_router(spa.router)
```

This file will start with all the imports failing because the router modules don't exist yet — that's fine. They're created one task at a time (Tasks 4–10). Tasks that haven't landed yet leave their import unresolved; `mount_routers` is not called until Task 12. Until then, `main.py` continues to register routes inline.

- [ ] **Step 7: Create `backend/api/deps.py`**

```python
"""FastAPI dependency helpers shared across routers.

Use these via `Depends(...)` in route signatures to centralize cross-cutting
concerns (job lookup with 404, settings access).
"""
from __future__ import annotations

from fastapi import Depends, HTTPException

from backend.config import settings as _settings
from models import JobState
from store import job_store


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
```

- [ ] **Step 8: Create `tests/unit/api/test_deps.py`**

```python
"""Unit tests for backend.api.deps."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.deps import get_settings, get_job


def test_get_settings_returns_singleton():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_get_job_raises_404_for_unknown(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        get_job("does-not-exist")
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()
```

- [ ] **Step 9: Create `backend/services/__init__.py`**

Empty file with a single docstring:
```python
"""Business-logic services used by HTTP routers.

Layering contract (enforced by import-linter in Task 13):
  services/ never imports from api/. services/ depends only on {llm,
  viz_generator, store, models, config, github_publisher, orchestrator,
  agents, dev_server}.
"""
```

- [ ] **Step 10: Run the full suite + commit**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 108 + new tests (4 config + 2 logging + 2 deps ≈ 116). The new code is purely additive — main.py still works because we haven't called `mount_routers` yet.

```bash
git add backend/api/ backend/services/ backend/logging_setup.py backend/config.py tests/unit/api/ tests/unit/services/ tests/unit/logging/ tests/unit/config/
git commit -m "feat(api,services): scaffold api/ + services/ packages + extend config

Stage 3 scaffolding. Adds:
  - backend/api/{__init__,deps}.py — router mount helper + Depends helpers
  - backend/services/__init__.py — empty package marker
  - backend/logging_setup.py — StructuredFormatter + configure_root_logger
  - backend/config.py extended with routing/Github/dev-server settings

No route moves yet; those follow in Tasks 2-10. main.py untouched."
```

---

## Task 2 — Move `/healthz` to `backend/api/health.py`

The smallest route. Warm-up to the pattern.

**Files:**
- Create: `backend/api/health.py`
- Modify: `main.py` (remove the route handler; add `from backend.api import mount_routers` — but DO NOT call it yet since other routers aren't ready)

Actually, the safer pattern: mount the health router in `main.py` directly via `app.include_router(...)` per-task. This way every step leaves `main.py` runnable. Switch to `mount_routers()` at the very end (Task 12).

- [ ] **Step 1: Read the current `/healthz` handler**

```bash
sed -n '174,189p' main.py
```

Note its imports and what it returns (`HealthResponse`).

- [ ] **Step 2: Create `backend/api/health.py`**

```python
"""GET /healthz — sanity check endpoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from backend.config import settings
from models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Sanity check + report fixed_main_v6.py reachability + active model."""
    fm = Path(settings.fixed_main_path)
    return HealthResponse(
        ok=True,
        fixed_main_path=str(fm),
        fixed_main_exists=fm.exists(),
        text_model=settings.openai_text_model,
        output_dir=settings.viz_output_dir,
    )
```

Important: match the **exact** field shape the existing `main.py:healthz` returns. Read main.py's version, copy the field names verbatim, and only swap `os.getenv` for `settings.*`. If main.py uses different paths for `FIXED_MAIN_PATH` or `VIZ_OUTPUT_DIR` lookup, use the same logic.

- [ ] **Step 3: Mount the router in main.py**

In `main.py`, after `app = FastAPI(...)`:
```python
from backend.api import health as _health  # noqa: E402
app.include_router(_health.router)
```

Then delete the original `@app.get("/healthz")` handler from main.py.

- [ ] **Step 4: Run the contract test**

```bash
.venv/bin/pytest tests/contract/test_health.py -v
.venv/bin/pytest tests/ -q
```

Expected: contract test still passes; suite still green (~116).

- [ ] **Step 5: Commit**

```bash
git add backend/api/health.py main.py
git commit -m "refactor(api): move GET /healthz into backend/api/health.py"
```

---

## Task 3 — Move job-read routes to `backend/api/jobs.py`

Three small routes share the same data model and can move together: `GET /jobs`, `GET /jobs/{job_id}`, `GET /jobs/{job_id}/topics`. The `POST /upload` route also belongs in this module conceptually (job lifecycle) but it has more dependencies; we move it in Task 4.

**Files:**
- Create: `backend/api/jobs.py`
- Modify: `main.py`

- [ ] **Step 1: Read the current handlers**

Lines 273-308 (`list_jobs`, `get_job`, `get_topics`).

- [ ] **Step 2: Create `backend/api/jobs.py` with three routes**

```python
"""Job lifecycle routes.

GET  /jobs                      — list summaries
GET  /jobs/{job_id}             — full job state
GET  /jobs/{job_id}/topics      — extracted topics only

POST /upload moves here in a later task (Task 4) so all job routes live together.
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
    # Body verbatim from main.py's list_jobs. Read main.py:274-287, copy the
    # implementation. Use job_store.list_summaries() and convert each to a
    # JobSummary in the same way the original does.
    ...


@router.get("/jobs/{job_id}", response_model=JobState)
def get_job_state(job: JobState = Depends(get_job)) -> JobState:
    """Return full job state — the polling endpoint."""
    return job


@router.get("/jobs/{job_id}/topics")
def get_topics(job: JobState = Depends(get_job)) -> dict:
    """Return just the extracted topics for a job."""
    # Body verbatim from main.py:297-307.
    ...
```

Read each function in main.py and copy its body exactly. The signatures change minimally — `get_job_state` and `get_topics` use the `get_job` dependency injection instead of inline `job_store.get` + raise. Both produce equivalent observable behavior (404 on missing job).

- [ ] **Step 3: Mount the router in main.py**

```python
from backend.api import jobs as _jobs  # noqa: E402
app.include_router(_jobs.router)
```

Delete the three original `@app.get(...)` handlers (`list_jobs`, `get_job`, `get_topics`) from main.py.

- [ ] **Step 4: Run contract tests + full suite**

```bash
.venv/bin/pytest tests/contract/test_jobs.py -v
.venv/bin/pytest tests/ -q
```

Expected: all 3 job contract tests pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/api/jobs.py main.py
git commit -m "refactor(api): move GET /jobs* read routes into backend/api/jobs.py

list_jobs, get_job_state, get_topics now use the get_job dependency
for consistent 404 handling. POST /upload follows in the next task."
```

---

## Task 4 — Move `POST /upload` into `backend/api/jobs.py`

The most complex route handler (~80 lines in main.py:190-272). It validates file extension, parses the .md, generates a job_id, runs Agent A inline via `topic_extraction_agent`, persists the job, and returns the `UploadResponse`.

**Files:**
- Modify: `backend/api/jobs.py` (add the upload route)
- Modify: `main.py`

- [ ] **Step 1: Read `main.py:upload_script` (lines 190-272)**

Note all its imports + dependencies. It calls:
- `topic_extraction_agent(...)` from `agents`
- `assemble_viz_brief(...)` (or similar) — read to confirm
- `job_store.add(...)` from `store`
- Uses constants like `MAX_UPLOAD_BYTES` if any

- [ ] **Step 2: Append the route to `backend/api/jobs.py`**

```python
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import File, Form, HTTPException, UploadFile

from agents import topic_extraction_agent
from models import (
    ExtractedTopic,
    JobState,
    JobStatus,
    UploadResponse,
)


@router.post("/upload", response_model=UploadResponse)
async def upload_script(
    file: UploadFile = File(...),
    track: str = Form("Academy DSA"),
) -> UploadResponse:
    """Upload a .md script, run Agent A inline, return job summary."""
    # Body verbatim from main.py:upload_script. Reproduce exactly:
    #   - filename check
    #   - file read
    #   - job_id generation
    #   - topic_extraction_agent call
    #   - JobState construction
    #   - job_store.add
    #   - return UploadResponse(...)
    ...
```

Read the original carefully and reproduce every line, only changing the import paths if they were qualified from `main` module.

- [ ] **Step 3: Mount is already done in Task 3 (same router)**

The jobs router is already mounted. No new mount needed. Just delete the original `@app.post("/upload")` handler in main.py.

- [ ] **Step 4: Run contract tests**

```bash
.venv/bin/pytest tests/contract/test_upload.py tests/contract/test_jobs.py -v
.venv/bin/pytest tests/ -q
```

Expected: all upload + jobs contract tests pass; full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/api/jobs.py main.py
git commit -m "refactor(api): move POST /upload into backend/api/jobs.py"
```

---

## Task 5 — Move `/suggestions` to `backend/api/suggestions.py`

**Files:**
- Create: `backend/api/suggestions.py`
- Modify: `main.py`

- [ ] **Step 1: Read main.py:309-364 (`get_topic_suggestions`)**

It calls `viz_suggestion_agent`. Note the in-memory cache behavior (suggestions are cached on the JobState after the first call — important for cost).

- [ ] **Step 2: Create `backend/api/suggestions.py`**

```python
"""POST /jobs/{job_id}/topics/{topic_id}/suggestions — Agent B (cached)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from agents import viz_suggestion_agent
from backend.api.deps import get_job
from models import JobState, VizSuggestion

router = APIRouter(tags=["suggestions"])


@router.post("/jobs/{job_id}/topics/{topic_id}/suggestions")
async def get_topic_suggestions(
    topic_id: str,
    job: JobState = Depends(get_job),
) -> dict:
    """Run Agent B for one topic (cached after first call)."""
    # Body verbatim from main.py:get_topic_suggestions. Preserve:
    #   - 404 if topic_id not in job.topics
    #   - cache check (job.suggestions.get(topic_id))
    #   - viz_suggestion_agent call on cache miss
    #   - persist suggestions to job.suggestions
    #   - status transitions if relevant
    ...
```

- [ ] **Step 3: Mount + delete original**

```python
from backend.api import suggestions as _suggestions  # noqa: E402
app.include_router(_suggestions.router)
```

Delete the original handler.

- [ ] **Step 4: Verify + commit**

```bash
.venv/bin/pytest tests/contract/test_suggestions.py -v
.venv/bin/pytest tests/ -q
git add backend/api/suggestions.py main.py
git commit -m "refactor(api): move POST /suggestions into backend/api/suggestions.py"
```

---

## Task 6 — Extract `_run_build_task` to `services/build_orchestrator.py` + move `/build` route

The biggest extraction. `_run_build_task` is ~122 lines (main.py:439-561) — it spawns the viz subprocess, monitors progress, optionally publishes to GitHub, and updates job state through several phases.

**Files:**
- Create: `backend/services/build_orchestrator.py`
- Create: `backend/api/builds.py`
- Create: `tests/unit/services/test_build_orchestrator.py`
- Modify: `main.py`

- [ ] **Step 1: Read main.py:_run_build_task (lines 439-561) and main.py:build_topic_viz (lines 366-438)**

Note dependencies:
- `orchestrator.run_subprocess(...)` or similar (from `orchestrator.py`)
- `github_publisher.publish(...)` (gated by `settings.publish_to_github` + `settings.github_token`)
- `services/manifest_builder._build_manifest` (will move in Task 7)
- `dev_server.*` (only if the original starts a dev server during build)
- `job_store.update(...)`

- [ ] **Step 2: Create `backend/services/build_orchestrator.py`**

```python
"""Background build orchestration.

run_build_task() is invoked by FastAPI's BackgroundTasks when the user
picks a viz suggestion. It spawns fixed_main_v6.py (now a stub for
backend.viz_generator.cli) as a subprocess, streams its progress into
the JobState, and optionally publishes the result to GitHub.

Was previously _run_build_task in main.py.
"""
from __future__ import annotations

import logging
from pathlib import Path

# Add the imports the function actually needs — read main.py's _run_build_task
# imports and reproduce them. Use settings.* in place of any os.getenv calls.
from backend.config import settings
from models import BuildPhase, BuildTask, JobState, JobStatus
from store import job_store

logger = logging.getLogger("hackmd-orch.build")


def run_build_task(job_id: str, topic_id: str) -> None:
    """Run a single viz build end-to-end. Updates the JobState as it progresses.

    Body verbatim from main.py:_run_build_task. The leading underscore is
    dropped because this is now a public service function. Any os.getenv
    calls inside the original are replaced with settings.* accesses.
    """
    # ... reproduce main.py:_run_build_task line by line ...
```

If the original function imports `_build_manifest` from `main`, replace with `from backend.services.manifest_builder import build_manifest` — but only after Task 7 lands. For now, keep the local `main._build_manifest` import via a deferred import inside the function, or split this task into 6a (extract function with old name) and 6b (rename after Task 7). The pragmatic choice: do them in order — Task 7 first, then Task 6.

**Reorder note**: If reading the code reveals that `_run_build_task` calls `_build_manifest`, swap the order — do Task 7 (manifest extract) before Task 6 (build orchestrator extract). The plan as written assumes they're independent.

- [ ] **Step 3: Create `backend/api/builds.py`**

```python
"""POST /jobs/{job_id}/topics/{topic_id}/build — queue a viz build."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from backend.api.deps import get_job
from backend.services.build_orchestrator import run_build_task
from models import BuildRequest, BuildTask, JobState

router = APIRouter(tags=["builds"])


@router.post("/jobs/{job_id}/topics/{topic_id}/build")
async def build_topic_viz(
    topic_id: str,
    body: BuildRequest,
    background: BackgroundTasks,
    job: JobState = Depends(get_job),
) -> dict:
    """Pick a suggestion (or custom notes) and queue a build."""
    # Body verbatim from main.py:build_topic_viz, with the BackgroundTasks
    # call site updated to use `run_build_task` (the new name).
    ...
```

- [ ] **Step 4: Write unit tests for `run_build_task`**

Create `tests/unit/services/test_build_orchestrator.py`:

```python
"""Unit tests for backend.services.build_orchestrator.run_build_task.

External systems (subprocess, github_publisher) are mocked. The goal is to
verify that phase transitions and JobState mutations happen correctly under
each branch (success, build failure, github publish disabled, github push
failure).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend.services.build_orchestrator import run_build_task
from models import BuildTask, JobState, JobStatus
from store import job_store


def _seed_job_with_topic_and_build(job_store_fixture) -> tuple[str, str]:
    # Construct a JobState with one topic + one BuildTask in queued phase.
    # Adapt to the actual JobState shape — read models.py.
    ...


@patch("backend.services.build_orchestrator.subprocess.run")
def test_successful_subprocess_transitions_phases(mock_run):
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    # ... drive run_build_task, assert phase ends at "completed"
    ...


@patch("backend.services.build_orchestrator.subprocess.run")
def test_subprocess_failure_marks_failed(mock_run):
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    # ... assert phase == "failed", error captured
    ...
```

These tests will only be precise after you read the actual run_build_task code. Adjust the mock targets (`subprocess.run` is a guess) to match what the function calls. Keep at least 2 tests — the success path and one failure path.

- [ ] **Step 5: Mount + delete originals**

```python
from backend.api import builds as _builds  # noqa: E402
app.include_router(_builds.router)
```

Delete `@app.post("/jobs/{job_id}/topics/{topic_id}/build")` and `_run_build_task` from main.py.

- [ ] **Step 6: Verify + commit**

```bash
.venv/bin/pytest tests/contract/test_build.py tests/unit/services/test_build_orchestrator.py -v
.venv/bin/pytest tests/ -q
git add backend/services/build_orchestrator.py backend/api/builds.py tests/unit/services/test_build_orchestrator.py main.py
git commit -m "refactor(services): extract _run_build_task → services/build_orchestrator.py + api/builds.py"
```

---

## Task 7 — Extract `_build_manifest` to `services/manifest_builder.py` + move `/manifest` route

**Files:**
- Create: `backend/services/manifest_builder.py`
- Create: `backend/api/manifest.py`
- Create: `tests/unit/services/test_manifest_builder.py`
- Modify: `main.py`
- Modify: `backend/services/build_orchestrator.py` (if it imports the old `_build_manifest`)

- [ ] **Step 1: Read main.py:_build_manifest (lines 562-596) + main.py:get_manifest (lines 597-630)**

`_build_manifest` is a pure function: given a `JobState`, return `list[EmbedManifestEntry]`. Easy to test.

- [ ] **Step 2: Create `backend/services/manifest_builder.py`**

```python
"""Build the final embed manifest for a completed job.

Pure function — given a JobState, produces the list of EmbedManifestEntry
records that the content creator drops into the source script.
"""
from __future__ import annotations

from models import BuildPhase, EmbedManifestEntry, JobState


def build_manifest(job: JobState) -> list[EmbedManifestEntry]:
    """Return the manifest entries for a job's completed builds.

    Body verbatim from main.py:_build_manifest.
    """
    # ... copy the body, drop the leading underscore on the function name ...
```

- [ ] **Step 3: Update `backend/services/build_orchestrator.py` if needed**

If Task 6's `run_build_task` imports `_build_manifest` from main, change it to `from backend.services.manifest_builder import build_manifest`.

- [ ] **Step 4: Create `backend/api/manifest.py`**

```python
"""GET /jobs/{job_id}/manifest — final embed manifest."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.api.deps import get_job
from backend.services.manifest_builder import build_manifest
from models import JobState

router = APIRouter(tags=["manifest"])


@router.get("/jobs/{job_id}/manifest")
def get_manifest(job: JobState = Depends(get_job)) -> dict:
    """Return the final embed manifest + token usage totals."""
    # Body verbatim from main.py:get_manifest, calling build_manifest()
    # instead of the local _build_manifest.
    ...
```

- [ ] **Step 5: Write unit tests**

Create `tests/unit/services/test_manifest_builder.py`:

```python
"""Unit tests for backend.services.manifest_builder.build_manifest."""
from __future__ import annotations

from datetime import datetime

from backend.services.manifest_builder import build_manifest
from models import (
    BuildPhase,
    BuildTask,
    EmbedManifestEntry,
    ExtractedTopic,
    JobState,
    JobStatus,
)


def _fixture_job(num_topics: int = 1, all_completed: bool = True) -> JobState:
    """Hand-build a JobState with N topics and matching BuildTasks."""
    # Read models.py for the exact field shapes. Construct topics and
    # builds with phase = "completed" by default.
    ...


def test_manifest_for_single_completed_build(...):
    job = _fixture_job(num_topics=1, all_completed=True)
    out = build_manifest(job)
    assert len(out) == 1
    assert out[0].status == "ok"


def test_manifest_skips_unfinished_builds(...):
    job = _fixture_job(num_topics=2, all_completed=False)
    # Adjust one BuildTask to phase != "completed"
    # build_manifest should mark it as "failed" or skip — match observed
    ...


def test_manifest_empty_for_no_builds(...):
    job = _fixture_job(num_topics=0, all_completed=False)
    out = build_manifest(job)
    assert out == []
```

Run them. Tighten assertions to match the actual function's behavior — read main.py's `_build_manifest` to know what it does on edge cases.

- [ ] **Step 6: Mount + delete originals**

```python
from backend.api import manifest as _manifest  # noqa: E402
app.include_router(_manifest.router)
```

Delete `@app.get(".../manifest")` and `_build_manifest` from main.py.

- [ ] **Step 7: Verify + commit**

```bash
.venv/bin/pytest tests/contract/test_manifest.py tests/unit/services/test_manifest_builder.py -v
.venv/bin/pytest tests/ -q
git add backend/services/manifest_builder.py backend/api/manifest.py tests/unit/services/test_manifest_builder.py main.py
git commit -m "refactor(services): extract _build_manifest → services/manifest_builder.py + api/manifest.py"
```

---

## Task 8 — Move `/preview` to `backend/api/preview.py`

**Files:**
- Create: `backend/api/preview.py`
- Modify: `main.py`

- [ ] **Step 1: Read main.py:preview_file (lines 631-654)**

It serves files from `VIZ_OUTPUT_DIR` while rejecting path traversal.

- [ ] **Step 2: Create `backend/api/preview.py`**

```python
"""GET /preview — serve screenshot/preview files from VIZ_OUTPUT_DIR."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.config import settings

router = APIRouter(tags=["preview"])


@router.get("/preview")
def preview_file(path: str) -> FileResponse:
    """Serve a file from VIZ_OUTPUT_DIR with path-traversal protection."""
    # Body verbatim from main.py:preview_file, using settings.viz_output_dir.
    ...
```

- [ ] **Step 3: Mount + delete + verify + commit**

```python
from backend.api import preview as _preview  # noqa: E402
app.include_router(_preview.router)
```

```bash
.venv/bin/pytest tests/contract/test_preview.py -v
.venv/bin/pytest tests/ -q
git add backend/api/preview.py main.py
git commit -m "refactor(api): move GET /preview into backend/api/preview.py"
```

---

## Task 9 — Move `GET /` (SPA) to `backend/api/spa.py`

**Files:**
- Create: `backend/api/spa.py`
- Modify: `main.py`

- [ ] **Step 1: Read main.py:index (lines 655-665)**

It serves `index.html` at the project root.

- [ ] **Step 2: Create `backend/api/spa.py`**

```python
"""GET / — serve the React frontend (index.html at project root).

When Stage 4 lands, this returns built static assets from backend/static/
instead. For now it serves the legacy CDN-React SPA verbatim.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["spa"])


_INDEX_PATH = Path(__file__).resolve().parents[2] / "index.html"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the SPA HTML."""
    return HTMLResponse(_INDEX_PATH.read_text(encoding="utf-8"))
```

Adjust `_INDEX_PATH` if `__file__` resolution from inside the package doesn't reach the project root. Verify by running the test.

- [ ] **Step 3: Mount + delete + verify + commit**

```python
from backend.api import spa as _spa  # noqa: E402
app.include_router(_spa.router)
```

```bash
.venv/bin/pytest tests/ -q
OPENAI_API_KEY=sk-test .venv/bin/python -c "
from fastapi.testclient import TestClient
from main import app
r = TestClient(app).get('/')
assert r.status_code == 200
assert '<title>' in r.text
print('OK SPA route')
"
git add backend/api/spa.py main.py
git commit -m "refactor(api): move GET / SPA route into backend/api/spa.py"
```

---

## Task 10 — Migrate remaining `os.getenv` calls to settings

**Files modified:**
- `main.py`
- `orchestrator.py`
- `dev_server.py`
- `github_publisher.py`

- [ ] **Step 1: Sweep all remaining `os.getenv` calls**

```bash
grep -n "os\.getenv\b" main.py orchestrator.py dev_server.py github_publisher.py | grep -v "^.*#"
```

Expected hits (per Stage 3 audit):
- `main.py`: PUBLISH_TO_GITHUB, GITHUB_INCLUDE_DIST, GITHUB_REPOS_PRIVATE, ALLOWED_ORIGINS, GITHUB_TOKEN, PORT, HOST
- `orchestrator.py`: BUILD_TIMEOUT_SECONDS + ~2 others (read the file)
- `dev_server.py`: DEV_SERVER_PORT_START/END, PREVIEW_BOOT_WAIT, NPM_INSTALL_TIMEOUT, AUDIT_FIX_ENABLED
- `github_publisher.py`: GITHUB_TOKEN, GITHUB_OWNER

- [ ] **Step 2: Replace each call site**

Add `from backend.config import settings` to each file's imports. Replace each `os.getenv(...)` with the corresponding `settings.<field>` access.

Field mapping reminders (from Task 1's extended Settings):
- `PUBLISH_TO_GITHUB` → `settings.publish_to_github`
- `GITHUB_INCLUDE_DIST` → `settings.github_include_dist`
- `GITHUB_REPOS_PRIVATE` → `settings.github_repos_private`
- `ALLOWED_ORIGINS` → `settings.allowed_origins`
- `GITHUB_TOKEN` → `settings.github_token`
- `GITHUB_OWNER` → `settings.github_owner`
- `PORT` → `settings.port`
- `HOST` → `settings.host`
- `BUILD_TIMEOUT_SECONDS` → `settings.build_timeout_seconds`
- `VIZ_OUTPUT_DIR` → `settings.viz_output_dir`
- `FIXED_MAIN_PATH` → `settings.fixed_main_path`
- `DEV_SERVER_PORT_START` → `settings.dev_server_port_start`
- `DEV_SERVER_PORT_END` → `settings.dev_server_port_end`
- `PREVIEW_BOOT_WAIT` → `settings.preview_boot_wait`
- `NPM_INSTALL_TIMEOUT` → `settings.npm_install_timeout`
- `AUDIT_FIX_ENABLED` → `settings.audit_fix_enabled`

If a module currently casts the env var (`int(os.getenv("X", "1"))`), the equivalent settings access is already typed — drop the cast.

Leave `os.environ` mutations and `os.environ.copy()` calls alone — those are legitimately runtime concerns (e.g., orchestrator constructing env for the subprocess).

- [ ] **Step 3: Remove now-dead imports**

If a file's only use of `import os` was for `os.getenv`, remove the import. If it still uses `os.environ`, `os.path`, etc., keep it.

- [ ] **Step 4: Run the full suite + boot smoke test**

```bash
.venv/bin/pytest tests/ -q
OPENAI_API_KEY=sk-test .venv/bin/python -c "from main import app; print(app.title)"
```

Expected: all tests pass; app boots.

- [ ] **Step 5: Confirm zero remaining `os.getenv` in backend-side files (excluding deliberate exceptions)**

```bash
grep -rn "os\.getenv\b" main.py orchestrator.py dev_server.py github_publisher.py agents.py backend/api/ backend/services/ backend/llm/client.py backend/llm/tracker.py
```

Expected: **no output**. Deliberate exceptions kept on `os.getenv`:
- `backend/llm/tasks.py::resolve_model` (per-call env reads for runtime override flexibility — established in Stage 1)
- `backend/viz_generator/llm.py` (multi-provider; deferred until viz subprocess config migration)

If anything else turns up, decide: migrate to settings now, or document as a deliberate exception.

- [ ] **Step 6: Commit**

```bash
git add main.py orchestrator.py dev_server.py github_publisher.py
git commit -m "refactor(config): migrate remaining os.getenv calls to backend.config.settings

Covers CORS, GitHub publish flags, host/port, build timeout, dev-server
config. Same env var names → no Railway dashboard change required.

Deliberate exceptions kept on os.getenv (documented in backend/config.py):
  - backend/llm/tasks.py::resolve_model — per-call flexibility
  - backend/viz_generator/llm.py — multi-provider; deferred"
```

---

## Task 11 — Delete the `llm_client.py` shim

After Stages 1 and 2, only two files outside tests still import from `llm_client`: `agents.py` and `main.py`. This task migrates them to direct `backend.llm` imports, then deletes the shim.

**Files modified:**
- `agents.py` — `from llm_client import llm_call, LLMTask` → `from backend.llm import llm_call, LLMTask`
- `main.py` — `from llm_client import TEXT_MODEL, TOKEN_BUDGET_PER_JOB, REASONING_EFFORT, is_reasoning_model, token_tracker` → split into direct imports

**Files deleted:**
- `llm_client.py`

- [ ] **Step 1: Migrate `agents.py`**

Change line 23:
```python
# Before:
from llm_client import llm_call, LLMTask
# After:
from backend.llm import llm_call, LLMTask
```

- [ ] **Step 2: Migrate `main.py`'s shim usage**

Read the current import block (around `main.py:39-45`). Replace with:
```python
from backend.llm import is_reasoning_model, token_tracker
from backend.config import settings
```

Replace any remaining references in main.py:
- `TEXT_MODEL` → `settings.openai_text_model`
- `TOKEN_BUDGET_PER_JOB` → `settings.token_budget_per_job`
- `REASONING_EFFORT` → `settings.reasoning_effort`

These should already be down to zero or near-zero after Task 10 — that task removed most usage. Sweep what remains:
```bash
grep -nE "\bTEXT_MODEL\b|\bTOKEN_BUDGET_PER_JOB\b|\bREASONING_EFFORT\b" main.py
```

Replace each with the settings access.

- [ ] **Step 3: Confirm no other module imports from `llm_client`**

```bash
grep -rn "from llm_client\|import llm_client" --include="*.py" | grep -v "^tests/" | grep -v ".venv/" | grep -v "__pycache__/"
```

Expected: empty output (after the migrations in Steps 1–2 land).

- [ ] **Step 4: Update `tests/conftest.py` fake_llm fixture**

The fixture currently patches BOTH `backend.llm.client.llm_call` and `llm_client.llm_call`. After deleting the shim, the `llm_client` patch will fail. Read the conftest and remove the `llm_client.llm_call` patch line; keep the `backend.llm.client.llm_call` and `agents.llm_call` patches.

```bash
sed -n '40,55p' tests/conftest.py
```

Then remove:
```python
# Also patch the legacy shim so `from llm_client import llm_call` works.
monkeypatch.setattr("llm_client.llm_call", llm_call_stub, raising=False)
```

(Leave `agents.llm_call` patch — it's needed because `from backend.llm import llm_call` in agents.py binds the name at import time.)

- [ ] **Step 5: Delete `llm_client.py`**

```bash
git rm llm_client.py
```

- [ ] **Step 6: Run the full suite**

```bash
.venv/bin/pytest tests/ -q
OPENAI_API_KEY=sk-test .venv/bin/python -c "from main import app; print(app.title)"
.venv/bin/python fixed_main_v6.py --help 2>&1 | head -3
```

Expected: all tests still pass; app boots; subprocess help works.

- [ ] **Step 7: Commit**

```bash
git add agents.py main.py tests/conftest.py
git commit -m "refactor(llm): delete llm_client.py shim; all consumers import from backend.llm

Final Stage 1 → Stage 3 cleanup. The compatibility shim served its
purpose during the LLM client extraction and the route migrations.
With every consumer (agents.py, main.py) on direct backend.llm
imports, the shim is no longer needed."
```

---

## Task 12 — Reduce `main.py` to ~100 lines

After Tasks 2-11, `main.py` should be largely stripped already. This task finalizes the app-factory pattern and uses `mount_routers()` for a clean structure.

- [ ] **Step 1: Inspect current `main.py`**

```bash
wc -l main.py
grep -nE "^@app\.|^def |^async def |^class " main.py
```

Expected: ~150-200 lines remaining (imports + app creation + startup hook + middleware + uvicorn entry). Should have NO `@app.<method>` decorators left — all routes are in api/ routers.

- [ ] **Step 2: Refactor to the `create_app()` factory pattern**

Restructure `main.py` to:

```python
"""FastAPI orchestrator entry point.

This module is the thinnest possible app factory + uvicorn entry. All
route handlers live under backend/api/. All business logic lives under
backend/services/. All env config lives in backend/config.py. All LLM
machinery lives in backend/llm/. The viz generator lives in
backend/viz_generator/.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv

# Load .env BEFORE constructing Settings or importing modules that read env.
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api import mount_routers
from backend.config import settings
from backend.logging_setup import configure_root_logger
from store import job_store


configure_root_logger(level=logging.INFO)
logger = logging.getLogger("hackmd-orch")


def create_app() -> FastAPI:
    """Construct and return the FastAPI app.

    Kept as a function (rather than a module-level instance) so tests and
    future multi-app embedding can call it explicitly. The default app
    instance below — `app` — is what uvicorn imports.
    """
    application = FastAPI(
        title="HackMD Visualization Orchestrator",
        description="Upload a HackMD lecture script, get embed-ready visualizations.",
        version="0.2.0",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.on_event("startup")
    def _on_startup() -> None:
        logger.info(
            "[startup] model=%s token_budget=%d viz_output=%s",
            settings.openai_text_model,
            settings.token_budget_per_job,
            settings.viz_output_dir,
        )
        # Periodic stale-job purge (job_store TTL eviction).
        # If main.py had a background thread for this, port the call here.
        job_store.purge_stale()

    mount_routers(application)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,  # we set up logging ourselves via configure_root_logger
    )
```

Important: if the original `_on_startup` did more (e.g., warmed clients, set up background tasks beyond `job_store.purge_stale()`), reproduce every line. Don't silently drop behavior. Read main.py:151-173 first.

- [ ] **Step 3: Verify line count and structure**

```bash
wc -l main.py
grep -nE "^@app\.|^@router\." main.py
```

Expected: under ~100 lines; zero route decorators.

- [ ] **Step 4: Full suite + boot smoke test + subprocess test**

```bash
.venv/bin/pytest tests/ -q
OPENAI_API_KEY=sk-test .venv/bin/python -c "from main import app, create_app; a = create_app(); print(a.title)"
.venv/bin/python fixed_main_v6.py --help 2>&1 | head -3
```

Expected: full suite green; app boots via both module-level `app` and `create_app()`; subprocess unchanged.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "refactor(main): reduce to create_app() factory + mount_routers + uvicorn entry

main.py is now ~100 lines. All routes live under backend/api/. All
business logic lives under backend/services/. Env config flows through
backend/config.py. Logging via backend/logging_setup."
```

---

## Task 13 — Add import-linter contract

Lock the layering so future PRs can't silently reverse-import.

**Files:**
- Modify: `requirements-dev.txt` (add `import-linter>=2.0`)
- Create: `importlinter.toml` (or `.importlinter` — the latter is what import-linter looks for by default; use `setup.cfg`-style `.importlinter` config)
- Modify: CI workflow if present (Stage 1's `make verify-compat` plan)

- [ ] **Step 1: Install import-linter**

```bash
grep "import-linter" requirements-dev.txt
```

If missing, append `import-linter>=2.0` to `requirements-dev.txt`, then:
```bash
.venv/bin/pip install -q "import-linter>=2.0"
```

- [ ] **Step 2: Create `.importlinter` at repo root**

```ini
[importlinter]
root_packages =
    backend
    main
    agents
    orchestrator
    store
    models
    github_publisher
    dev_server

[importlinter:contract:1]
name = api → services only (never the reverse)
type = forbidden
source_modules =
    backend.services
forbidden_modules =
    backend.api

[importlinter:contract:2]
name = backend.llm depends on nothing else under backend
type = forbidden
source_modules =
    backend.llm
forbidden_modules =
    backend.api
    backend.services
    backend.viz_generator

[importlinter:contract:3]
name = api/ and services/ never import from main.py
type = forbidden
source_modules =
    backend.api
    backend.services
forbidden_modules =
    main
```

If import-linter's tomls/inis behave differently in practice, adjust syntax. The contracts above lock three invariants:
1. `services/` can't reach into `api/` (UI flow direction)
2. `backend/llm/` is a leaf (nothing in `backend.api`, `backend.services`, `backend.viz_generator` is allowed to be imported from inside it)
3. Routers and services can't import `main` (avoids circular runtime)

- [ ] **Step 3: Run import-linter**

```bash
.venv/bin/lint-imports
```

Expected: all contracts pass. If a violation appears, **fix the violation**, don't loosen the contract. Common violations:
- `services/build_orchestrator.py` imports from `main` for something — fix by extracting the symbol or moving it
- `backend/llm/` reaches into `backend.viz_generator` — fix the dep direction

- [ ] **Step 4: Add a tiny test that runs import-linter**

Create `tests/contract/test_import_layering.py`:

```python
"""Import-linter contract enforcement runs in the test suite.

Fails the build if any forbidden cross-package import appears.
"""
from __future__ import annotations

import subprocess
import sys


def test_lint_imports_contract_passes():
    r = subprocess.run(
        [sys.executable, "-m", "importlinter.cli.lint_imports"],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        print("lint-imports stdout:\n" + r.stdout)
        print("lint-imports stderr:\n" + r.stderr)
    assert r.returncode == 0, "import-linter contract violation"
```

- [ ] **Step 5: Verify + commit**

```bash
.venv/bin/pytest tests/contract/test_import_layering.py -v
.venv/bin/pytest tests/ -q
git add requirements-dev.txt .importlinter tests/contract/test_import_layering.py
git commit -m "feat(arch): enforce api → services → core import layering via import-linter

Three contracts:
  1. services/ never imports from api/
  2. backend/llm is a leaf (no reverse imports from llm into api/services/viz_generator)
  3. api/ and services/ never import main.py

Contract violations fail CI via tests/contract/test_import_layering.py."
```

---

## Task 14 — Final verification + tag

- [ ] **Step 1: Full suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: ~125+ tests passing (108 from Stage 2 + ~17 new from Stage 3: 6 config + 2 logging + 2 deps + ~4 manifest_builder + ~3 build_orchestrator + 1 import-linter).

- [ ] **Step 2: App boots cleanly**

```bash
OPENAI_API_KEY=sk-test .venv/bin/python -c "
from main import app, create_app
a = create_app()
print('OK', a.title, '— routes:', len(a.routes))
"
```

- [ ] **Step 3: Subprocess argv contract intact**

```bash
.venv/bin/python fixed_main_v6.py --help 2>&1 | head -10
```

- [ ] **Step 4: HTTP smoke test against the live app**

```bash
OPENAI_API_KEY=sk-test .venv/bin/uvicorn main:app --port 18003 &
sleep 3
curl -s http://127.0.0.1:18003/healthz | head
curl -s http://127.0.0.1:18003/jobs | head
kill %1 2>/dev/null
```

Expected: JSON responses on both endpoints.

- [ ] **Step 5: No leftover route decorators in main.py**

```bash
grep -nE "^@app\.|^@router\." main.py
```

Expected: no output.

- [ ] **Step 6: Import-linter passes**

```bash
.venv/bin/lint-imports
```

- [ ] **Step 7: Confirm `llm_client.py` is gone**

```bash
test -f llm_client.py && echo "ERROR: still exists" || echo "OK: deleted"
```

- [ ] **Step 8: Tag the cut**

```bash
git tag -a modularization-stage-3 -m "Stage 3 of the modularization project — api/ + services/

Decomposed main.py (was 669 lines, now ~100) into:
  - backend/api/ (one router per resource — health, jobs,
    suggestions, builds, manifest, preview, spa)
  - backend/services/ (build_orchestrator, manifest_builder)
  - backend/logging_setup.py (StructuredFormatter)
  - backend/config.py extended (routing, Github, dev-server settings)

Final llm_client.py shim deleted; agents.py and main.py now import
backend.llm directly.

Import-linter contract enforces api → services → core layering. CI
fails on any reverse import.

Tests: 125+ passing."
```

---

## Self-review (run before sending the plan)

**Spec coverage:**

| Spec requirement | Implementing task |
|---|---|
| `backend/api/` package (Section 1) | Tasks 1, 2–9 |
| `backend/services/` package (Section 1) | Tasks 6, 7 |
| `backend/logging_setup.py` (Section 1, Section 2 "Structured logging") | Task 1 |
| `backend/config.py` extended (Section 2 "Configuration consolidated") | Tasks 1, 10 |
| Delete `llm_client.py` shim (Section 4 Stage 3) | Task 11 |
| Reduce `main.py` to app factory + routers + middleware (Section 4 Stage 3 target ≤ 100 lines) | Task 12 |
| `import-linter` contract enforced (Section 2 maintainability bullet) | Task 13 |
| Subprocess CLI contract preserved (Section 4) | Implicit — main.py changes don't touch subprocess spawn; Stage 2's contract test guards it |
| HTTP API surface unchanged (Stable contracts) | Stage 1's 12 contract tests run on every commit |

**Deferred (with task / plan owner identified):**

- Stage 4: frontend SPA migration to Vite + React + TS
- Performance optimizations from Section 2 (Stage-2b follow-up)
- viz_generator's `LLM_PROVIDER` / `MODEL_NAME` migration to `Settings` (deeper restructuring; later stage)

**Placeholder scan:** Two tasks (6, 7) have function-body extractions where the plan says "Body verbatim from main.py:N-M" rather than pasting the body. This is intentional — pasting 120 lines of build orchestration into the plan is noise. The implementer reads the source location and copies. The remaining `...` placeholders inside `def` bodies are clearly marked as "reproduce main.py:foo verbatim" — the implementer knows what to do.

**Type consistency:** `Settings` field names match the env var names via auto-lowercasing (`ALLOWED_ORIGINS` ↔ `allowed_origins`). `Router` mount pattern is consistent across all 7 router files. `Depends(get_job)` shape is the same wherever it's used. `JobState` and `BuildTask` model types are the same across all imports.

**Risk hotspots called out:**

- Task 6 (build_orchestrator extraction) is the highest risk. It touches background tasks + subprocess + GitHub publish + several JobState transitions. The unit tests are intentionally light (subprocess mocked); the real safety net is the existing E2E manual workflow.
- Task 10 (os.getenv sweep) touches `orchestrator.py` and `dev_server.py` which have no contract tests. Boot smoke + `python fixed_main_v6.py --help` are the only automated checks; a manual end-to-end build is recommended before merge.
- Task 11 (shim deletion) is final; a partial revert is the rollback path. Confirmed safe because every consumer migrates first.
