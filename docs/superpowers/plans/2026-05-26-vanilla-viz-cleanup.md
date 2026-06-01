# Vanilla Viz Cleanup Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Remove dead React/Vite/Gemini code left behind after the vanilla-viz rewrite. Consolidate `backend/viz_generator/llm.py` into `backend/llm/` (OpenAI only, Gemini path dropped). Delete dead modules + settings. Sweep stale docstrings.

**Architecture decisions (made during brainstorming):**
- **OpenAI only** — Gemini branches, `LLM_PROVIDER` env, `_GEMINI_PRICES` table all go. Tasks specify their model via `LLMTask` routing; global `MODEL_NAME` global is removed.
- **One `llm_call`** — the existing `backend/llm/client.py:llm_call(system_prompt, user_prompt, …)` becomes the only entry point. The three viz call sites all use exactly system + user messages, so they migrate without API changes.
- **Keep LangSmith** — port the optional `wrap_openai` wiring from `viz_generator/llm.py` into `backend/llm/client.py`.
- **Keep `fixed_main_v6.py` stub + the subprocess contract** — that's a separate concern; this PR is focused on dead-code removal and llm consolidation.
- **Stale docstrings: fix in the same files we're already touching.** Don't do a separate sweep.

**Tech stack:** Python 3.12, FastAPI, OpenAI SDK, pydantic-settings.

**Branch:** `feat/vanilla-viz-cleanup` (already created off `feat/vanilla-viz-spec`).
**Base for PR:** `main` (after `feat/vanilla-viz-spec` merges) OR `feat/vanilla-viz-spec` if landing both PRs together.

---

## File Structure

**Deleted:**
- `backend/viz_generator/llm.py` (consolidated into `backend/llm/client.py`)
- `backend/viz_generator/parsing.py` (unused by new pipeline)
- `tests/unit/viz_generator/test_parsing.py`
- `backend/dev_server.py` (old vite-preview manager)
- `backend/legacy/` (one-file pre-modularization snapshot)

**Modified:**
- `backend/llm/client.py` — adds LangSmith wrap
- `backend/viz_generator/cli.py` — imports + startup log
- `backend/viz_generator/topic.py` — call-site migration
- `backend/viz_generator/phases/draft.py` — call-site migration
- `backend/viz_generator/phases/polish.py` — call-site migration
- `backend/config.py` — drop 6 dead settings
- `frontend/src/*` — stale copy ("React app", "npm build loop", "brand-new GitHub repo", `fixed_main_v6.py`)
- Stale docstrings in `backend/__init__.py`, `backend/models.py`, `backend/orchestrator.py`, `backend/services/build_orchestrator.py`, `backend/agents.py`

**Untouched (keep):**
- `backend/viz_generator/topic.py` (still used by cli — only its llm_call site changes)
- `backend/viz_generator/files.py`, `events.py`, `validator.py`, `prompts.py`, `cli.py`, `phases/*` (the new pipeline)
- `fixed_main_v6.py` stub + the subprocess contract tests
- `backend/llm/tracker.py`, `pricing.py`, `tasks.py`, `errors.py`, `reasoning.py`
- All API routers, services, models (beyond docstring sweeps)

---

## Implementation Order

Each task is incremental and the test suite stays green between commits.

1. Add LangSmith wrap to `backend/llm/client.py`
2. Migrate `topic.py` to the consolidated `llm_call`
3. Migrate `phases/draft.py`
4. Migrate `phases/polish.py`
5. Migrate `cli.py` (startup log + imports)
6. Delete `backend/viz_generator/llm.py`
7. Delete `backend/dev_server.py` + drop the 6 dead settings from `config.py`
8. Delete `backend/viz_generator/parsing.py` + its test
9. Delete `backend/legacy/`
10. Sweep stale docstrings in 5 files
11. Update frontend copy + rebuild
12. Final verification + commit summary

---

## Task 1 — Add LangSmith tracing to `backend/llm/client.py`

