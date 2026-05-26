# Vanilla Viz Generator — Design Spec

**Date:** 2026-05-26
**Status:** Approved (brainstorming complete; awaiting plan)
**Project:** Universal-visualizer

## Summary

Replace the React+Vite+TypeScript viz generator with a vanilla HTML+CSS+JS pipeline targeting GitHub Pages.

The viz generator currently produces a multi-file React+Vite+TS project per build, runs `npm install` + `npm run build` (≈30–60s), then runs Playwright for runtime semantic checks. The output is published as a per-viz GitHub repo (Pages not enabled by the publisher — users enable it manually). Today's full pipeline image carries Node 20 + Playwright Chromium (~700–900MB).

For single-screen embedded widgets — which is what these visualizations are — the React stack is overkill. The new pipeline:
- LLM produces one self-contained `index.html` per viz (inline `<style>` and `<script>`, no external URLs).
- Playwright loads it once for a sanity check + screenshot. One fix-loop iteration if errors are detected; then the viz is marked failed if it still doesn't work.
- The publisher pushes `index.html` + `screenshot.png` to `<viz-slug>/` inside a single monorepo (`<owner>/<viz_monorepo_name>`) and ensures GitHub Pages is enabled on that monorepo.

Expected wins:
- Pipeline wall-clock: ~60–120s → ~5–10s per viz (no npm install, no npm build, fewer fix-loop iterations).
- Docker runtime image: drop Node 20 install (~400MB) from the runtime stage.
- Deployment friction: GitHub Pages auto-enabled; one URL per viz; no manual user setup.
- Code surface: ~30% reduction in `backend/viz_generator/` (six files deleted).

