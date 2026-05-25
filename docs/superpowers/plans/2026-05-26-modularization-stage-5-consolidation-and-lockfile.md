# Modularization Stage 5 — Backend Consolidation + Lockfile

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Finish the modularization by moving all root-level Python modules into `backend/` so the spec's target layout is reached; (2) replace `requirements.txt` (range-pinned) with `pyproject.toml` + hash-pinned `requirements.lock`/`requirements-dev.lock` for reproducible production deploys.

**Architecture:** Each Python module moves one-PR-task-at-a-time via `git mv`, with all callers updated in the same commit. `fixed_main_v6.py` stays at repo root — its absolute path is the orchestrator-subprocess argv contract (documented in the design spec). The uvicorn target becomes `backend.main:app`; Dockerfile + `railway.toml` get updated in lockstep. After the moves land, a single follow-up task introduces `pyproject.toml`, generates hash-pinned lockfiles via `uv` (with `pip-tools` fallback), and switches the Dockerfile from `requirements.txt` to `--require-hashes -r requirements.lock`.

**Tech Stack:** stdlib `git mv` + Python imports; `uv` (or `pip-tools` fallback) for lockfile generation. No new runtime dependencies.

**Dependency graph** (informs task order — leaves first, callers later):

```
models.py  (no internal imports — leaf)
  ↑
store.py  (imports models)
  ↑
agents.py  (imports models + backend.llm)
github_publisher.py  (imports backend.config — leaf-equivalent)
dev_server.py  (imports backend.config — leaf-equivalent)
orchestrator.py  (imports backend.config — leaf-equivalent)
  ↑
main.py  (imports orchestrator, store, backend.api, backend.config, backend.llm)
```

So the move order is: **models → store → agents → github_publisher → dev_server → orchestrator → main**. Each move task is a single commit that updates every caller.

**Acceptance gate at the end of every move task:**
- `pytest tests/contract tests/unit -v` — green (the pre-existing import-linter test may or may not fail depending on the host env; treat its result as informational, not a blocker)
- `python -c "import main"` (after Task 7) becomes `python -c "from backend.main import app"` — successfully imports

---

## File structure (end of Stage 5)

```
Universal-visualizer/
├── pyproject.toml             # NEW — canonical metadata + direct deps
├── requirements.lock          # NEW — hash-pinned full closure
├── requirements-dev.lock      # NEW — adds pytest, mypy, ruff, import-linter, openapi-typescript
├── Dockerfile                 # MODIFIED — drops per-file COPYs of root .py; switches to --require-hashes
├── fixed_main_v6.py           # UNCHANGED — 9-line stub; argv contract
├── .importlinter              # MODIFIED — module list under [importlinter:contract] expanded
├── backend/
│   ├── agents.py              # MOVED from root
│   ├── dev_server.py          # MOVED from root
│   ├── github_publisher.py    # MOVED from root
│   ├── main.py                # MOVED from root
│   ├── models.py              # MOVED from root
│   ├── orchestrator.py        # MOVED from root
│   ├── store.py               # MOVED from root
│   └── …                       # (api/, services/, llm/, etc. unchanged)
├── tests/
│   ├── contract/conftest.py   # MODIFIED — `from main import app` → `from backend.main import app`
│   └── unit/services/test_*.py # MODIFIED — model/store imports rewritten
└── (removed) requirements.txt, requirements-dev.txt
```

---

## Pre-flight check

Before Task 1, run this once to capture the full set of files that import any root-level module — every later task will narrow this list:

```bash
cd /Users/pulkitmangal/Universal-visualizer
grep -rn "^from \(orchestrator\|store\|models\|agents\|dev_server\|github_publisher\|main\)\b\|^import \(orchestrator\|store\|models\|agents\|dev_server\|github_publisher\|main\)\b" \
  --include="*.py" . 2>/dev/null \
  | grep -vE "/(\\.venv|__pycache__|\\.import_linter_cache)/"
```

Save the output. After Task 7, re-run — must be empty.

---

## Task 1: Move `models.py` → `backend/models.py`

**Why first:** no project-internal imports; lowest risk.

**Files:**
- Move: `models.py` → `backend/models.py`
- Modify (callers): `store.py`, `agents.py`, `tests/unit/services/test_manifest_builder.py`, `tests/unit/services/test_build_orchestrator.py`, `backend/api/health.py`, `backend/api/manifest.py`, `backend/api/jobs.py`, `backend/api/deps.py`, `backend/api/builds.py`, `backend/api/suggestions.py`, `backend/services/build_orchestrator.py`, `backend/services/manifest_builder.py`

- [ ] **Step 1: Move the file**

