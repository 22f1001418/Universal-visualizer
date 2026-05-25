# Universal-visualizer — Modularization & Optimization Design

**Date:** 2026-05-25
**Status:** Draft (awaiting user review)
**Owner:** pulkit.mangal@scaler.com
**Successor project:** Observability layer (parked — resumes after this lands)

---

## Summary

Universal-visualizer is a FastAPI + React app that turns a HackMD `.md` lecture script
into a set of embedded React+Vite visualizations via two LLM agents and a viz-generator
subprocess. Three files hold most of the complexity:

| File | Lines | State |
|---|---|---|
| `fixed_main_v6.py` | 2,579 | Monolith. Duplicates the LLM client. 5 codegen phases mixed with parsing, npm, and Playwright. |
| `main.py` | 858 | Monolith. App bootstrap + 10 HTTP routes + build orchestration + viz post-processing in one file. |
| `index.html` | 881 | Monolith. React via CDN + Babel-in-browser + 9 components + ~300 lines of CSS. |

This project modularizes those three monoliths and lands four production-grade
optimizations along the way (code maintainability, LLM cost efficiency, build pipeline
speed, deploy footprint). It is delivered as a staged migration (four PRs) under one
spec. Backend modularization of `fixed_main_v6.py` deduplicates the LLM core; backend
modularization of `main.py` splits it into routers and services; the frontend is
replaced with a Vite + React + TypeScript SPA. Each stage ships behind stable contracts
so production never breaks.

Observability — the originally-requested second feature — is intentionally **deferred**.
It is the next project, and it will be far cleaner to instrument the modular codebase
than the current one.

## Goals & non-goals

**Goals**
1. Reduce the three monoliths to single-responsibility modules with explicit interfaces.
2. Eliminate the duplicated LLM client between `fixed_main_v6.py` and `llm_client.py`.
3. Cut LLM cost via per-task model selection and prompt-cache-friendly call shape.
4. Cut viz-build wall-clock via a warmed npm template cache.
5. Cut runtime image size and cold-start time via a multi-stage Dockerfile and a real
   frontend build.
6. Establish a test foundation (contract + unit + integration) before the bigger
   surface area lands.
7. Preserve every existing HTTP endpoint shape and the subprocess CLI contract during
   the migration.

**Non-goals**
- Adding the observability layer (separate spec follows).
- Changing the LLM agent prompts or the viz-generation algorithm itself.
- Multi-process / horizontal-scale deployment (still single uvicorn worker).
- Authentication / multi-tenancy.
- Database persistence of jobs (in-memory `JobStore` stays; the observability project
  introduces SQLite).

## Constraints & stable contracts

These must remain stable through every stage of the migration:

| Contract | Why it must stay stable |
|---|---|
| HTTP API surface (paths, methods, request/response shapes) | The browser SPA depends on it during Stages 1–3; the new SPA in Stage 4 depends on the same shapes. |
| Subprocess CLI argv for the viz generator | The orchestrator spawns `fixed_main_v6.py` with specific flags. |
| Env var names | Existing Railway deployments depend on them. Renames only at controlled cutovers. |
| `VIZ_OUTPUT_DIR` layout | On-disk viz projects must remain valid across deploys. |
| `github_publisher.py` semantics | Currently working end-to-end. Only its callers move. |

## Approach: strangler-fig, staged

Four stages, one per PR (some may split into 3a/3b). Each stage is independently
shippable, independently revert-able, and leaves production working. Big-bang rewrite
and cosmetic-only file-splitting were explicitly rejected.

```
Stage 1 — Shared LLM core
  Pull the LLM client duplicated inside fixed_main_v6.py into a shared package.
  Outcome: one client, one TokenUsageTracker, one pricing table, one retry policy.

Stage 2 — Decompose fixed_main_v6.py into a viz_generator/ package
  One module per phase. Thin main() CLI wrapper preserved so the subprocess
  contract from main.py is unchanged.

Stage 3 — Decompose main.py into app + api/ + services/
  Routers grouped by resource. Build orchestration extracted to a service.
  Viz post-processing (vite-config patch, error-boundary inject) moves into
  viz_generator/ where it belongs.

Stage 4 — Replace index.html with a Vite + React + TypeScript SPA
  Full feature parity with the current UI. Existing CSS tokens carried over.
  Real bundling, types, code-split routes, no Babel-in-browser.
```

---

## Section 1 — Target architecture

### Final file layout (end of Stage 4)