What does NOT change:
- The job lifecycle (`upload → topics → suggestions → building → done`).
- The HTTP API and the SPA architecture.
- The manifest model (just one field's phase enum changes).
- Existing already-published React vizes — they live in their per-viz GitHub repos forever, unchanged.
- `fixed_main_v6.py` subprocess argv contract.
- `backend/llm/` (per-task models, budgets, tracker).
- `backend/agents.py` (topic extraction + viz suggestions are pre-codegen LLM calls; unaffected).

## Section 1 — Architecture

```
Upload .md → topic extraction → suggestions → user picks → BUILD START
                                                                 │
                                              ┌──────────────────┴──────────────────┐
                                              │   backend/viz_generator/ pipeline   │
                                              │                                       │
                                              │   1. Draft (LLM call)                 │
                                              │      → one index.html string          │
                                              │                                       │
                                              │   2. Validate + screenshot (Playwright│
                                              │      single launch)                   │
                                              │      → if console.error / pageerror / │
                                              │        empty body: ONE fix-loop iter  │
                                              │      → else: 1280×800 screenshot.png  │
                                              │                                       │
                                              │   3. Polish (LLM call on working HTML)│
                                              │      → re-validate to catch regress.  │
                                              │                                       │
                                              │   4. Publish (GitHub API)             │
                                              │      → push index.html + screenshot   │
                                              │        to <monorepo>/<slug>/          │
                                              │      → ensure Pages enabled (idemp.)  │
                                              │                                       │
                                              └────────────────┬────────────────────┘
                                                               │
                                              Build manifest entry: status=done,
                                              embed_url=https://<owner>.github.io/<monorepo>/<slug>/
```

### Final file layout (end of project)

```
backend/viz_generator/
├── __init__.py
├── cli.py              # KEPT — subprocess argv contract; same flags as today
├── parsing.py          # KEPT — marker/codeblock parser
├── files.py            # SIMPLIFIED — write index.html + screenshot.png; no dep pinning
├── topic.py            # KEPT — classify_topic
├── prompts.py          # REWRITTEN — vanilla HTML/CSS/JS design language
├── validator.py        # NEW — Playwright load + console capture + screenshot in one launch
└── phases/
    ├── __init__.py
    ├── draft.py        # SIMPLIFIED — one LLM call → one HTML string
    └── polish.py       # SIMPLIFIED — operates on HTML; re-validates after

(DELETED: npm.py, postprocess.py, select.py, llm.py, screenshot.py)
(DELETED: phases/build_loop.py, phases/runtime_loop.py)
```

### Two non-obvious architectural choices

1. **Single self-contained HTML, not multi-file.** All CSS in `<style>`, all JS in `<script>`, no external `<script src="...">`, no CDN. Maximum portability — the file works offline, can be downloaded as a single artifact, has no path-resolution risk in iframe embeds. The LLM has fewer files to keep consistent.

2. **Single Playwright launch for both validation and screenshot.** The current pipeline launches Playwright multiple times across the runtime fix loop and a separate screenshot step. The new validator launches once — load, capture errors, and on success, take the screenshot. Saves ~3s of Chromium startup per build.

## Section 2 — Build pipeline details

### 2.1 Draft phase

One LLM call. Input: topic + brief + audience difficulty (same as today). Output: one HTML string conforming to `UNIVERSAL_SYSTEM_PROMPT`'s hard constraints:

- Exactly one file named `index.html`.
- All CSS inside `<style>` tags in `<head>`.
- All JavaScript inside `<script>` tags before `</body>`.
- No `<script src="https://...">`, no `<link rel="stylesheet" href="https://...">`. Fully offline-capable.
- The visualization must render meaningful content in `<body>` without user interaction (so screenshot capture works).
- `<html lang="en">`, `<meta charset="UTF-8">`, `<meta name="viewport" content="width=device-width, initial-scale=1.0">` required.

The new design language replaces the current 313-line React/Tailwind prompt with a much shorter vanilla design language:
- CSS custom properties on `:root` for theming (colors, spacing, motion timings).
- CSS Grid and Flexbox for layout.
- CSS keyframes and transitions for motion.
- Inline SVG for icons.
- Web Animations API or `setInterval` for time-based animation when CSS isn't expressive enough.
- No external icon set, no animation library, no UI framework.

Hard cap on output tokens (`MODEL_VIZ_DRAFT_MAX_TOKENS`, default ~16k) — fail-fast on truncation.

### 2.2 Validate + screenshot phase

Single function in `validator.py`. Launches Chromium once. Sequence:

1. `chromium.launch(headless=True)` → new context with viewport 1280×800.
2. Register listeners: `page.on("pageerror", …)`, `page.on("console", lambda msg: capture if msg.type == "error")`.
3. `page.goto(f"file://{path}/index.html", wait_until="networkidle", timeout=10000)`.
4. Semantic assertion: `await page.locator("body *").first().bounding_box()` must return a non-null box (catches blank pages).
5. **If any error captured:** kill the page, return `ValidationResult(success=False, error_log=…)` to caller.
6. **On success:** `page.screenshot(path=f"{project_dir}/screenshot.png")`. Then `browser.close()`. Return `ValidationResult(success=True, screenshot_path=…)`.

The fix-loop orchestration lives in `phases/draft.py`, not in `validator.py`. `draft.py`'s top-level function:
```
def run_draft_phase(topic, brief, …) -> DraftResult:
    html = _llm_call_draft(topic, brief)                  # initial generation
    result = validator.validate(html, project_dir)        # first attempt
    if not result.success:
        html = _llm_call_fix(html, result.error_log)      # one fix iteration
        result = validator.validate(html, project_dir)    # re-validate
    return DraftResult(html=html, result=result)
```
`validator.py` exposes a pure check; the fix-loop policy (one iteration max) lives in the phase that owns it. Clear separation of concerns.

The semantic assertion is intentionally minimal. We don't try to detect "the viz looks good." We catch: syntax errors, missing resources (would 404 since we banned external URLs), totally blank pages.

### 2.3 Polish phase

After validate succeeds: one LLM call asks for design refinement on the working HTML. Inputs: the topic, the polished-design-rubric (typography, spacing, motion smoothness), and the working HTML. Output: a refined HTML string.

After polish: re-run validator once. If polish broke something, attempt one fix iteration; if still broken, fall back to the pre-polish HTML and mark the build done (we'd rather ship an unpolished working viz than fail).

### 2.4 Publish phase

In `backend/github_publisher.py` (rewrite — see Section 3).

### 2.5 Phase enum on BuildTask

`backend/models.py` `BuildTask.phase` enum changes:

- Old: `queued, npm_install, npm_build, runtime, polish, screenshot, github, done, failed`
- New: `queued, draft, validate, polish, publish, done, failed`

The SPA `BuildCard.tsx` reads this enum to label progress. Phase labels updated to match.

## Section 3 — github_publisher.py rewrite

### 3.1 New config

`backend/config.py` additions:

- `viz_monorepo_name: str` — REQUIRED at publish time; e.g. `"lecture-visualizations"`. Stored in `.env`/`settings`.

Existing settings kept: `github_owner`, `github_token`.

### 3.2 New publisher logic

Replaces `publish_viz_repo()`. New function `publish_viz_to_monorepo(slug, project_dir, …) -> PublishResult`:

1. **Ensure monorepo exists.** `GET /repos/{owner}/{name}`. If 404 → `POST /user/repos` (or `/orgs/{org}/repos` for orgs) with `{name, auto_init: true, description, private: False}`. `auto_init: true` gives a default branch with a README, which we need to push commits against.

2. **Ensure Pages enabled.** `GET /repos/{owner}/{name}/pages`. If 404 → `POST /repos/{owner}/{name}/pages` with `{source: {branch: "main", path: "/"}}`. If response is 422 "already exists," treat as success (handles race conditions).

3. **Pick unique subdir.** Slug from the viz topic (e.g., `binary-search`). Probe `GET /repos/{owner}/{name}/contents/{slug}`. If 200 (subdir exists) → suffix with `-2`, `-3`, etc. Mirrors today's `_pick_unique_repo_name` pattern.

4. **Push two files via Git Data API.** One commit per viz:
   - `POST /repos/{owner}/{name}/git/blobs` for `index.html` (base64)
   - `POST /repos/{owner}/{name}/git/blobs` for `screenshot.png` (base64)
   - `GET /repos/{owner}/{name}/git/refs/heads/main` for parent SHA
   - `GET /repos/{owner}/{name}/git/trees/{sha}` for current tree
   - `POST /repos/{owner}/{name}/git/trees` with `base_tree` and new entries at `<slug>/index.html`, `<slug>/screenshot.png`
   - `POST /repos/{owner}/{name}/git/commits` with the new tree + parent
   - `PATCH /repos/{owner}/{name}/git/refs/heads/main` with the new commit SHA

5. **Return embed URL.** `https://<owner>.github.io/<monorepo>/<slug>/`. Plus the repo-edit URL `https://github.com/<owner>/<monorepo>/tree/main/<slug>`. Both end up in `manifest.embed_manifest_entry`.

### 3.3 Concurrency

The GitHub Git Data API requires the parent commit SHA. Concurrent pushes will race on the ref update — one will get 422 "ref not at expected SHA." Handle with a retry loop: refetch ref → rebuild tree on the new parent → retry commit + ref update. Cap at 3 retries. Acceptable since per-job builds are sequential within a job, but multiple users could push simultaneously.

### 3.4 What gets simpler

- No more "create new repo per viz" — one API roundtrip on first push, then zero on subsequent.
- No `wait_for_pages_live` polling — Pages is already enabled.
- The `BuildTask.github` status payload shape is unchanged (the SPA `GithubRepoStatus` component renders without modification).
- `sanitize_repo_name` → `sanitize_subdir_name` — allow more chars (subdirs aren't repo-name-restricted, but stay alnum + dash for URL cleanliness).

### 3.5 Known UX risks

- **Pages first-deploy latency.** Even with Pages already enabled, individual pushes take 30s–10min to go live in the CDN. Users may briefly see 404 after publish. Same UX as today, unchanged.
- **Pages build queue contention.** Rapid pushes to the same monorepo serialize Pages rebuilds. Acceptable for normal use; problematic only at extreme volume.

## Section 4 — Migration & cleanup

This is a clean replacement, not a parallel ship. After this work merges, the React/Vite/TS viz generator is gone.

### 4.1 Files deleted

- `backend/viz_generator/npm.py`
- `backend/viz_generator/postprocess.py`
- `backend/viz_generator/select.py`
- `backend/viz_generator/llm.py` (multi-provider client; consolidate by routing all viz-generator LLM calls through `backend/llm/`)
- `backend/viz_generator/screenshot.py` (folded into `validator.py`)
- `backend/viz_generator/phases/build_loop.py`
- `backend/viz_generator/phases/runtime_loop.py`
- All matching tests under `tests/unit/viz_generator/`

### 4.2 Files updated

- `backend/viz_generator/prompts.py` — full rewrite. New `UNIVERSAL_SYSTEM_PROMPT` for vanilla. Targeted: ~80 lines (vs today's 313).
- `backend/viz_generator/files.py` — substantial trim. Drop `enforce_pinned_deps`, dep-pinning markers, multi-file validation. Keep only "write one HTML file to disk."
- `backend/viz_generator/phases/draft.py` — single LLM call producing one HTML string.
- `backend/viz_generator/phases/polish.py` — single LLM call on working HTML; re-validates after.
- `backend/viz_generator/cli.py` — argv shape unchanged; internal pipeline simpler.
- `backend/services/build_orchestrator.py` — new phase enum (`draft → validate → polish → publish`). The phase-transition logic is shorter than today.
- `backend/models.py` `BuildTask.phase` — enum members change (Section 2.5).
- `backend/orchestrator.py` — drop any npm-related coordination; remains a thin subprocess spawner.
- `backend/llm/tasks.py` — drop `VIZ_BUILD_FIX`. Add `VIZ_RUNTIME_FIX` if not already there. Keep `VIZ_DRAFT`, `VIZ_POLISH`.
- `backend/config.py` — add `viz_monorepo_name`. Remove npm-related settings if any.
- `backend/github_publisher.py` — full rewrite (Section 3).
- `Dockerfile` — DROP the NodeSource install in the runtime stage. KEEP the frontend-builder stage (operator-facing SPA still builds with Node). Expected runtime image: ~500MB (Python + Playwright Chromium only).
- SPA `frontend/src/components/BuildCard.tsx` — update phase labels to match the new enum. No structural change.
- `.importlinter` — no changes needed; existing contracts already cover the new file set.

### 4.3 Existing manifests / published vizes

Already-published React vizes live in their per-viz GitHub repos forever. The in-memory `JobStore` is ephemeral (job_lifecycle TTL eviction). After deploy, in-flight jobs still showing old phase enum values will be evicted within the TTL window; no migration needed.

The new manifest entries will point at `https://<owner>.github.io/<monorepo>/<slug>/`. Existing entries continue to point at the per-viz repo URLs they always did.

### 4.4 No SPA cutover gymnastics

Unlike Stage 4, there's no parallel mount or rollback path. The SPA reads `BuildTask.phase` and `manifest.embed_url` — both are server-driven. Update the backend and the SPA picks up the new values automatically. The only SPA code change is the phase-label dictionary in `BuildCard.tsx`.

## Section 5 — Testing strategy

### 5.1 Unit tests (new)

`tests/unit/viz_generator/test_validator.py`:
- HTML with `<script>throw new Error("boom")</script>` → validator returns failed + captured error text contains "boom"
- HTML with `<body></body>` (no content) → semantic assertion fails (empty page)
- HTML with valid `<body><div>hi</div></body>` → validator returns success + screenshot.png exists at expected path
- One fix-loop iteration semantics: validator's caller invokes `fix_and_revalidate` exactly once on first failure; if second pass also fails, returns terminal failure (not 3 attempts)

`tests/unit/viz_generator/test_files.py`:
- `write_to_disk` writes exactly `index.html` + `screenshot.png` to `project_dir`; raises if HTML string is empty

`tests/unit/viz_generator/test_prompts.py`:
- `UNIVERSAL_SYSTEM_PROMPT` is non-empty
- Contains the hard constraints: "single file", "index.html", "no external URLs"
- Does NOT contain `react`, `tailwind`, `vite`, `framer-motion`, `zustand`, `import` (regression guard — these would mean the React prompt got accidentally restored)

`tests/unit/github/test_publisher.py`:
- Mock GitHub API via `responses`. Coverage:
  - Monorepo doesn't exist → POST creates it
  - Monorepo exists, Pages not enabled → enables Pages
  - Monorepo exists, Pages already enabled (422) → continues
  - Subdir already taken → suffix bumps to `-2`
  - Two-file commit posts the right blob SHAs and tree
  - Embed URL shape: `https://<owner>.github.io/<monorepo>/<slug>/`
  - Concurrency: stale parent SHA on ref update → retries with refreshed ref

### 5.2 Tests deleted

- `tests/unit/viz_generator/test_npm.py` (if exists)
- `tests/unit/viz_generator/test_postprocess.py`
- `tests/unit/viz_generator/test_select.py`
- `tests/unit/viz_generator/test_llm.py`
- Any tests targeting `build_loop` or `runtime_loop` directly

### 5.3 Contract tests

Existing `tests/contract/` suite stays. Update phase-string assertions if any test reads `BuildTask.phase` enum values — likely none, but a grep before the work confirms.

### 5.4 End-to-end

- Drop `tests/e2e/test_full_flow.py` (depends on npm install — too much overhead).
- Add a minimal e2e: run `python -m backend.viz_generator.cli --topic "binary search" --brief "..." --output-dir /tmp/e2e --job-id e2e-1`. Assert one `index.html` + one `screenshot.png` produced, both non-empty. Real OpenAI; gated behind `pytest -m e2e --runslow`.

### 5.5 CI

No CI changes in this work. GitHub Actions wiring remains a follow-up.

## Section 6 — Risks & open questions

### 6.1 Quality risk: vanilla viz output

The biggest unknown. Today's 313-line React/Tailwind prompt produces visually polished output. The new vanilla prompt will land at maybe 80 lines. Quality may be worse initially; the polish phase exists partly to compensate.

Mitigation: the user requested both phases (initial rewrite + prompt tuning) in one PR. After the new generator lands, run it against ~5 real lecture briefs, observe output, iterate on `prompts.py` before merging.

### 6.2 Screenshot quality

`page.screenshot()` at 1280×800 produces a static PNG. The current pipeline does the same. No change.

### 6.3 Pages build latency

Already discussed (Section 3.5). Unchanged from today's UX.

### 6.4 GitHub Pages concurrency

If a user generates many vizes rapidly, Pages rebuilds serialize. Acceptable for current scale; would become a problem only with sustained high-volume generation.

### 6.5 Monorepo size growth

Each viz adds 2 files (~20–100KB each) to the monorepo. After 10,000 vizes that's still under 1GB. No issue at any realistic scale for a single user/org.

### 6.6 What if a viz needs more than CSS animation?

The hard constraint forbids CDN scripts. If a topic genuinely needs Three.js or a complex chart library, the LLM has to either implement from scratch in inline JS or refuse. Most embedded explanatory visualizations don't need this. If observed empirically, we revisit the constraint.

## Section 7 — Implementation phasing

User requested: one PR combining the rewrite AND prompt tuning. Plan:

1. Implement the new pipeline end-to-end with a first-draft `prompts.py`.
2. Run against ~5 real-world lecture briefs before merging.
3. Iterate on `prompts.py` and `polish.py` based on actual output.
4. Merge only when output quality is at least on par with the React version.

Tag at merge: `vanilla-viz-stage-1`.

## Related future work (NOT this plan)

These remain follow-ups, deferred:

1. Observability layer (token/cost persistence + dashboard) — the user's original "after modularization" project.
2. mypy `--strict` CI gate.
3. GitHub Actions CI for ruff, mypy, import-linter, pytest, npm build.
4. Removal of `backend/legacy/` (Stage 4's one-release-cycle marker).
5. Lockfile regeneration cadence (post-merge of any pyproject.toml change).

These are tracked separately from this design.