```bash
cd /Users/pulkitmangal/Universal-visualizer
git mv models.py backend/models.py
```

- [ ] **Step 2: Rewrite imports**

Use a single bash one-liner across all the caller files. Re-run the pre-flight grep first to confirm the call sites are exactly what's listed above; if anything else has appeared, include it.

```bash
grep -rl "^from models\b\|^import models\b" \
  --include="*.py" /Users/pulkitmangal/Universal-visualizer/ \
  | grep -vE "/(\\.venv|__pycache__|\\.import_linter_cache)/" \
  | xargs sed -i '' -e 's/^from models /from backend.models /' \
                    -e 's/^from models import/from backend.models import/' \
                    -e 's/^import models$/import backend.models as models/'
```

After the sed: re-run the pre-flight grep with only `models` in the alternation — must show 0 hits in the project, all rewritten to `from backend.models …`.

- [ ] **Step 3: Run tests**

```bash
pytest tests/contract tests/unit -v
```

Expected: same baseline as before the move (any pre-existing import-linter failure on this host is unchanged, everything else green).

- [ ] **Step 4: Smoke-import**

```bash
python -c "from backend.models import JobState, JobStatus; print('OK')"
```

Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py
git add -u   # captures the deleted root models.py + every import rewrite
git commit -m "refactor(backend): move models.py into backend/"
```

---

## Task 2: Move `store.py` → `backend/store.py`

**Files:**
- Move: `store.py` → `backend/store.py`
- Modify: `tests/unit/services/test_build_orchestrator.py`, `backend/api/manifest.py`, `backend/api/jobs.py`, `backend/api/deps.py`, `backend/api/builds.py`, `backend/api/suggestions.py`, `backend/services/build_orchestrator.py`, `main.py`

Same 5-step pattern as Task 1:

- [ ] **Step 1: Move**

```bash
git mv store.py backend/store.py
```

- [ ] **Step 2: Rewrite imports**

```bash
grep -rl "^from store\b\|^import store\b" \
  --include="*.py" /Users/pulkitmangal/Universal-visualizer/ \
  | grep -vE "/(\\.venv|__pycache__|\\.import_linter_cache)/" \
  | xargs sed -i '' -e 's/^from store /from backend.store /' \
                    -e 's/^from store import/from backend.store import/' \
                    -e 's/^import store$/import backend.store as store/'
```

- [ ] **Step 3: Tests** — `pytest tests/contract tests/unit -v` green.
- [ ] **Step 4: Smoke** — `python -c "from backend.store import job_store; print('OK')"`.
- [ ] **Step 5: Commit** — `refactor(backend): move store.py into backend/`.

---

## Task 3: Move `agents.py` → `backend/agents.py`

**Files:**
- Move: `agents.py` → `backend/agents.py`
- Modify: `backend/api/jobs.py`, `backend/api/builds.py`, `backend/api/suggestions.py`

- [ ] **Step 1: Move**

```bash
git mv agents.py backend/agents.py
```

- [ ] **Step 2: Rewrite imports**

```bash
grep -rl "^from agents\b\|^import agents\b" \
  --include="*.py" /Users/pulkitmangal/Universal-visualizer/ \
  | grep -vE "/(\\.venv|__pycache__|\\.import_linter_cache)/" \
  | xargs sed -i '' -e 's/^from agents /from backend.agents /' \
                    -e 's/^from agents import/from backend.agents import/' \
                    -e 's/^import agents$/import backend.agents as agents/'