```
Universal-visualizer/
├── pyproject.toml             # NEW — canonical metadata + direct deps
├── requirements.lock          # generated, hash-pinned
├── requirements-dev.lock      # adds pytest, mypy, ruff, import-linter
├── Dockerfile                 # multi-stage: node-build → python-deps → runtime
├── railway.toml
├── env.example                # regenerated to match backend/config.py
├── docs/superpowers/specs/    # this spec
├── fixed_main_v6.py           # 2-line compatibility stub (see Section 4)
├── backend/                   # NEW — all Python code under one root
│   ├── __init__.py
│   ├── main.py                # app factory + startup wiring (target ≤ 100 lines)
│   ├── config.py              # pydantic-settings singleton
│   ├── logging_setup.py       # structured logger + correlation IDs
│   ├── llm/                   # Stage 1
│   │   ├── __init__.py
│   │   ├── client.py          # get_client(), llm_call(), retries
│   │   ├── pricing.py         # PRICE_PER_1K + cost computation
│   │   ├── tracker.py         # TokenUsageTracker
│   │   ├── errors.py          # error classification (permanent/retryable)
│   │   └── reasoning.py       # is_reasoning_model, reasoning kwargs
│   ├── viz_generator/         # Stage 2
│   │   ├── __init__.py
│   │   ├── cli.py             # main() entry — preserves the CLI contract
│   │   ├── topic.py           # classify_topic
│   │   ├── parsing.py         # parse_files, marker/codeblock parsers
│   │   ├── files.py           # write_to_disk, _validate_filepath, dep pinning
│   │   ├── select.py          # select_relevant_files, deterministic heuristic
│   │   ├── phases/            # one module per pipeline phase
│   │   │   ├── draft.py             # generate_draft_code
│   │   │   ├── build_loop.py        # npm install/build + error loop
│   │   │   ├── runtime_loop.py      # playwright + semantic checks
│   │   │   └── polish.py            # design polish pass
│   │   ├── postprocess.py     # vite-config patch + error-boundary inject
│   │   ├── screenshot.py      # playwright screenshot capture
│   │   └── npm.py             # _run_npm_install, _run_npm_build, port pick
│   ├── api/                   # Stage 3 — was the routes in main.py
│   │   ├── __init__.py
│   │   ├── health.py
│   │   ├── jobs.py
│   │   ├── suggestions.py
│   │   ├── builds.py
│   │   ├── manifest.py
│   │   ├── preview.py
│   │   └── dev_servers.py
│   ├── services/              # Stage 3 — business logic, no HTTP
│   │   ├── __init__.py
│   │   ├── build_orchestrator.py  # was _run_build_task in main.py
│   │   ├── manifest_builder.py    # was _build_manifest in main.py
│   │   ├── job_lifecycle.py       # status transitions, log appending
│   │   └── suggestion_cache.py    # was inline in main.py (see Section 2)
│   ├── orchestrator.py        # existing — slight refactor for new layout
│   ├── agents.py              # existing — unchanged
│   ├── github_publisher.py    # existing — unchanged
│   ├── store.py               # existing — unchanged
│   ├── models.py              # existing — unchanged
│   ├── dev_server.py          # existing — moved under backend/, unchanged
│   └── static/                # built frontend assets land here
│       └── index.html
├── frontend/                  # Stage 4
│   ├── package.json
│   ├── vite.config.ts         # outputs to ../backend/static/
│   ├── tsconfig.json
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   └── types.gen.ts   # generated from FastAPI /openapi.json
│   │   ├── pages/
│   │   │   ├── Upload.tsx
│   │   │   ├── Topics.tsx
│   │   │   ├── Suggestions.tsx
│   │   │   ├── Building.tsx
│   │   │   └── Done.tsx
│   │   ├── components/
│   │   │   ├── Crumbs.tsx
│   │   │   ├── GithubRepoStatus.tsx
│   │   │   ├── BuildCard.tsx
│   │   │   └── ...
│   │   ├── lib/
│   │   │   ├── formatters.ts
│   │   │   └── theme.css      # imported tokens (--ink-0, --accent, ...)
│   │   └── hooks/
│   │       └── useJobPolling.ts
│   └── tests/
└── tests/                     # backend tests
    ├── unit/
    ├── contract/
    ├── integration/
    └── e2e/
```

### Two non-obvious architectural choices

1. **The CLI contract of `fixed_main_v6.py` is preserved.** Stage 2 introduces
   `backend/viz_generator/cli.py` whose `main()` accepts the same flags. The
   orchestrator continues to spawn it as a subprocess. The viz generator is **not**
   in-process-imported — that would break the subprocess isolation that protects the
   FastAPI worker from playwright/npm crashes. A backward-compatible
   `fixed_main_v6.py` stub at repo root delegates to `backend.viz_generator.cli`.