**Files:** Modify `backend/llm/client.py`.

**Why:** The old `viz_generator/llm.py` wraps the OpenAI client with `langsmith.wrappers.wrap_openai` when `LANGSMITH_API_KEY` is set. We want that observability preserved post-consolidation.

- [ ] **Step 1: Read the current `backend/llm/client.py`** — verify the `get_client()` body so the LangSmith wrap goes in the right place.

- [ ] **Step 2: Add the wrap**

After the line that creates `_client = OpenAI(...)`, insert an optional LangSmith wrap. The full updated `get_client()` body should look like:

```python
def get_client() -> OpenAI:
    """Return the lazily-initialised OpenAI client singleton."""
    global _client
    if _client is not None:
        return _client

    api_key = settings.openai_api_key
    if not api_key:
        logger.error("[FATAL] OPENAI_API_KEY missing. Add it to .env")
        sys.exit(1)

    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client_timeout = settings.llm_client_timeout
    client = OpenAI(api_key=api_key, timeout=client_timeout, max_retries=0)
    logger.info("[LLM] OpenAI client ready  timeout=%.0fs", client_timeout)
    logger.info(
        "[LLM] Reasoning models (gpt-5*/o-series) will use max_completion_tokens + reasoning_effort=%s",
        REASONING_EFFORT,
    )

    # Optional LangSmith tracing: only active when langsmith is installed
    # AND LANGSMITH_API_KEY is set. Silent no-op otherwise.
    import os
    if os.getenv("LANGSMITH_API_KEY"):
        try:
            from langsmith.wrappers import wrap_openai
            os.environ.setdefault("LANGSMITH_TRACING", "true")
            os.environ.setdefault("LANGSMITH_PROJECT", "viz-agent")
            client = wrap_openai(client)
            logger.info(
                "[LLM] LangSmith tracing: ENABLED  (project=%s)",
                os.environ["LANGSMITH_PROJECT"],
            )
        except ImportError:
            logger.info("[LLM] LangSmith tracing: disabled (langsmith not installed)")
    else:
        logger.info("[LLM] LangSmith tracing: disabled (LANGSMITH_API_KEY not set)")

    _client = client
    return _client
```

(The only change from the existing file is the `client = OpenAI(...)` line — drop the `_client =` assignment so we can rebind through `wrap_openai` before assigning the module global.)

- [ ] **Step 3: Sanity test**

Run: `.venv/bin/pytest tests/unit/llm -v`
Expected: existing tests still pass. No new tests for LangSmith — it's environment-gated; a real test would need to install/mock the library.

- [ ] **Step 4: Commit**

```bash
git add backend/llm/client.py
git commit -m "feat(llm): port optional LangSmith client wrap from viz_generator/llm.py"
```

---

## Task 2 — Migrate `backend/viz_generator/topic.py` to consolidated `llm_call`

**Files:** Modify `backend/viz_generator/topic.py`.

The current call uses the old viz signature `llm_call(messages=[{"role": "user", "content": classify_prompt}], …)`. New signature is `llm_call(system_prompt, user_prompt, step_label, task=…, …)`. For the classify call, system prompt can be empty.

- [ ] **Step 1: Read `backend/viz_generator/topic.py`** — find the `llm_call(...)` invocation (around line 290 in `classify_topic`).

- [ ] **Step 2: Update the import**

Replace:
```python
from backend.viz_generator.llm import llm_call
```
with:
```python
from backend.llm.client import llm_call
```

- [ ] **Step 3: Update the call site**

Replace:
```python
raw_name = llm_call(
    [{"role": "user", "content": classify_prompt}],
    temperature=1,
    max_tokens=1000,
    step_label="step0_classify",
    task=LLMTask.VIZ_TOPIC_CLASSIFY,
).strip().lower()
```