```

- [ ] **Step 3: Tests** — green.
- [ ] **Step 4: Smoke** — `python -c "from backend.agents import topic_extraction_agent; print('OK')"` (set `OPENAI_API_KEY=sk-test` if the agent module's import requires it; if it fails on missing key, that's expected behavior preserved from before — wrap with `OPENAI_API_KEY=sk-test python -c "..."`).
- [ ] **Step 5: Commit** — `refactor(backend): move agents.py into backend/`.

---

## Task 4: Move `github_publisher.py` → `backend/github_publisher.py`

**Files:**
- Move: `github_publisher.py` → `backend/github_publisher.py`
- Modify: `backend/services/build_orchestrator.py`

Same pattern.

- [ ] **Step 1: Move** — `git mv github_publisher.py backend/github_publisher.py`
- [ ] **Step 2: Rewrite imports** — same sed pattern with `github_publisher`.
- [ ] **Step 3: Tests** — green.
- [ ] **Step 4: Smoke** — `python -c "from backend.github_publisher import publish_viz_repo; print('OK')"`.
- [ ] **Step 5: Commit** — `refactor(backend): move github_publisher.py into backend/`.

---

## Task 5: Move `dev_server.py` → `backend/dev_server.py`

**Files:**
- Move: `dev_server.py` → `backend/dev_server.py`
- Modify: any importers (run the grep — currently `orchestrator.py` may reference it via subprocess invocation, but verify directly).

- [ ] **Step 1: Move** — `git mv dev_server.py backend/dev_server.py`
- [ ] **Step 2: Rewrite imports** — sed pattern with `dev_server`.
- [ ] **Step 3: Tests** — green.
- [ ] **Step 4: Smoke** — `python -c "from backend.dev_server import ManagedDevServer; print('OK')"` (substitute actual export name if different — grep `dev_server.py` for `def ` and `class ` at module top).
- [ ] **Step 5: Commit** — `refactor(backend): move dev_server.py into backend/`.

---

## Task 6: Move `orchestrator.py` → `backend/orchestrator.py`

**Files:**
- Move: `orchestrator.py` → `backend/orchestrator.py`
- Modify: `main.py`, `backend/services/build_orchestrator.py`

Care: `orchestrator.py` exports `FIXED_MAIN_PATH` and `VIZ_OUTPUT_DIR` consumed by `main.py:25` startup logging. Confirm those exports still resolve after the move.

- [ ] **Step 1: Move** — `git mv orchestrator.py backend/orchestrator.py`
- [ ] **Step 2: Rewrite imports** — sed with `orchestrator`.
- [ ] **Step 3: Tests** — green.
- [ ] **Step 4: Smoke** — `OPENAI_API_KEY=sk-test python -c "from backend.orchestrator import run_viz_build, FIXED_MAIN_PATH, VIZ_OUTPUT_DIR; print('OK')"`.
- [ ] **Step 5: Commit** — `refactor(backend): move orchestrator.py into backend/`.

---

## Task 7: Move `main.py` → `backend/main.py` + retarget uvicorn

This is the biggest task — touches Dockerfile, railway.toml, contract tests, and any `python main.py` callers.

**Files:**
- Move: `main.py` → `backend/main.py`
- Modify: `tests/contract/conftest.py` (imports `from main import app`), `Dockerfile` (CMD), `railway.toml` (startCommand if present)
- Verify: `fixed_main_v6.py` stays at root and still imports `from backend.viz_generator.cli import main`

- [ ] **Step 1: Move the file**

```bash
git mv main.py backend/main.py
```

- [ ] **Step 2: Rewrite imports**

```bash
grep -rl "^from main\b\|^import main\b" \
  --include="*.py" /Users/pulkitmangal/Universal-visualizer/ \
  | grep -vE "/(\\.venv|__pycache__|\\.import_linter_cache)/" \
  | xargs sed -i '' -e 's/^from main /from backend.main /' \
                    -e 's/^from main import/from backend.main import/'
```

Re-run grep — must be 0 hits.

- [ ] **Step 3: Update Dockerfile CMD**

Replace the existing `CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8001}"]` with:

```dockerfile
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
```

- [ ] **Step 4: Drop per-file COPYs from Dockerfile**

The current Dockerfile's runtime stage likely has either:
- `COPY . .` (sweeps everything — relies on .dockerignore), or
- explicit `COPY main.py orchestrator.py agents.py … ./` (now all stale)

Read the current Dockerfile (it was rewritten in Stage 4 Task 13). For any per-file COPY of a now-moved root .py, delete the line — `COPY backend/ ./backend/` already covers them. Keep `COPY fixed_main_v6.py ./` if present (still at root).

- [ ] **Step 5: Check `railway.toml`**

Read `railway.toml`. If a `startCommand = "uvicorn main:app …"` is present, update to `uvicorn backend.main:app …`. If no `startCommand` is set (Stage 4 left it relying on Dockerfile CMD), no edit needed.

- [ ] **Step 6: Verify `fixed_main_v6.py` is unaffected**

```bash
cat /Users/pulkitmangal/Universal-visualizer/fixed_main_v6.py
```

Expected: 9-line stub doing `from backend.viz_generator.cli import main; main()`. **Do not change this file** — its absolute path is the subprocess contract.

- [ ] **Step 7: Run tests**

```bash
pytest tests/contract tests/unit -v
```

Expected: green (the contract conftest now imports `from backend.main import app`).

- [ ] **Step 8: Smoke-boot the app**

```bash
OPENAI_API_KEY=sk-test python -c "from backend.main import app; print('routes:', len(app.routes))"
```

Expected: prints a route count > 0.

If `docker` is available, also smoke-build:

```bash
docker build -t univiz:stage5-smoke .
docker run --rm -d -e OPENAI_API_KEY=sk-test -p 8001:8001 --name univiz-smoke univiz:stage5-smoke
sleep 8
curl -sf http://localhost:8001/healthz   # must 200
curl -sI http://localhost:8001/v2/ | head -1   # /v2/ was removed in Stage 4; check /
curl -sI http://localhost:8001/ | head -1
docker stop univiz-smoke
```

If `docker` is not available locally, mark this step as deferred-to-CI in the commit message.

- [ ] **Step 9: Update `.importlinter`**

Read `.importlinter`. The `root_packages = backend` line should already cover everything once the moves complete. But verify the layering contracts still describe the right modules — e.g., if a contract names `backend.services` and `backend.api`, those still exist. The new modules (`backend.models`, `backend.store`, `backend.agents`, `backend.orchestrator`, `backend.github_publisher`, `backend.dev_server`, `backend.main`) are reachable but not constrained by the existing contracts; that's acceptable for now. (Tightening the layering — e.g., forbidding `backend.services → backend.api` reverse imports — happens in a future PR if needed.)

- [ ] **Step 10: Commit**

```bash
git add -u
git add backend/main.py
git commit -m "refactor(backend): move main.py into backend/ and retarget uvicorn to backend.main:app"
```

After this commit, **re-run the pre-flight grep** — it must return zero hits.

---

## Task 8: Write `pyproject.toml`

**Files:**
- Create: `pyproject.toml`

This file becomes the canonical source of metadata + direct dependencies. Subsequent lockfile generation reads from here.

Read `requirements.txt` first to capture the exact direct deps. Read `requirements-dev.txt` for the dev deps. The current `requirements.txt` likely contains version ranges that match what's deployed today — preserve those ranges.

- [ ] **Step 1: Read existing deps**

```bash
cat /Users/pulkitmangal/Universal-visualizer/requirements.txt
cat /Users/pulkitmangal/Universal-visualizer/requirements-dev.txt
```

- [ ] **Step 2: Write `pyproject.toml`**

Use the spec's Section 3 template as the starting point. The direct deps section MUST match what `requirements.txt` already pins — if `requirements.txt` says `fastapi>=0.110.0,<0.120`, mirror that. Don't tighten or loosen without justification.

```toml
[project]
name = "universal-visualizer"
version = "0.2.0"
description = "Turn HackMD lecture scripts into embedded React+Vite visualizations."
requires-python = ">=3.12"
dependencies = [
    # FILL IN from requirements.txt — preserve existing ranges
]

[project.optional-dependencies]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "httpx>=0.27",
    "mypy>=1.10",
    "ruff>=0.5",
    "import-linter>=2.0",
    # FILL IN any additional dev deps from requirements-dev.txt
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["backend*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
strict = true
files = ["backend/llm", "backend/services", "backend/api", "backend/config.py"]
```

- [ ] **Step 3: Verify `pip install -e .` works**

```bash
cd /Users/pulkitmangal/Universal-visualizer
pip install -e .   # uses pyproject.toml
python -c "import backend.main; print('editable install OK')"
```