2. **All Python code moves into `backend/`.** Needed so the frontend can live as a
   peer (`frontend/`), so Docker can copy them in separate stages, and so
   `pip install -e .` is clean.

---

## Section 2 — Optimizations, mapped to stages

The four selected optimizations: code maintainability, production deploy footprint,
LLM cost / token efficiency, build pipeline speed. Each must land in a specific module.

### Code maintainability (all stages)

- Single-responsibility per file. Each module answers "what does it do, how do I use
  it, what does it depend on" in under a minute.
- Public interface = `__init__.py` only. Cross-package imports go through it.
  Enforced by `import-linter` in CI:
  ```
  api/ → services/ → {llm, viz_generator, store, models}
  No reverse imports. services/ never imports api/. llm/ depends on nothing.
  ```
- Full type annotations on new/touched Python; `mypy --strict` on
  `backend/{llm,services,api,config.py}`.
- Configuration consolidated in `backend/config.py` (`pydantic-settings`).
  No `os.getenv(...)` scattered across modules.
- Structured logging with correlation IDs (`pipeline_id` / `job_id`) via
  `contextvars`. Replaces ad-hoc `_StructuredFormatter` in `main.py:71`.
- Each new module has a colocated unit test file.

### LLM cost / token efficiency (Stage 1 + Stage 2)

- **Deduplicate retry/budget layer.** `fixed_main_v6.py` currently has its own retry
  loop, `TokenUsageTracker`, and pricing table. Stage 1 deletes them and routes
  through `backend/llm/`. Eliminates preventable cost from inconsistent backoff.
- **Per-task model selection.** Introduce an `LLMTask` enum with per-task model
  defaults overridable by env. Today every call uses the global
  `OPENAI_TEXT_MODEL`; this lets the expensive `viz_draft` step keep a heavy
  model while every other step drops to mini.

  | `LLMTask` | Env var | Default model |
  |---|---|---|
  | `AGENT_A_EXTRACT` | `MODEL_AGENT_A` | `gpt-4o-mini` |
  | `AGENT_B_SUGGEST` | `MODEL_AGENT_B` | `gpt-4o-mini` |
  | `VIZ_TOPIC_CLASSIFY` | `MODEL_VIZ_CLASSIFY` | `gpt-4o-mini` |
  | `VIZ_DRAFT` | `MODEL_VIZ_DRAFT` | `gpt-4o` |
  | `VIZ_BUILD_FIX` | `MODEL_VIZ_FIX` | `gpt-4o-mini` |
  | `VIZ_RUNTIME_FIX` | `MODEL_VIZ_RUNTIME` | `gpt-4o-mini` |
  | `VIZ_POLISH` | `MODEL_VIZ_POLISH` | `gpt-4o-mini` |

  Defaults are reviewed in the Stage 1 PR before merge.
- **Prompt-cache-friendly call shape.** Document and enforce: long static rules in
  `system_prompt` (cacheable); per-call variable content in `user_prompt`. Add
  OpenAI prompt-caching headers where applicable.
- **Output-token ceilings per task.** Per-task `max_tokens` defaults; fail-fast on
  truncation rather than silent retries.
- **Cache agent_B suggestions per topic.** Lift the in-memory cache from `main.py`
  into `services/suggestion_cache.py`, keyed on `(topic_id, custom_notes_hash)`.
- **Smarter file selection in viz fix loops.** `select_relevant_files` currently
  asks the LLM which files are relevant. Replace with deterministic heuristic for
  small projects (the error-mentioned file + its imports); fall back to LLM only
  when heuristic returns nothing.
- **Hard per-task budgets.** Add `TOKEN_BUDGET_PER_TASK` (e.g., viz_draft=80k,
  runtime_fix=40k) alongside the existing per-job budget.

### Build pipeline speed (Stage 2 + Stage 3)

- **Warmed npm template cache.** Pre-build a template `node_modules` at image
  build time (`scripts/warm_template.sh`). Each viz build does `cp -a` from the
  cache instead of running `npm install` from scratch. Cuts ~30–60s per build.
- **Run npm install in parallel with code generation** where possible — the template
  cache makes most installs trivial; this further parallelizes the few that aren't.
- **Skip `npm audit fix --force` from the auto-flow.** Slow and rarely needed with
  pinned deps. Opt-in only.