with:
```python
raw_name = llm_call(
    system_prompt="",
    user_prompt=classify_prompt,
    step_label="step0_classify",
    task=LLMTask.VIZ_TOPIC_CLASSIFY,
    temperature=1,
    max_tokens=1000,
).strip().lower()
```

(Same args; just reshape the messages into the new signature.)

- [ ] **Step 4: Run the related test**

Run: `.venv/bin/pytest tests/unit/viz_generator -v -k "not test_parsing"`
Expected: green (parsing test gets deleted in Task 8).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/topic.py
git commit -m "refactor(viz): migrate topic.classify_topic to backend.llm.client.llm_call"
```

---

## Task 3 — Migrate `backend/viz_generator/phases/draft.py` to consolidated `llm_call`

**Files:** Modify `backend/viz_generator/phases/draft.py`.

- [ ] **Step 1: Update imports**

Replace:
```python
from backend.viz_generator.llm import llm_call
```
with:
```python
from backend.llm.client import llm_call
```

- [ ] **Step 2: Update `_llm_call_draft`**

Replace:
```python
raw = llm_call(
    [
        {"role": "system", "content": UNIVERSAL_SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ],
    temperature=1,
    max_tokens=MODEL_VIZ_DRAFT_MAX_TOKENS,
    step_label="draft",
    task=LLMTask.VIZ_DRAFT,
)
```

with:
```python
raw = llm_call(
    system_prompt=UNIVERSAL_SYSTEM_PROMPT,
    user_prompt=user_prompt,
    step_label="draft",
    task=LLMTask.VIZ_DRAFT,
    temperature=1,
    max_tokens=MODEL_VIZ_DRAFT_MAX_TOKENS,
)
```

- [ ] **Step 3: Update `_llm_call_fix`**

Same shape change:
```python
raw = llm_call(
    system_prompt=UNIVERSAL_SYSTEM_PROMPT,
    user_prompt=user_prompt,
    step_label="draft_fix",
    task=LLMTask.VIZ_RUNTIME_FIX,
    temperature=1,
    max_tokens=MODEL_VIZ_FIX_MAX_TOKENS,
)
```

- [ ] **Step 4: Run draft tests**

Run: `.venv/bin/pytest tests/unit/viz_generator/phases/test_draft.py -v`
Expected: 3/3 PASS (tests patch `_llm_call_draft`/`_llm_call_fix` so they don't actually exercise `llm_call`).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/phases/draft.py
git commit -m "refactor(viz): migrate draft phase llm_call to backend.llm.client"
```

---

## Task 4 — Migrate `backend/viz_generator/phases/polish.py`

**Files:** Modify `backend/viz_generator/phases/polish.py`.

Same pattern as Task 3.

- [ ] **Step 1: Update import**

Replace `from backend.viz_generator.llm import llm_call` with `from backend.llm.client import llm_call`.

- [ ] **Step 2: Update `_llm_call_polish`**

Replace:
```python
raw = llm_call(
    [
        {"role": "system", "content": UNIVERSAL_SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ],
    temperature=1,
    max_tokens=MODEL_VIZ_POLISH_MAX_TOKENS,
    step_label="polish",
    task=LLMTask.VIZ_POLISH,
)
```

with:
```python
raw = llm_call(
    system_prompt=UNIVERSAL_SYSTEM_PROMPT,
    user_prompt=user_prompt,
    step_label="polish",
    task=LLMTask.VIZ_POLISH,
    temperature=1,
    max_tokens=MODEL_VIZ_POLISH_MAX_TOKENS,
)
```

- [ ] **Step 3: Run polish tests**

Run: `.venv/bin/pytest tests/unit/viz_generator/phases/test_polish.py -v`
Expected: 3/3 PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/viz_generator/phases/polish.py
git commit -m "refactor(viz): migrate polish phase llm_call to backend.llm.client"
```

---

## Task 5 — Migrate `backend/viz_generator/cli.py` startup + imports

**Files:** Modify `backend/viz_generator/cli.py`.

The current cli.py imports `LLM_PROVIDER`, `MODEL_NAME`, `TOKEN_BUDGET`, `_init_client`, `status`, `token_tracker` from `backend.viz_generator.llm`. All of those go away with the consolidation:

- `LLM_PROVIDER` → gone (OpenAI only)
- `MODEL_NAME` → replaced with `settings.openai_text_model` (the global default; per-task routing reports its own model in the `[LLM]` log lines)
- `TOKEN_BUDGET` → replaced with `settings.token_budget_per_job`
- `_init_client` → replaced with `get_client()` from `backend.llm.client` (or just rely on lazy init — calling `get_client()` upfront is optional)
- `status` → inline the small helper into cli.py (it's only used here)
- `token_tracker` → use `backend.llm.tracker.token_tracker`

- [ ] **Step 1: Rewrite imports + helpers**

Replace the imports block:

```python
from backend.viz_generator.llm import (
    LLM_PROVIDER,
    MODEL_NAME,
    TOKEN_BUDGET,
    _init_client,
    status,
    token_tracker,
)
```

with:

```python
from backend.config import settings
from backend.llm.client import get_client
from backend.llm.tracker import token_tracker
```

Add the inlined `status` helper near the top of cli.py (after `log = logging.getLogger(...)`):

```python
def status(stage: str, detail: str = "") -> None:
    """Print a visible phase marker; the orchestrator's stdout parser keys
    on these for SPA progress-bar updates."""
    bar = "─" * 4
    if detail:
        log.info("\n%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        log.info("\n%s [STATUS] %s %s", bar, stage, bar)
```

- [ ] **Step 2: Update `main()` startup log**

Replace:
```python
_init_client()
...
log.info(
    "[Config] Token budget: %d  Model: %s  Provider: %s",
    TOKEN_BUDGET, MODEL_NAME, LLM_PROVIDER,
)
```

with:
```python
get_client()  # lazily initialise + log readiness
...
log.info(
    "[Config] Token budget: %d  Default model: %s  (per-task routing via LLMTask)",
    settings.token_budget_per_job, settings.openai_text_model,
)
```

- [ ] **Step 3: Replace `token_tracker.print_summary()`**

`backend.llm.tracker.token_tracker` has a `print_summary()` method too — confirm by reading `backend/llm/tracker.py`. If the method exists with the same name, no call-site change. If named differently, adapt to whatever the tracker exports.

Run a quick check:
```bash
grep -n "def print_summary\|def summary\|def log_summary" backend/llm/tracker.py
```

If `print_summary` doesn't exist on `backend.llm.tracker.token_tracker`, either:
- (a) add a `print_summary()` method that mirrors the viz tracker's output format, OR
- (b) inline a small summary printer in cli.py.

Pick (a) if the method is genuinely useful elsewhere; otherwise (b).

- [ ] **Step 4: Run cli contract tests**

Run: `.venv/bin/pytest tests/contract/test_viz_cli.py -v`
Expected: 5/5 PASS (argv contract preserved).

- [ ] **Step 5: Smoke-test the CLI**

Run from a tmp dir:
```bash
cd /tmp && rm -rf viz-smoke && mkdir viz-smoke && cd viz-smoke && \
  MODEL_VIZ_DRAFT_MAX_TOKENS=32000 MODEL_VIZ_POLISH_MAX_TOKENS=32000 \
  /Users/pulkitmangal/Universal-visualizer/.venv/bin/python -m backend.viz_generator.cli \
    --topic "binary search" --polish
```

Expected: completes, produces `binary-search-viz/{index.html,screenshot.png}`. Token summary prints at the end.

- [ ] **Step 6: Commit**

```bash
git add backend/viz_generator/cli.py
git commit -m "refactor(viz): cli uses consolidated llm client + settings (drop legacy globals)"
```

---

## Task 6 — Delete `backend/viz_generator/llm.py`

**Files:** Delete `backend/viz_generator/llm.py`.

- [ ] **Step 1: Confirm no remaining importers**

Run:
```bash
rg -n "from backend.viz_generator.llm\b" backend tests
```
Expected: empty (everything migrated in Tasks 2-5).

If anything remains, fix it before deleting.

- [ ] **Step 2: Delete**

```bash
git rm backend/viz_generator/llm.py
```

- [ ] **Step 3: Verify import-linter + full suite**

Run:
```bash
.venv/bin/lint-imports
.venv/bin/pytest tests/unit tests/contract -q
```

Both should be green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(viz): delete viz_generator/llm.py — consolidated into backend/llm/client.py"
```

---

## Task 7 — Delete `backend/dev_server.py` + drop dead settings

**Files:** Delete `backend/dev_server.py`. Modify `backend/config.py`.

- [ ] **Step 1: Confirm nothing imports dev_server**

```bash
rg -n "from backend.dev_server\|import.*dev_server" backend tests
```
Expected: empty.

- [ ] **Step 2: Delete dev_server.py**

```bash
git rm backend/dev_server.py
```

- [ ] **Step 3: Drop dead settings from `backend/config.py`**

Remove these 5 settings (search for each line and delete):

```python
    dev_server_port_start: int = 5180
    dev_server_port_end: int = 5230
    preview_boot_wait: int = 45
    npm_install_timeout: int = 300
    audit_fix_enabled: bool = False
```

Also remove the comment block above them:
```python
    # ── Dev server (Stage 3) ─────────────────────────────────
```

Also drop `github_include_dist` from the GitHub publish section — the monorepo publisher doesn't take it:
```python
    github_include_dist: bool = True
```

- [ ] **Step 4: Confirm nothing references the dropped settings**

```bash
rg -n "dev_server_port|preview_boot_wait|npm_install_timeout|audit_fix_enabled|github_include_dist" backend tests
```
Expected: empty.

- [ ] **Step 5: Run config + full unit tests**

```bash
.venv/bin/pytest tests/unit/config -v
.venv/bin/pytest tests/unit tests/contract -q
```

Both green.

- [ ] **Step 6: Commit**

```bash
git add backend/config.py
git commit -m "chore(config): drop dev_server.py + 5 dead settings (dev_server_port_*, preview_boot_wait, npm_install_timeout, audit_fix_enabled) + github_include_dist"
```

---

## Task 8 — Delete `backend/viz_generator/parsing.py` + its test

**Files:** Delete two files.

- [ ] **Step 1: Confirm parsing is unused by production**

```bash
rg -n "from backend.viz_generator.parsing\|backend\.viz_generator\.parsing" backend
```
Expected: empty (only `tests/unit/viz_generator/test_parsing.py` should reference it, which we're also deleting).

- [ ] **Step 2: Delete**

```bash
git rm backend/viz_generator/parsing.py tests/unit/viz_generator/test_parsing.py
```

- [ ] **Step 3: Run full suite + import-linter**

```bash
.venv/bin/pytest tests/unit tests/contract -q
.venv/bin/lint-imports
```

Both green.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore(viz): delete unused parsing.py (multi-file parser; vanilla pipeline is single-file)"
```

---

## Task 9 — Delete `backend/legacy/`

**Files:** Delete the directory.

- [ ] **Step 1: Confirm nothing references it**

```bash
rg -n "backend/legacy\|backend\.legacy" backend tests frontend
```

Expected: no Python/TS imports. May find docstring/comment mentions; those go in Task 10.

- [ ] **Step 2: Delete**

```bash
git rm -r backend/legacy
```

- [ ] **Step 3: Verify**

```bash
.venv/bin/pytest tests/unit tests/contract -q
```

Green.

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: delete backend/legacy/ (pre-modularization landing page snapshot)"
```

---

## Task 10 — Sweep stale docstrings

**Files:** Modify `backend/__init__.py`, `backend/models.py`, `backend/orchestrator.py`, `backend/services/build_orchestrator.py`, `backend/agents.py`.

Goal: replace stale React/npm/Vite/fixed_main_v6 references in docstrings + comments with the current vanilla-pipeline reality. **Behavior unchanged** — these are pure text edits.

- [ ] **Step 1: For each file below, read the current docstring + comments and update**

The exact wording is your call. Examples of what to fix:

**backend/__init__.py** (around line 3):
> "Houses all server-side Python code. Top-level files (main.py, fixed_main_v6.py, …)"
Update to mention `backend.viz_generator.cli` as the subprocess entry (via `fixed_main_v6.py` stub).

**backend/models.py** (around line 6):
> "Each topic the user picks spawns a 'BuildTask' which runs fixed_main_v6.py in a subprocess"
Reword: subprocess runs `backend.viz_generator.cli` (entered via `fixed_main_v6.py` stub).

**backend/models.py** (line 79): `# Build task (calls fixed_main_v6.py)` → `# Build task (spawns the vanilla viz generator subprocess)`

**backend/models.py** (around line 104): `project_dir: str = ""               # path to the generated Vite project` → `# path to the generated viz project directory (contains index.html + screenshot.png)`

**backend/orchestrator.py** (docstring + comments referencing `fixed_main_v6.py creates them in cwd`, `npm`, etc.) — reword to describe the vanilla-cli subprocess.

**backend/services/build_orchestrator.py** (around line 4):
> "It spawns fixed_main_v6.py (via backend.viz_generator.cli) as a subprocess, streams its progress into the JobState, optionally publishes the result to GitHub"
This is mostly accurate but could trim "spawns fixed_main_v6.py (via backend.viz_generator.cli)" to "spawns the vanilla viz generator subprocess". Update line 63 (`fixed_main_v6.py tries to slug them`) similarly.

**backend/agents.py** (around line 347 + 360 + 367) — mentions `fixed_main_v6.py`. Reword to subprocess / cli.

- [ ] **Step 2: Run full suite to confirm zero behavior change**

```bash
.venv/bin/pytest tests/unit tests/contract -q
```

Green.

- [ ] **Step 3: Commit**

```bash
git add backend/__init__.py backend/models.py backend/orchestrator.py backend/services/build_orchestrator.py backend/agents.py
git commit -m "docs: sweep stale React/npm/fixed_main_v6.py references in docstrings"
```

---

## Task 11 — Update frontend copy + rebuild

**Files:** Modify `frontend/src/` (find the strings below), rebuild via `npm run build`.

The current SPA has stale user-facing copy that says things like:
> "Each viz spawns a fixed_main_v6.py subprocess that drafts the React app, runs the npm build loop, validates runtime behavior, and screenshots the result."
> "On success, the generated viz will be pushed to a brand-new GitHub repo (if GITHUB_TOKEN is configured on the server)."

Both are wrong post-rewrite.

- [ ] **Step 1: Find the strings**

```bash
rg -n "fixed_main_v6\|drafts the React app\|npm build loop\|brand-new GitHub repo\|standalone repo" frontend/src
```

The user-facing copy lives in TSX components (probably `App.tsx`, build/suggestion-related components).

- [ ] **Step 2: Update copy**

Suggested replacements:

| Old | New |
|---|---|
| "Each viz spawns a fixed_main_v6.py subprocess that drafts the React app, runs the npm build loop, validates runtime behavior, and screenshots the result." | "Each viz spawns the vanilla generator subprocess: it drafts a self-contained HTML page, runs a single Playwright validation pass, polishes the design, and screenshots the result." |
| "On success, the generated viz will be pushed to a brand-new GitHub repo (if GITHUB_TOKEN is configured on the server)." | "On success, the viz is pushed to a subdirectory of your monorepo (set `VIZ_MONOREPO_NAME` + `GITHUB_TOKEN` on the server) and served via GitHub Pages at `https://<owner>.github.io/<monorepo>/<slug>/`." |
| "View on GitHub ↗" (button) | keep — still goes to the monorepo |
| "Each viz has its own standalone repo. Clone, deploy, and embed at your leisure." | "All vizes live in a single monorepo as subdirectories — each served at its own GitHub Pages URL." |

- [ ] **Step 3: Rebuild**

```bash
cd frontend && npm run build
```

Verify clean tsc + vite build, then check `backend/static/index.html` updated.

- [ ] **Step 4: Smoke-test in browser**

```bash
.venv/bin/uvicorn backend.main:app --port 8001 &
sleep 2 && open http://localhost:8001/
# verify the strings updated in the upload + build + manifest screens
kill %1
```

- [ ] **Step 5: Commit**

```bash
git add frontend/src backend/static
git commit -m "docs(spa): replace stale React/npm/per-viz-repo copy in user-facing strings"
```

(`backend/static/` is gitignored except `backend/static/index.html` and `backend/static/assets/` — check what's actually tracked with `git status` and adjust the `git add`.)

Actually verify first:
```bash
git check-ignore backend/static/index.html backend/static/assets/index-*.js
```
If those are gitignored, the rebuild artifacts won't be staged and only `frontend/src` changes go in the commit. CI/deploy must run `npm run build` to materialise them. That's the existing convention.

---

## Task 12 — Final verification + push

- [ ] **Step 1: Full test sweep**

```bash
.venv/bin/pytest tests/unit tests/contract -q
.venv/bin/lint-imports
cd frontend && npm run build && cd ..
```

All green.

- [ ] **Step 2: Inventory check**

Run these and confirm clean:
```bash
rg -n "viz_generator\.(npm|postprocess|select|screenshot|llm|parsing)\b" backend tests
rg -n "publish_viz_repo\b" backend tests
rg -n "dev_server\b" backend tests
rg -n "LLM_PROVIDER\|MODEL_NAME\|TOKEN_BUDGET\b" backend tests
rg -n "import.*gemini\|gemini" backend tests | grep -iv "test\|comment\|docstring"
```

All should return zero/near-zero hits.

- [ ] **Step 3: Push**

```bash
git push -u origin feat/vanilla-viz-cleanup
```

- [ ] **Step 4: Open PR** (whichever path: `gh pr create` if authed, or the browser compare URL)

PR title: `chore(viz): consolidate llm client + delete dead React/Gemini code`

PR body should summarise:
- llm.py consolidation (Gemini dropped, single OpenAI client via backend/llm/)
- Deleted: dev_server.py, parsing.py, legacy/, 6 dead settings
- Frontend copy updated for vanilla pipeline reality
- Stale docstrings swept across 5 files
- Tests: 167 still passing, import-linter clean

---

## Self-Review

**Spec coverage:** Each design decision from the brainstorming session has a task: Gemini drop (Task 6 via deleting llm.py), single `llm_call` (Tasks 2-5), LangSmith preserved (Task 1), `fixed_main_v6.py` stub kept (explicit non-goal, docstrings just clarified), dev_server.py + parsing.py + legacy/ deleted (Tasks 7-9), stale docstrings swept (Task 10 + 11).

**Placeholder scan:** Every step has explicit code or commands. The Task 5 step 3 has a conditional ("if print_summary doesn't exist on the new tracker, choose A or B") — this is genuine optionality based on what the reader finds, not a placeholder. The Task 10 stale-docstring step gives examples rather than verbatim diffs because the surrounding text is subjective wording — acceptable since the spec is just "remove stale React/npm references."

**Type consistency:** No new types introduced. `LLMTask`, `token_tracker`, `get_client`, `llm_call` are reused from existing `backend/llm/`. Tracker method `print_summary` flagged as potentially absent — Task 5 includes a check + fix branch.

**Scope check:** Single PR. Each task commits cleanly. Suite stays green between commits. No external system changes beyond optional LangSmith.