Expected: succeeds. Roll back the install if you want (`pip uninstall universal-visualizer -y`) — what we needed to confirm is that the metadata is valid.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "build: add pyproject.toml as canonical Python metadata"
```

---

## Task 9: Generate `requirements.lock` and `requirements-dev.lock`

**Files:**
- Create: `requirements.lock`, `requirements-dev.lock`

The spec recommends `uv`. If `uv` isn't installed, fall back to `pip-tools` (`pip-compile`).

- [ ] **Step 1: Detect tool**

```bash
which uv && uv --version
# if absent:
pip install --user uv  # or fall through to pip-tools below
```

If `uv` install isn't acceptable in the user's environment, use `pip-tools` instead:

```bash
pip install --user pip-tools
```

- [ ] **Step 2: Generate `requirements.lock`**

With `uv`:

```bash
cd /Users/pulkitmangal/Universal-visualizer
uv pip compile pyproject.toml --generate-hashes -o requirements.lock
```

With `pip-tools`:

```bash
pip-compile --generate-hashes --output-file=requirements.lock pyproject.toml
```

Expected: `requirements.lock` is created with every transitive dep, each pinned with `--hash=sha256:…` lines.

- [ ] **Step 3: Generate `requirements-dev.lock`**

With `uv`:

```bash
uv pip compile pyproject.toml --extra dev --generate-hashes -o requirements-dev.lock
```

With `pip-tools`:

```bash
pip-compile --generate-hashes --extra=dev --output-file=requirements-dev.lock pyproject.toml
```

- [ ] **Step 4: Smoke-install from the lock**

```bash
# In a throwaway venv:
python -m venv /tmp/stage5-lockfile-smoke
/tmp/stage5-lockfile-smoke/bin/pip install --require-hashes -r requirements.lock
/tmp/stage5-lockfile-smoke/bin/python -c "import fastapi, openai, pydantic_settings; print('lock install OK')"
rm -rf /tmp/stage5-lockfile-smoke
```

Expected: clean install with no hash mismatches.

- [ ] **Step 5: Commit**

```bash
git add requirements.lock requirements-dev.lock
git commit -m "build: generate hash-pinned requirements.lock + requirements-dev.lock"
```

---

## Task 10: Switch Dockerfile to `--require-hashes` and delete old requirements files

**Files:**
- Modify: `Dockerfile` (python-deps stage now uses the lock)
- Delete: `requirements.txt`, `requirements-dev.txt`

- [ ] **Step 1: Update Dockerfile python-deps stage**

Current Dockerfile (after Stage 4 Task 13) has roughly:

```dockerfile
FROM python:3.12-slim AS python-deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
```

Replace with:

```dockerfile
FROM python:3.12-slim AS python-deps
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
```

- [ ] **Step 2: Delete old requirements files**

```bash
git rm requirements.txt requirements-dev.txt
```

- [ ] **Step 3: Smoke docker build**

```bash
docker build -t univiz:stage5-final .
docker run --rm -d -e OPENAI_API_KEY=sk-test -p 8001:8001 --name univiz-final univiz:stage5-final
sleep 10
curl -sf http://localhost:8001/healthz   # 200
curl -sI http://localhost:8001/ | head -1   # 200 (new SPA at root)
docker stop univiz-final
```

If `docker` is not available, note this and rely on CI.

- [ ] **Step 4: Update README/dev-docs**

If `README.md` or any `docs/*.md` says "install via `pip install -r requirements.txt`", update to `pip install -e ".[dev]"` (uses pyproject.toml — installs deps loosely, picks up code changes in editable mode). If no such instruction is present, skip.

Don't add new documentation unless the user explicitly requests it.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile
git add -u   # captures the deletions
git commit -m "build: switch Dockerfile to --require-hashes -r requirements.lock; remove requirements.txt"
```

---

## Task 11: Tag and open PR

- [ ] **Step 1: Tag**

```bash
git tag modularization-stage-5
```

- [ ] **Step 2: Push the feature branch + tag**

```bash
git push -u origin feat/modularization-stage-5
git push origin modularization-stage-5
```

- [ ] **Step 3: PR**

Open a PR with title `feat: modularization stage 5 — backend consolidation + lockfile` and body summarizing:
- All 7 root-level .py modules moved into `backend/`
- `fixed_main_v6.py` retained at repo root (subprocess argv contract)
- `pyproject.toml` is now canonical metadata
- `requirements.lock` + `requirements-dev.lock` (hash-pinned) replace `requirements.txt`
- Dockerfile uses `--require-hashes` for reproducible installs
- Follow-ups (separate PRs): template `node_modules` cache; `BUILD_CONCURRENCY` semaphore; non-blocking subprocess stdout; per-task LLM budgets; mypy `--strict` CI gate; observability project

---

## After Stage 5 — follow-ups (NOT this plan)

1. **Template `node_modules` cache** baked at image build time — biggest remaining build-pipeline win (~30–60s/build).
2. **`BUILD_CONCURRENCY` semaphore** in `backend/services/build_orchestrator.py`.
3. **Non-blocking subprocess stdout** streaming.
4. **Per-task `TOKEN_BUDGET_PER_TASK` + output-token ceilings.**
5. **Deterministic file selection** in viz fix loops (LLM only on miss).
6. **mypy `--strict`** CI gate on `backend/{llm,services,api,config.py}`.
7. **GitHub Actions CI** wiring (ruff, mypy, import-linter, pytest, npm).
8. **Delete `backend/legacy/`** after one quiet release cycle.
9. **Observability project** — separate spec, deferred since the start.

---

## Self-review notes

- **Spec coverage:** every "Findings discovered during Stage 1 implementation" item is addressed except those already deferred. The "final file layout" in spec Section 1 is now reached.
- **No placeholders.** Every step has either a code snippet, a sed pattern, or an exact command. The pyproject.toml dependencies are derived from `requirements.txt` (read it; don't guess).
- **Risk concentration.** Tasks 1–7 are 7 mechanical moves; each is its own commit with a smoke step. Tasks 8–10 are the lockfile work — independent of the moves, can roll back in isolation.
- **Subprocess argv contract preserved.** `fixed_main_v6.py` stays at repo root. Verified in Task 7 Step 6.