- **Reuse dev servers when content hasn't changed.** `dev_server.py` already manages
  ports. Add a content fingerprint (SHA-256 over the sorted list of
  `(relative_path, file_sha256)` pairs under `project_dir`, ignoring `node_modules`
  and `dist`). If the fingerprint matches a still-healthy dev server, return its
  existing URL instead of spawning a new one. Fingerprint is recomputed only when
  the build pipeline finishes; runtime file edits don't trigger a respawn.
- **Bound concurrent builds.** `BUILD_CONCURRENCY` semaphore (default 2) in the
  orchestrator service. Today nothing prevents N simultaneous builds from saturating
  the box.
- **Stream stdout, don't buffer.** Switch to non-blocking line reads from subprocess
  stdout to reduce tail latency on "what's happening now" polling.

### Production deploy footprint (Stage 4 + deploy plumbing)

- **Multi-stage Dockerfile** (see Section 3): frontend toolchain (node-alpine, npm
  registry, dev deps) never reaches the runtime image.
- **Slim base image.** `python:3.12-slim` already in use; keep.
- **Tight `.dockerignore`.** Currently missing entirely. Excludes `node_modules`,
  `__pycache__`, `.venv`, `*.pyc`, `viz_outputs/`, `data/`, `tests/`, `docs/`, etc.
- **Frozen, hashed deps.** `pyproject.toml` + `uv` (or `pip-tools`) produces
  `requirements.lock` with hashes. Reproducible installs.
- **Frontend bundle hygiene.** Vite production build: gzip+brotli, hashed asset
  filenames, code-split routes, drop console.log, no source maps in prod artifacts.
- **Cold-start friendly.** Keep expensive work out of import time; `get_client()`
  stays lazy.
- **Healthcheck upgrades.** `/healthz` to additionally surface "fixed_main_v6
  callable", "template cache populated", "current build queue depth".

### Optimization summary

| Optimization | Lands in stage | Effort | Expected impact |
|---|---|---|---|
| Maintainability (single-responsibility, types, import contracts) | All stages | High (the work itself) | Foundation for everything that follows |
| Per-step model selection | Stage 1 | Low | Largest LLM cost saver — most calls drop to mini |
| Template cache for npm-install | Stage 2 | Medium | ~30–60s saved per build |
| Multi-stage Docker + slim base | Stage 4 deploy | Low | Image ~60% smaller; faster cold start |
| Bundle code-split + drop Babel-in-browser | Stage 4 | Comes with migration | Faster page load; no in-browser compile |

---

## Section 3 — Build & deploy plumbing

The runtime image must keep Node + Chromium because `fixed_main_v6.py` is spawned as
a subprocess and that subprocess runs `npm install` / `npm run dev` and a Playwright
screenshot. The wins are everywhere else.

### Current state, audited

| Concern | Today | Problem |
|---|---|---|
| Image base | `python:3.12-slim` | Already good |
| Node + npm | Installed via NodeSource into the runtime | Has to stay (subprocess needs it) |
| Playwright + Chromium | `playwright install chromium --with-deps` | Has to stay (~250–400 MB) |
| Source copy | `COPY . .` | Sweeps in `viz_outputs/`, `.git`, tests, `__pycache__`, generated viz subprojects |
| `.dockerignore` | Missing entirely | Compounds the COPY problem |
| Python deps | `requirements.txt` ranges (`fastapi>=0.110.0`) | Not reproducible; rebuilds drift |
| Frontend build | None (CDN-React + Babel-in-browser) | Stage 4 introduces a real build |
| Healthcheck | `/healthz`, 30s timeout | Adequate; will expand |
| Restart policy | `ON_FAILURE`, max 3 retries | Fine |

### Target Dockerfile (multi-stage, Stage 4 deploy)

```dockerfile
# ── Stage 1: frontend build ──────────────────────────────────────────────
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # → /app/frontend/dist

# ── Stage 2: python deps (separate so deps cache survives code changes) ──
FROM python:3.12-slim AS python-deps
WORKDIR /app
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

# ── Stage 3: runtime ─────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl gnupg ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app

COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

RUN playwright install chromium --with-deps \
    && rm -rf /var/lib/apt/lists/*

COPY backend/ ./backend/
COPY pyproject.toml ./
COPY --from=frontend-builder /app/frontend/dist ./backend/static/

COPY scripts/warm_template.sh ./scripts/
RUN bash scripts/warm_template.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIZ_OUTPUT_DIR=/app/viz_outputs \
    FIXED_MAIN_PATH=/app/backend/viz_generator/cli.py \
    UV_TEMPLATE_CACHE=/app/.cache/uv-template

RUN mkdir -p /app/viz_outputs

RUN useradd --create-home --uid 1001 app && chown -R app:app /app
USER app

EXPOSE 8001
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8001}"]
```

### `.dockerignore` (new, mandatory)

```
.git
.gitignore
.venv
__pycache__
**/__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.mypy_cache
.ruff_cache
node_modules
frontend/node_modules
frontend/dist
viz_outputs
data
docs
tests
*.md
!README.md
.env
.env.*
.DS_Store
*.log
.idea
.vscode
.claude
.github
```

### Python dependency management

Replace `requirements.txt` (range-pinned) with:

```
pyproject.toml          # canonical metadata + direct deps
requirements.lock       # hash-pinned full transitive closure
requirements-dev.lock   # adds pytest, mypy, ruff, import-linter
```

Tool: `uv` (preferred) or `pip-tools`. Lockfile regeneration is a deliberate
action committed in its own PR, not a side effect of `pip install`.

```toml
# pyproject.toml (relevant sections)
[project]
name = "universal-visualizer"
version = "0.2.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.110.0,<0.120",
  "uvicorn[standard]>=0.27.0,<0.40",
  "python-multipart>=0.0.9,<0.1",
  "python-dotenv>=1.0.0,<2",
  "openai>=1.66.0,<2",
  "pydantic>=2.0.0,<3",
  "pydantic-settings>=2.2.0,<3",
  "playwright>=1.40.0,<2",
  "requests>=2.31.0,<3",
]

[project.optional-dependencies]
dev = [
  "pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27",
  "mypy>=1.10", "ruff>=0.5", "import-linter>=2.0",
  "openapi-typescript>=7",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.mypy]
strict = true
files = ["backend/llm", "backend/services", "backend/api", "backend/config.py"]
```

### `railway.toml` updates

```toml
[build]
builder = "DOCKERFILE"
dockerfilePath = "Dockerfile"

[deploy]
restartPolicyType = "ON_FAILURE"
restartPolicyMaxRetries = 3
startCommand = "uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1"
healthcheckPath = "/healthz"
healthcheckTimeout = 60

[[deploy.environmentVariables]]
name = "VIZ_OUTPUT_DIR"
value = "/app/viz_outputs"
```

`--workers 1` is deliberate: the in-memory `JobStore` is per-process. Multi-worker
silently corrupts state. Documented in `config.py`. The observability project
introduces persistence; only after that should `--workers` go up.

### Frontend bundle hygiene (Stage 4)

```ts
// vite.config.ts (essentials)
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/static',
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
        },
      },
    },
  },
  esbuild: {
    drop: ['console', 'debugger'],
  },
});
```

### Config consolidation

`backend/config.py` becomes the single source of truth (`pydantic-settings`).
Every module imports `settings` from here; `os.getenv(...)` removed across the
codebase. `env.example` regenerated to match.

### Expected outcomes

| Metric | Today | After Stage 4 |
|---|---|---|
| Runtime image size | ~1.5–2 GB (Chromium + dev cruft + viz_outputs accidentally COPY'd) | ~700–900 MB |
| Cold start | ~6–10s | ~3–5s |
| Build context upload (incremental rebuild) | hundreds of MB | <10 MB |
| First viz build wall-clock | npm install ~30–60s + codegen | npm install ~2s (cache hit) + codegen |
| Frontend bundle | None — Babel compiles in browser on every page load | ~80–120 KB gz; first load cached via hashed filenames |

---

## Section 4 — Backward-compat & cutover strategy

### Per-stage cutover detail

Each stage is one PR (some may split). Each PR's final commit leaves the app
working in production.

#### Stage 1 — Shared LLM core (PR #1)

**Risk:** Low. Touches the LLM path, well-exercised by every build.

**Order within the PR:**
1. Create `backend/llm/` (new package, parallel to existing code).
2. Move contents of `llm_client.py` into `backend/llm/{client.py, pricing.py,
   tracker.py, errors.py, reasoning.py}`. Keep `llm_client.py` as a shim:
   ```python
   # llm_client.py (compatibility shim — deleted in Stage 3)
   from backend.llm.client import llm_call, get_client      # noqa: F401
   from backend.llm.tracker import token_tracker, TokenUsageTracker  # noqa: F401
   from backend.llm.reasoning import is_reasoning_model     # noqa: F401
   from backend.llm.pricing import PRICE_PER_1K             # noqa: F401
   ```
3. Replace the duplicated LLM block in `fixed_main_v6.py` (lines ~870–1090,
   ~1438+) with imports from `backend.llm`. Subprocess argv unchanged.
4. Introduce `LLMTask` enum + per-task model resolution. Default everything to
   today's behaviour (no behaviour change in this PR).
5. Run the full manual flow end-to-end. Manifest cost figure must be ≤ today's
   for the same input.

**Cutover marker:** PR merges, deploy. Shim keeps every `from llm_client import …`
working.

**Rollback:** revert the PR.

#### Stage 2 — `viz_generator/` package (PR #2)

**Risk:** Medium. Heaviest file; bugs here break the build pipeline.

**Order within the PR:**
1. Create `backend/viz_generator/` and move each concern into its module per
   Section 1.
2. Create `backend/viz_generator/cli.py` whose `main()` accepts the **exact same
   argv** as today's `fixed_main_v6.py`. The argparse spec is captured verbatim
   from current behaviour as the Stage-2 freeze.
3. Replace `fixed_main_v6.py` at the repo root with a 2-line stub:
   ```python
   from backend.viz_generator.cli import main
   if __name__ == "__main__":
       main()
   ```
4. Verify `FIXED_MAIN_PATH=/app/fixed_main_v6.py` still resolves and the
   subprocess works exactly as before.
5. Layer in optimizations from Section 2 (template cache, deterministic file
   selection, per-task budgets) **behind feature flags in `config.py`**
   (default OFF). Land the PR with flags off → identical behaviour. Flip flags
   one at a time in subsequent small PRs.

**Cutover marker:** PR merges with flags off → behaviour identical.

**Rollback:** revert the PR.

#### Stage 3 — `api/` + `services/` split (PR #3, possibly 3a/3b)

**Risk:** Medium. Touches every HTTP route.

**Order within the PR:**
1. Create `backend/api/` (empty routers) and `backend/services/` (empty modules).
2. Move route handlers one resource at a time: `health → jobs → suggestions →
   builds → manifest → preview → dev_servers`. Each is one commit.
3. Extract `_run_build_task → services/build_orchestrator.py`. Extract
   `_build_manifest → services/manifest_builder.py`. Extract
   `_patch_vite_config_base` and `_inject_error_boundary →
   backend/viz_generator/postprocess.py`.
4. Reduce `backend/main.py` to: app factory, startup hooks, router mounts,
   middleware. Target ≤ 100 lines.
5. Replace shim users (`from llm_client import …`) with direct imports from
   `backend.llm`. **Delete `llm_client.py` shim** at the end of this PR.
6. Replace scattered `os.getenv(...)` with `settings` from `backend/config.py`.
   Same env var names → no Railway config change required.

**Cutover marker:** All HTTP contract tests pass with byte-equivalent responses.

**Rollback:** revert PR. Shim removal in step 5 is replayed in the revert.

#### Stage 4 — Vite + React + TS SPA (PR #4)

**Risk:** Highest. Full UI replacement.

**Cutover strategy:** side-by-side ship, then flip.

1. Build the new SPA in `frontend/`. `npm run build` outputs to `backend/static/`.
2. While developing, the old `index.html` stays the served root. The new SPA is
   mounted at `/v2/` (`StaticFiles(directory="backend/static_v2", html=True)`)
   for parity testing.
3. Feature-parity acceptance checklist (every existing flow tested side-by-side):
   - Upload `.md`, see topics.
   - Click topic, see 5 suggestions.
   - Pick a suggestion + custom notes, build queues.
   - Poll progress, see live log tail.
   - "Open Live Preview" works.
   - GitHub publish status renders correctly.
   - Manifest download works.
4. Flip the root: the route serving `/` returns the new SPA; old `index.html`
   moves to `/legacy/` for one release cycle.
5. After one quiet week with no rollbacks, delete the legacy HTML and the
   `/legacy/` mount.

**Cutover marker:** the flip in step 4. One config change, reversible.

**Rollback:** flip the feature flag back. Legacy HTML stays in the repo for one
release.

### Compatibility shims summary

| Shim | Introduced in | Removed in | Purpose |
|---|---|---|---|
| `llm_client.py` shim re-exporting from `backend.llm` | Stage 1 | Stage 3 | Lets `fixed_main_v6.py` and `main.py` keep working before they're touched |
| `fixed_main_v6.py` stub at repo root | Stage 2 | Never (kept for `FIXED_MAIN_PATH` back-compat) | Subprocess contract |
| `/v2/` parallel SPA mount | Stage 4 | After step 4 flip | Side-by-side testing |
| `/legacy/` old HTML mount | Stage 4 flip | One release after flip | Emergency rollback |

### Continuous compat verification

Add a `make verify-compat` (and CI job) runnable against any branch:

```
1. Boot the new build in a throwaway container.
2. Run the API contract test suite (Section 5) against every documented endpoint.
3. Run one E2E smoke: upload a 300-char .md, ensure /upload + /jobs/{id} respond
   with expected shapes (no full build).
4. For Stage 4: also run the frontend Playwright happy-path.
```

PRs that fail `make verify-compat` cannot merge.

### What deployers need to know

- **Stages 1–3:** no env changes, no manual steps. Railway autodeploys on merge.
- **Stage 4:** multi-stage Dockerfile + new layout requires a clean rebuild. No
  data migration. First deploy ~4–6 minutes; subsequent deploys cached.
- **Lockfile regen:** any time `pyproject.toml` direct deps change, regenerate
  `requirements.lock` in its own PR.

---

## Section 5 — Testing strategy

### Five layers

```
Layer                  Speed     When run         What it protects
─────────────────────────────────────────────────────────────────────
1. Unit                ms        Every commit     New module logic
2. API contract        ~1s/test  Every commit     HTTP surface
3. Subprocess contract ~1s/test  Every commit     CLI argv interface
4. Integration         ~5s/test  PR pre-merge     Module wiring
5. End-to-end (E2E)    ~2–5 min  Manual/nightly   Real OpenAI + npm
```

### Layer 1 — Unit tests

Mirror-path layout:

```
tests/unit/llm/test_pricing.py        ← backend/llm/pricing.py
tests/unit/llm/test_errors.py         ← backend/llm/errors.py
tests/unit/llm/test_tracker.py        ← backend/llm/tracker.py
tests/unit/viz_generator/test_parsing.py
tests/unit/viz_generator/test_files.py
tests/unit/viz_generator/test_select.py
tests/unit/viz_generator/test_postprocess.py
tests/unit/services/test_manifest_builder.py
tests/unit/services/test_build_orchestrator.py
tests/unit/config/test_settings.py
```

High-leverage targets:
- `pricing.py`: cost across all models incl. unknown-model fallback.
- `errors.py`: permanent vs retryable across status codes and error bodies.
- `tracker.py`: per-job buckets, budget overrun raises, reasoning-token accounting.
- `parsing.py`: marker + codeblock parsers against fixture LLM outputs.
- `files.py`: `_validate_filepath` rejects path traversal; dep pinning correct.
- `select.py`: heuristic returns expected files; LLM only invoked on miss.
- `postprocess.py`: `_patch_vite_config_base` + `_inject_error_boundary` for
  both `.ts` and `.js` configs (real bug history at commits `0099c84`, `68ddd9c`).
- `manifest_builder.py`: from a fixture `JobState`, manifest equals expected.
- `settings.py`: env loading, coercion, defaults, required-field validation.

The `fake_llm` fixture (in `tests/conftest.py`) provides pre-recorded responses
keyed by `step_label`, lets tests drive any LLM-dependent code path
deterministically, and records into the real `token_tracker` so budget tests
still work.

### Layer 2 — API contract tests

```
tests/contract/
  test_health.py
  test_upload.py
  test_jobs.py
  test_suggestions.py
  test_build.py
  test_manifest.py
  test_preview.py
  test_dev_servers.py
```

Two purposes:
1. **Migration safety net** — captured BEFORE Stage 3 starts, against the
   current `main.py`. The first commit of the modularization project lands the
   full contract suite passing against today's code.
2. **Future regression guard** — any change to an existing response shape must
   update the contract test in the same PR.

Pattern:

```python
def test_get_job_returns_expected_shape(client, seeded_job):
    r = client.get(f"/jobs/{seeded_job.job_id}")
    assert r.status_code == 200
    body = r.json()
    assert {"job_id", "script_name", "status", "topics",
            "suggestions", "builds", "manifest", "logs",
            "token_usage", "created_at"} <= set(body.keys())
    assert body["status"] in {"uploaded", "topics_extracted",
                              "awaiting_user_picks", "building",
                              "done", "failed"}
```

### Layer 3 — Subprocess contract tests

```python
# tests/contract/test_viz_cli.py
def test_cli_accepts_today_flags():
    from backend.viz_generator.cli import build_parser
    parser = build_parser()
    args = parser.parse_args([
        "--topic", "binary search",
        "--brief", "Show how binary search bisects a sorted array.",
        "--output-dir", "/tmp/test",
        "--job-id", "j_abc123",
        # …and every other flag the orchestrator passes today
    ])
    assert args.topic == "binary search"
    # …assert every flag
```

The list of flags is captured from `orchestrator.py`'s current call site at
the start of Stage 2.

### Layer 4 — Integration tests

External systems faked, multiple modules wired together:
- Upload `.md` → Agent A (faked) → topics extracted → `JobStore` updated.
- Pick suggestion → Agent B (faked) → cached; second call doesn't hit LLM.
- Build queued → `_run_build_task` thread → `subprocess.run` mocked → phases
  transition → manifest entry created.
- GitHub publish path with `requests` mocked via `responses` library.

### Layer 5 — End-to-end tests

```
tests/e2e/
  test_full_flow.py          # upload → topics → suggestions → build → manifest
  test_github_publish.py     # requires GITHUB_TOKEN; sacrificial repo
```

Constraints:
- Gated behind `pytest --runslow`; skipped by default.
- Marked `@pytest.mark.e2e`.
- Short fixture `.md` (1–2 sentences) → 1 topic → 1 suggestion → 1 viz.
- Wall-clock target < 5 minutes.
- `TOKEN_BUDGET_PER_JOB=30000` to cap real spend per run.
- Cleanup hook deletes the generated GitHub repo and `viz_outputs/` dir.

Run policy:
- Locally: before opening any Stage 2 or Stage 4 PR.
- CI: nightly cron; non-blocking.
- Before Railway production deploy: manual `make e2e` on dev machine.

### Frontend tests (Stage 4)

```
frontend/tests/
  unit/                  # Vitest — component logic, formatters, hooks
  contract/              # Validates types.gen.ts against /openapi.json
  e2e/                   # Playwright — happy path against a live backend
```

- **Unit (Vitest):** components rendered with React Testing Library; hooks
  isolated. Polling logic + formatters get the most coverage.
- **Contract:** `tsc --noEmit` verifies `types.gen.ts` compiles against actual
  component usage. Backend shape changes that the frontend hasn't picked up
  cause `npm run typecheck` to fail.
- **E2E (Playwright):** scripted parity checklist from Section 4 Stage 4. Runs
  in CI against an ephemeral backend container.

### Tooling & CI layout

```
job: lint              ruff + import-linter + mypy strict on touched modules
job: backend-unit      pytest tests/unit -n auto
job: backend-contract  pytest tests/contract
job: backend-integration  pytest tests/integration
job: frontend-typecheck   npm run typecheck (Stage 4+)
job: frontend-unit        npm run test (Stage 4+)
job: docker-build         docker buildx build (smoke)
job: e2e                  (manual / nightly) pytest -m e2e --runslow
```

Pre-commit hooks (recommended, not enforced):
- `ruff format` + `ruff check --fix`
- `mypy` on staged Python files in covered packages
- `npm run lint` on staged frontend files

### Coverage targets (informational, not enforced)

| Module | Target | Reason |
|---|---|---|
| `backend/llm/` | 90%+ | Pure logic, deterministic |
| `backend/services/` | 75%+ | Some subprocess interaction |
| `backend/api/` | 70%+ | Covered by contract suite |
| `backend/viz_generator/` (non-phase) | 70%+ | Parsing + file ops |
| `backend/viz_generator/phases/` | 40%+ | Mostly LLM + subprocess; covered by E2E |
| Frontend `src/lib/`, `src/api/` | 80%+ | Pure logic + types |
| Frontend `src/pages/`, `src/components/` | 50%+ | Visual; covered by E2E + manual QA |

### What testing does NOT include

- No mutation testing, no fuzz testing, no contract testing against OpenAI's API.
- No load/perf tests — single-user / small-team app.
- No automated a11y suite beyond Playwright defaults. Manual a11y review at
  Stage 4 cutover.

---

## Open questions

None blocking. The following are deferred to the implementation plan:
- Choice between `uv` and `pip-tools` for the lockfile (`uv` recommended).
- Whether GitHub Actions or Railway-native CI runs the suite.
- Exact `LLMTask` → model mapping defaults (rough defaults proposed in Section 2;
  finalised in the Stage 1 PR review).

## Related future work

1. **Observability layer.** Token/cost persistence (SQLite), per-pipeline live
   stream, historical dashboard. Parked. Will land as a separate spec after this
   project completes.
2. **Multi-process / horizontal scale.** Requires replacing the in-memory
   `JobStore` with persistent state. Out of scope here; partly enabled by the
   observability project's SQLite layer.
3. **Auth / multi-tenancy.** Out of scope.
