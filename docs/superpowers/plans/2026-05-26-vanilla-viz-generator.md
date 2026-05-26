# Vanilla Viz Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the React+Vite+TS viz generator with a vanilla HTML+CSS+JS pipeline that emits one self-contained `index.html` per viz and publishes to a single monorepo with GitHub Pages.

**Architecture:** LLM produces one self-contained `index.html` (inline `<style>`/`<script>`, no external URLs). A single Playwright launch validates + screenshots. One fix-loop iteration on validation failure, then polish, then re-validate. Publisher pushes `index.html` + `screenshot.png` to `<owner>/<viz_monorepo_name>/<slug>/` via the Git Data API and ensures Pages is enabled. The job lifecycle, HTTP API, SPA architecture, and `--topic`/`--polish` subprocess argv contract are all preserved.

**Tech Stack:** Python 3.12, FastAPI, pydantic, pydantic-settings, OpenAI client (`backend.viz_generator.llm` and `backend.llm`), Playwright (chromium), `requests` for GitHub API, vanilla HTML/CSS/JS for the generated artifact.

**Spec:** [docs/superpowers/specs/2026-05-26-vanilla-viz-generator-design.md](../specs/2026-05-26-vanilla-viz-generator-design.md)

---

## Implementation Order

The plan is grouped into 8 phases that can mostly be executed in order. The new generator modules (validator, prompts, files, draft, polish, cli) are built and unit-tested **before** any old file is deleted, so the codebase stays runnable through most of the plan. The "delete old files" tasks come near the end, paired with `import-linter` and test-suite runs to catch dangling references.

Phase 1: Models + config + LLMTask cleanup (additive, no behavior change)
Phase 2: Build the new viz-generator modules end-to-end (TDD)
Phase 3: Rewrite the `cli.py` orchestrator
Phase 4: Update backend orchestrator + build_orchestrator to use the new phase names + drop postprocess hooks
Phase 5: Rewrite `github_publisher.py` for monorepo + Pages
Phase 6: Frontend `BuildCard.tsx` phase labels + manifest `embed_url`
Phase 7: Delete the dropped files + their tests; run full suite + import-linter
Phase 8: Dockerfile cleanup + end-to-end smoke + prompt iteration

---

## Phase 1 — Models, config, and LLMTask

### Task 1: Add `viz_monorepo_name` setting

**Files:**
- Modify: `backend/config.py:67-112` (add field in the existing Settings class)
- Test: `tests/unit/config/test_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# Append to tests/unit/config/test_settings.py
def test_viz_monorepo_name_defaults_to_empty_string(monkeypatch):
    """The setting must exist with a safe empty default; callers raise if used unset."""
    monkeypatch.delenv("VIZ_MONOREPO_NAME", raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.viz_monorepo_name == ""

def test_viz_monorepo_name_from_env(monkeypatch):
    monkeypatch.setenv("VIZ_MONOREPO_NAME", "lecture-visualizations")
    from backend.config import Settings
    s = Settings()
    assert s.viz_monorepo_name == "lecture-visualizations"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/config/test_settings.py::test_viz_monorepo_name_defaults_to_empty_string tests/unit/config/test_settings.py::test_viz_monorepo_name_from_env -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'viz_monorepo_name'`.

- [ ] **Step 3: Add the field**

In [backend/config.py](../../../backend/config.py), inside `class Settings`, add a new block after the GitHub publish section (after `github_repos_private: bool = False` around line 99):

```python
    # ── Vanilla viz monorepo (vanilla-viz-stage-1) ───────────
    # Name of the GitHub repo that holds all published vanilla vizes as
    # subdirectories. REQUIRED at publish time; empty default makes test
    # construction work without env config.
    viz_monorepo_name: str = ""
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/config/test_settings.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/unit/config/test_settings.py
git commit -m "feat(config): add viz_monorepo_name setting for vanilla viz publish target"
```

---

### Task 2: Update `BuildPhase` enum to new vanilla phase names

**Files:**
- Modify: `backend/models.py:82-90` (the `BuildPhase` Literal)
- Test: add a new file `tests/unit/test_models_phase.py`

This is a breaking enum change. The new values are `queued, draft, validate, polish, publish, done, failed`. We do the swap here; subsequent tasks (orchestrator, build_orchestrator, BuildCard) update their consumers.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_models_phase.py`:

```python
"""Locks the new vanilla viz BuildPhase enum members."""
from backend.models import BuildPhase, BuildTask


def test_buildphase_members_match_vanilla_pipeline():
    # Literal members are not introspectable as an iterable in py3.12 without
    # typing.get_args, so use that. Order is part of the contract — orchestrator
    # phase-detection relies on it for the SPA progress bar.
    from typing import get_args
    assert get_args(BuildPhase) == (
        "queued", "draft", "validate", "polish", "publish", "done", "failed",
    )


def test_buildtask_phase_default_is_queued():
    t = BuildTask(id="b1", topic_id="t1")
    assert t.phase == "queued"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_models_phase.py -v`
Expected: FAIL on the `get_args` comparison (current members differ).

- [ ] **Step 3: Update the enum**

In [backend/models.py:82-90](../../../backend/models.py#L82-L90), replace the `BuildPhase` Literal:

```python
BuildPhase = Literal[
    "queued",
    "draft",
    "validate",
    "polish",
    "publish",
    "done",
    "failed",
]
```

- [ ] **Step 4: Run the new test**

Run: `pytest tests/unit/test_models_phase.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full unit suite to see which call sites break**

Run: `pytest tests/unit -q`
Expected: Failures in `tests/unit/services/test_build_orchestrator.py` referencing old phase strings (`completed`, `step1_generate`, etc.). These are fixed in Phase 4. Capture the failure list, leave them red for now.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py tests/unit/test_models_phase.py
git commit -m "feat(models): switch BuildPhase to vanilla viz enum (draft/validate/polish/publish)"
```

---

### Task 3: Add `embed_url` and `repo_edit_url` to `BuildTask` + `EmbedManifestEntry`

**Files:**
- Modify: `backend/models.py:93-115` (`BuildTask`)
- Modify: `backend/models.py:149-161` (`EmbedManifestEntry`)
- Test: extend `tests/unit/test_models_phase.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_models_phase.py`:

```python
def test_buildtask_has_embed_url_fields():
    t = BuildTask(id="b1", topic_id="t1")
    # New fields default to empty strings; publisher populates them on success.
    assert t.embed_url == ""
    assert t.repo_edit_url == ""
    assert t.monorepo_name == ""


def test_embed_manifest_entry_has_embed_url():
    from backend.models import EmbedManifestEntry
    e = EmbedManifestEntry(
        section="## S", embed_after_sentence="x.", topic="T",
        why_visual_helps="y", viz_title="v", viz_brief="b", project_dir="/p",
    )
    assert e.embed_url == ""
    assert e.repo_edit_url == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_models_phase.py::test_buildtask_has_embed_url_fields tests/unit/test_models_phase.py::test_embed_manifest_entry_has_embed_url -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the fields**

In [backend/models.py:93-115](../../../backend/models.py#L93-L115), inside `class BuildTask(BaseModel)`, add these fields after `github_error: str = ""`:

```python
    # Vanilla viz monorepo publish (vanilla-viz-stage-1) — populated by
    # publish_viz_to_monorepo. embed_url is the GitHub Pages URL the SPA
    # shows; repo_edit_url is the GitHub UI URL for the viz subdir.
    embed_url: str = ""
    repo_edit_url: str = ""
    monorepo_name: str = ""
```

In [backend/models.py:149-161](../../../backend/models.py#L149-L161), inside `class EmbedManifestEntry`, add:

```python
    embed_url: str = ""                # https://<owner>.github.io/<monorepo>/<slug>/
    repo_edit_url: str = ""            # https://github.com/<owner>/<monorepo>/tree/main/<slug>
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_models_phase.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py tests/unit/test_models_phase.py
git commit -m "feat(models): add embed_url/repo_edit_url/monorepo_name to BuildTask + manifest"
```

---

### Task 4: Drop `VIZ_BUILD_FIX` from `backend/llm/tasks.py`

**Files:**
- Modify: `backend/llm/tasks.py:26-54`
- Test: `tests/unit/llm/test_tasks.py` (create if missing, or extend if present)

Check first:

```bash
ls tests/unit/llm/
```

If `test_tasks.py` doesn't exist, create it.

- [ ] **Step 1: Write the failing test**

Create or extend `tests/unit/llm/test_tasks.py`:

```python
"""Locks the LLMTask enum membership after the vanilla viz rewrite."""
from backend.llm import LLMTask
from backend.llm.tasks import resolve_model


def test_viz_build_fix_is_removed():
    names = {t.name for t in LLMTask}
    assert "VIZ_BUILD_FIX" not in names


def test_remaining_viz_tasks_present():
    names = {t.name for t in LLMTask}
    for required in (
        "AGENT_A_EXTRACT", "AGENT_B_SUGGEST",
        "VIZ_TOPIC_CLASSIFY", "VIZ_DRAFT", "VIZ_RUNTIME_FIX", "VIZ_POLISH",
    ):
        assert required in names, f"{required} missing"


def test_resolve_model_for_viz_draft_defaults_to_gpt5(monkeypatch):
    monkeypatch.delenv("MODEL_VIZ_DRAFT", raising=False)
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/llm/test_tasks.py -v`
Expected: FAIL on `test_viz_build_fix_is_removed`.

- [ ] **Step 3: Remove `VIZ_BUILD_FIX`**

In [backend/llm/tasks.py](../../../backend/llm/tasks.py), delete the `VIZ_BUILD_FIX` enum member (line 31), the `_DEFAULTS` entry (line 41), and the `_ENV_VAR` entry (line 51). The trailing comments referencing it can be removed too.

After edit, the relevant blocks should read:

```python
class LLMTask(str, Enum):
    AGENT_A_EXTRACT = "agent_a_extract"
    AGENT_B_SUGGEST = "agent_b_suggest"
    VIZ_TOPIC_CLASSIFY = "viz_topic_classify"
    VIZ_DRAFT = "viz_draft"
    VIZ_RUNTIME_FIX = "viz_runtime_fix"
    VIZ_POLISH = "viz_polish"


_DEFAULTS: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "gpt-4o-mini",
    LLMTask.AGENT_B_SUGGEST:    "gpt-4o-mini",
    LLMTask.VIZ_TOPIC_CLASSIFY: "gpt-4o-mini",
    LLMTask.VIZ_DRAFT:          "gpt-5",
    LLMTask.VIZ_RUNTIME_FIX:    "gpt-5",
    LLMTask.VIZ_POLISH:         "gpt-5",
}

_ENV_VAR: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "MODEL_AGENT_A",
    LLMTask.AGENT_B_SUGGEST:    "MODEL_AGENT_B",
    LLMTask.VIZ_TOPIC_CLASSIFY: "MODEL_VIZ_CLASSIFY",
    LLMTask.VIZ_DRAFT:          "MODEL_VIZ_DRAFT",
    LLMTask.VIZ_RUNTIME_FIX:    "MODEL_VIZ_RUNTIME",
    LLMTask.VIZ_POLISH:         "MODEL_VIZ_POLISH",
}
```

Also update the module docstring at the top of [backend/llm/tasks.py:1-18](../../../backend/llm/tasks.py#L1-L18) — drop the `VIZ_BUILD_FIX → MODEL_VIZ_FIX` line.

- [ ] **Step 4: Confirm no remaining references**

Run: `rg -n "VIZ_BUILD_FIX" backend tests`
Expected: empty.

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/llm/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/llm/tasks.py tests/unit/llm/test_tasks.py
git commit -m "refactor(llm): drop VIZ_BUILD_FIX task — vanilla pipeline has no build step"
```

---

## Phase 2 — Build the new viz-generator modules

We build the new modules first and unit-test them. The old files (npm.py, postprocess.py, etc.) remain in place but the new modules don't import them. We swap the orchestrator over in Phase 3 and delete the old files in Phase 7.

### Task 5: New `backend/viz_generator/validator.py` — failing tests

**Files:**
- Create: `tests/unit/viz_generator/test_validator.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/viz_generator/test_validator.py`:

```python
"""Unit tests for the single-launch Playwright validator.

These tests require Playwright to be installed with the chromium browser
(the project already depends on `playwright>=1.40` and the Dockerfile runs
`playwright install chromium --with-deps`). They run in CI's slow lane.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from backend.viz_generator.validator import validate, ValidationResult


pytestmark = pytest.mark.slow


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "viz"
    d.mkdir()
    return d


def test_validator_returns_success_for_valid_html(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>t</title></head>
    <body><div>hi</div></body></html>"""
    result = validate(html, project_dir)
    assert isinstance(result, ValidationResult)
    assert result.success is True
    assert result.error_log == ""
    assert result.screenshot_path == str(project_dir / "screenshot.png")
    assert (project_dir / "screenshot.png").exists()
    assert (project_dir / "screenshot.png").stat().st_size > 0


def test_validator_captures_pageerror(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><div>boot</div>
    <script>throw new Error("boom-runtime-error")</script>
    </body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "boom-runtime-error" in result.error_log
    # No screenshot on failure
    assert result.screenshot_path == ""


def test_validator_captures_console_error(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><div>boot</div>
    <script>console.error("logged-error-marker")</script>
    </body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "logged-error-marker" in result.error_log


def test_validator_rejects_empty_body(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body></body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "empty" in result.error_log.lower() or "blank" in result.error_log.lower()


def test_validator_writes_html_to_disk_before_launch(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><p>x</p></body></html>"""
    validate(html, project_dir)
    written = (project_dir / "index.html").read_text(encoding="utf-8")
    assert written == html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/viz_generator/test_validator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.viz_generator.validator'`.

- [ ] **Step 3: Commit the failing tests**

```bash
git add tests/unit/viz_generator/test_validator.py
git commit -m "test(viz): failing tests for new single-launch validator"
```

---

### Task 6: New `backend/viz_generator/validator.py` — implementation

**Files:**
- Create: `backend/viz_generator/validator.py`

- [ ] **Step 1: Write the module**

Create `backend/viz_generator/validator.py`:

```python
"""Single-launch Playwright validator + screenshot for a vanilla HTML viz.

The fix-loop policy (one iteration max) lives in `phases/draft.py`; this
module exposes a pure check. On success we write screenshot.png alongside
index.html in project_dir. On failure we capture pageerror + console.error
text into error_log and return without taking a screenshot.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("viz_agent")

PAGE_GOTO_TIMEOUT_MS: int = 10_000
NETWORKIDLE_TIMEOUT_MS: int = 10_000
VIEWPORT = {"width": 1280, "height": 800}


@dataclass(frozen=True)
class ValidationResult:
    success: bool
    error_log: str = ""
    screenshot_path: str = ""


def validate(html: str, project_dir: Path) -> ValidationResult:
    """Load `html` in Chromium, capture errors, screenshot on success.

    Writes html to `project_dir/index.html` first (Playwright needs a file
    URL). On success, also writes `project_dir/screenshot.png` at 1280x800.
    """
    project_dir.mkdir(parents=True, exist_ok=True)
    html_path = project_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    screenshot_path = project_dir / "screenshot.png"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()

            page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
            page.on(
                "console",
                lambda msg: errors.append(f"console.error: {msg.text}")
                if msg.type == "error" else None,
            )

            try:
                page.goto(
                    f"file://{html_path.resolve()}",
                    wait_until="networkidle",
                    timeout=PAGE_GOTO_TIMEOUT_MS,
                )
            except Exception as exc:
                errors.append(f"navigation: {exc}")

            # Semantic assertion: <body> must have at least one rendered child
            # with a non-null bounding box. Catches blank pages where the LLM
            # produced no body content or where init JS crashed before render.
            try:
                box = page.locator("body *").first.bounding_box(timeout=2_000)
                if box is None:
                    errors.append("empty body: no visible elements")
            except Exception as exc:
                errors.append(f"empty body / locator error: {exc}")

            if errors:
                return ValidationResult(success=False, error_log="\n".join(errors))

            page.screenshot(path=str(screenshot_path), full_page=False)
        finally:
            browser.close()

    return ValidationResult(
        success=True,
        error_log="",
        screenshot_path=str(screenshot_path),
    )
```

- [ ] **Step 2: Run tests**

Run: `pytest tests/unit/viz_generator/test_validator.py -v --no-header`
Expected: PASS (4 tests). If Playwright is not installed in the worktree, install it:
- `pip install playwright`
- `playwright install chromium`

- [ ] **Step 3: Commit**

```bash
git add backend/viz_generator/validator.py
git commit -m "feat(viz): add single-launch Playwright validator + screenshot"
```

---

### Task 7: Vanilla `UNIVERSAL_SYSTEM_PROMPT` in `prompts.py`

**Files:**
- Modify (full rewrite): `backend/viz_generator/prompts.py`
- Test: `tests/unit/viz_generator/test_prompts.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/viz_generator/test_prompts.py`:

```python
"""Locks the vanilla viz prompt's hard constraints. The Stage-2 React/Tailwind
prompt should never be reintroduced; the regression guard below catches that.
"""
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT, POLISH_RUBRIC


def test_universal_prompt_is_nonempty_and_under_target_length():
    p = UNIVERSAL_SYSTEM_PROMPT
    assert p.strip()
    # Target: ~80 lines, well under the previous 313-line React prompt.
    assert p.count("\n") < 150, f"prompt grew to {p.count(chr(10))} lines"


def test_universal_prompt_contains_hard_constraints():
    p = UNIVERSAL_SYSTEM_PROMPT.lower()
    # Must state the single-file rule and the file name.
    assert "single file" in p or "one file" in p
    assert "index.html" in p
    # Must ban external URLs (no CDN scripts/stylesheets).
    assert "no external" in p or "no cdn" in p or "fully offline" in p


def test_universal_prompt_has_no_react_or_tailwind_keywords():
    # Regression guard: these would mean the React prompt got restored.
    p = UNIVERSAL_SYSTEM_PROMPT.lower()
    for banned in ("react", "tailwind", "vite", "framer-motion",
                   "zustand", "tsx", "createroot"):
        assert banned not in p, f"banned keyword present: {banned!r}"


def test_polish_rubric_is_nonempty():
    assert POLISH_RUBRIC.strip()
    # Must talk about *visual* refinement, not algorithm logic.
    low = POLISH_RUBRIC.lower()
    assert "typography" in low or "spacing" in low or "motion" in low
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/viz_generator/test_prompts.py -v`
Expected: FAIL — current prompt is 322 lines of React/Tailwind text; `POLISH_RUBRIC` doesn't exist yet.

- [ ] **Step 3: Rewrite the module**

Overwrite [backend/viz_generator/prompts.py](../../../backend/viz_generator/prompts.py) with:

```python
"""LLM prompts for the vanilla HTML+CSS+JS viz generator.

UNIVERSAL_SYSTEM_PROMPT — used by both draft and fix LLM calls.
POLISH_RUBRIC — the design-refinement directives used by the polish phase.
"""
from __future__ import annotations

UNIVERSAL_SYSTEM_PROMPT = """You are a senior front-end developer building \
interactive educational visualizations for a single-screen embed.

OUTPUT FORMAT (hard requirements — any deviation is a failure)
- Output ONLY the HTML document. No prose before or after. No code fences.
- Exactly ONE file named index.html. All CSS lives in a <style> tag inside <head>.
  All JavaScript lives in a <script> tag at the end of <body>.
- NO external resources: no <script src="https://...">, no <link rel="stylesheet" \
href="https://...">, no <img src="https://...">, no @import url(...), no fonts \
from Google/CDN. The page must work fully offline from a file:// URL.
- Required head elements: <!doctype html>, <html lang="en">, \
<meta charset="UTF-8">, <meta name="viewport" content="width=device-width, \
initial-scale=1.0">, <title>.
- The visualization must render meaningful content in <body> without any user \
interaction (the validator takes a screenshot on first load).
- Do not throw uncaught exceptions or log to console.error on init — the \
validator treats both as failure.

DESIGN LANGUAGE
- Define CSS custom properties on :root for the palette, type scale, spacing \
scale, and motion timings. Reuse them throughout the stylesheet.
- Layout with CSS Grid and Flexbox. Avoid fixed pixel widths where the viz \
should scale to the container; prefer min/max with clamp().
- Animate with CSS keyframes, transitions, and Web Animations API. Use \
setInterval / requestAnimationFrame only when CSS isn't expressive enough.
- Use inline SVG for icons and diagrams. Do not pull in icon fonts or sets.
- No animation library, no UI framework, no charting library. If you need a \
chart, draw it with SVG or canvas.
- Aim for a dark, monochrome-with-accent aesthetic by default unless the \
topic suggests otherwise. Sensible defaults: bg #0a0e1a, surface #111827, \
text #e2e8f0, accent #6366f1, highlight #f59e0b, success #10b981.

INTERACTIVITY
- Where the topic has a step-by-step nature (algorithms, traversals, \
training loops), pre-compute the steps and expose play / pause / step / \
reset controls. Show the current step and a one-line explanation.
- Where the topic is a static structure (a tree, a diagram, a formula), \
animate the build-up on load and leave the user with the final state.
- Keep DOM size modest (< ~500 nodes). Performance budget: smooth at 60fps \
on a 2020 laptop.

ACCESSIBILITY
- All interactive elements are real <button> / <input> with visible labels \
or aria-label. Color contrast ratio >= 4.5:1 for body text.

The output of this conversation is a single HTML document that obeys every \
rule above."""


POLISH_RUBRIC = """Polish the visual design of the working visualization.
Improve typography (clear hierarchy, comfortable line-height, monospace \
for numeric values), spacing (consistent rhythm based on a 4 or 8 px scale), \
motion smoothness (150-250ms transitions on color and size; ease-out), and \
contrast (text/background AA, accent/background AA).

DO NOT change algorithm logic, step generation, data structures, or any \
behavior. Only adjust CSS, inline styles, SVG colors/sizes, layout wrappers, \
and transition timing. The page must continue to render meaningful content \
on first paint without user interaction."""
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/viz_generator/test_prompts.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/prompts.py tests/unit/viz_generator/test_prompts.py
git commit -m "feat(viz): rewrite prompts.py as vanilla HTML/CSS/JS design language"
```

---

### Task 8: Slim `files.py` — `write_html_to_disk` + `extract_html`

The old `files.py` validates filepaths, pins package.json deps, handles a multi-file dict. We replace it with two small helpers:
- `extract_html(raw: str) -> str` — strip optional ```html fence and trim.
- `write_html_to_disk(project_dir, html)` — write `index.html`, refuse empty.
- Keep `print_error_block` (the build/runtime loops will be deleted but the new draft phase still uses it for nice error rendering).

**Files:**
- Modify (full rewrite): `backend/viz_generator/files.py`
- Modify: `tests/unit/viz_generator/test_files.py` (replace old tests)

- [ ] **Step 1: Write the new failing tests**

Overwrite `tests/unit/viz_generator/test_files.py`:

```python
"""Unit tests for the slim vanilla-viz file helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.viz_generator.files import (
    extract_html,
    write_html_to_disk,
    print_error_block,
)


# ── extract_html ─────────────────────────────────────────────────────────

def test_extract_html_passes_raw_doctype_through():
    raw = "<!doctype html><html lang=\"en\"><body>x</body></html>"
    assert extract_html(raw) == raw


def test_extract_html_strips_html_codefence():
    raw = "```html\n<!doctype html><html><body>x</body></html>\n```"
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_strips_unlabeled_codefence():
    raw = "```\n<!doctype html><html><body>x</body></html>\n```"
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_strips_leading_and_trailing_whitespace():
    raw = "   \n<!doctype html><html><body>x</body></html>\n   "
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_raises_when_no_html_found():
    with pytest.raises(ValueError, match="no html"):
        extract_html("sorry, I cannot help with that")


# ── write_html_to_disk ───────────────────────────────────────────────────

def test_write_html_to_disk_writes_index_html(tmp_path: Path):
    html = "<!doctype html><html><body>x</body></html>"
    write_html_to_disk(tmp_path, html)
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html


def test_write_html_to_disk_creates_project_dir(tmp_path: Path):
    target = tmp_path / "new-sub"
    write_html_to_disk(target, "<!doctype html><html><body>x</body></html>")
    assert (target / "index.html").exists()


def test_write_html_to_disk_rejects_empty(tmp_path: Path):
    with pytest.raises(ValueError, match="empty"):
        write_html_to_disk(tmp_path, "")
    with pytest.raises(ValueError, match="empty"):
        write_html_to_disk(tmp_path, "   \n\n   ")


# ── print_error_block ────────────────────────────────────────────────────

def test_print_error_block_runs_without_raising(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="viz_agent")
    print_error_block("Demo error", "line one\nline two\nline three")
    # Should have emitted at least one log record with the label
    assert any("Demo error" in rec.message for rec in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/viz_generator/test_files.py -v`
Expected: FAIL with `ImportError` on `extract_html` / `write_html_to_disk` (the new helpers don't exist; old helpers do).

- [ ] **Step 3: Rewrite the module**

Overwrite [backend/viz_generator/files.py](../../../backend/viz_generator/files.py):

```python
"""On-disk file helpers for the vanilla viz generator.

write_html_to_disk writes the single index.html for a viz.
extract_html strips optional ```html``` code fences from the LLM response
and returns the bare HTML string.
print_error_block is the small log formatter used by the draft/polish
phases to render error output legibly.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("viz_agent")

ERROR_DISPLAY_MAX_LINES: int = 40

_HTML_FENCE_RE = re.compile(
    r"^\s*```(?:html|HTML)?\s*\n(?P<body>.*?)\n```\s*$",
    re.DOTALL,
)

# Heuristic for "this looks like HTML at all" — used by extract_html so we
# fail loudly when the LLM apologizes or returns Markdown instead of HTML.
_LOOKS_HTML_RE = re.compile(r"<(?:!doctype|html|body)\b", re.IGNORECASE)


def extract_html(raw: str) -> str:
    """Return the HTML body of the LLM's response.

    Strips a single surrounding ```html ... ``` (or plain ``` ... ```) fence
    if present, then trims whitespace. Raises ValueError if the result does
    not look like an HTML document.
    """
    s = raw.strip()
    m = _HTML_FENCE_RE.match(s)
    if m:
        s = m.group("body").strip()
    if not _LOOKS_HTML_RE.search(s):
        raise ValueError("no html detected in LLM output")
    return s


def write_html_to_disk(project_dir: Path, html: str) -> None:
    """Write `html` as `index.html` under `project_dir`. Refuses empty input."""
    if not html or not html.strip():
        raise ValueError("refusing to write empty html")
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "index.html").write_text(html, encoding="utf-8")


def print_error_block(
    label: str,
    text: str,
    max_lines: int = ERROR_DISPLAY_MAX_LINES,
) -> None:
    """Render an error/output block to the viz_agent logger."""
    text = (text or "").strip()
    if not text:
        log.info("  %s: (no output captured)", label)
        return
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    shown = lines[-max_lines:] if truncated else lines
    log.info("\n  ━━━ %s ━━━", label)
    if truncated:
        log.info("  (showing last %d of %d lines)", max_lines, len(lines))
    for line in shown:
        log.info("  | %s", line)
    log.info("  ━━━ end %s ━━━\n", label)
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/viz_generator/test_files.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/files.py tests/unit/viz_generator/test_files.py
git commit -m "refactor(viz): slim files.py to extract_html + write_html_to_disk for vanilla pipeline"
```

---

### Task 9: Rewrite `phases/draft.py` — single LLM call + one fix-loop iteration

The new draft phase: one LLM call → validator → on failure, one fix call → validator again → return result. The fix-loop policy (one iteration max) lives here.

**Files:**
- Modify (full rewrite): `backend/viz_generator/phases/draft.py`
- Test: `tests/unit/viz_generator/phases/test_draft.py` (new — create the dir)

- [ ] **Step 1: Create test directory**

```bash
mkdir -p tests/unit/viz_generator/phases
touch tests/unit/viz_generator/phases/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/viz_generator/phases/test_draft.py`:

```python
"""Unit tests for phases.draft.run_draft_phase.

The LLM and validator are mocked; we verify orchestration: how many calls
fire, how the fix prompt is built, and what gets returned on success vs.
two-failure terminal state.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.viz_generator.phases.draft import run_draft_phase, DraftResult
from backend.viz_generator.validator import ValidationResult


VALID_HTML = "<!doctype html><html><body>x</body></html>"
VALID_HTML_FIX = "<!doctype html><html><body>fixed</body></html>"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "viz"


def test_draft_succeeds_on_first_attempt(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ) as mock_draft, patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        return_value=ValidationResult(success=True, screenshot_path=str(project_dir / "screenshot.png")),
    ) as mock_validate:
        result = run_draft_phase("binary search", "brief", project_dir)

    assert isinstance(result, DraftResult)
    assert result.success is True
    assert result.html == VALID_HTML
    assert result.attempts == 1
    mock_draft.assert_called_once()
    mock_fix.assert_not_called()
    mock_validate.assert_called_once()


def test_draft_runs_one_fix_iteration_and_succeeds(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ), patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
        return_value=VALID_HTML_FIX,
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        side_effect=[
            ValidationResult(success=False, error_log="boom"),
            ValidationResult(success=True, screenshot_path="x"),
        ],
    ) as mock_validate:
        result = run_draft_phase("topic", "brief", project_dir)

    assert result.success is True
    assert result.html == VALID_HTML_FIX
    assert result.attempts == 2
    assert mock_fix.call_count == 1
    assert mock_validate.call_count == 2
    # Fix call must include the failure log so the model can act on it.
    fix_kwargs = mock_fix.call_args
    assert "boom" in str(fix_kwargs)


def test_draft_returns_failure_after_one_fix_iteration_max(project_dir: Path):
    """If fix attempt also fails, we stop — no third LLM call."""
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ), patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
        return_value=VALID_HTML_FIX,
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        side_effect=[
            ValidationResult(success=False, error_log="first"),
            ValidationResult(success=False, error_log="second"),
        ],
    ) as mock_validate:
        result = run_draft_phase("topic", "brief", project_dir)

    assert result.success is False
    assert result.attempts == 2
    assert mock_fix.call_count == 1
    assert mock_validate.call_count == 2
    assert "second" in result.error_log
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/viz_generator/phases/test_draft.py -v`
Expected: FAIL — `_llm_call_draft` / `_llm_call_fix` don't exist in the new shape.

- [ ] **Step 4: Rewrite the module**

Overwrite [backend/viz_generator/phases/draft.py](../../../backend/viz_generator/phases/draft.py):

```python
"""Draft phase: one LLM call → validate → optional one-shot fix.

Public surface: run_draft_phase(topic, brief, project_dir) -> DraftResult.
Fix-loop policy (one iteration max) lives here, NOT in validator.py.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.llm import LLMTask
from backend.viz_generator.files import extract_html, print_error_block
from backend.viz_generator.llm import llm_call
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT
from backend.viz_generator.validator import ValidationResult, validate

log = logging.getLogger("viz_agent")

# Per-call output budget. The whole vanilla viz typically fits well under
# this; truncation at this limit means the LLM tried to over-generate and
# we should fail loudly rather than ship a half-file.
MODEL_VIZ_DRAFT_MAX_TOKENS: int = 16_000
MODEL_VIZ_FIX_MAX_TOKENS: int = 8_000


@dataclass(frozen=True)
class DraftResult:
    success: bool
    html: str
    attempts: int
    error_log: str = ""
    screenshot_path: str = ""


def _llm_call_draft(topic: str, brief: str) -> str:
    user_prompt = f"""Generate a self-contained vanilla HTML visualization for:

TOPIC: "{topic}"

BRIEF:
{brief}

Output the complete HTML document only. No prose. No code fences."""
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
    return extract_html(raw)


def _llm_call_fix(topic: str, broken_html: str, error_log: str) -> str:
    user_prompt = f"""The previous visualization for "{topic}" failed validation. \
Fix the issues below and return the complete corrected HTML document.

ERRORS:
{error_log}

CURRENT HTML:
{broken_html}

Output the complete fixed HTML document only. Obey every constraint in the \
system prompt. No prose. No code fences."""
    raw = llm_call(
        [
            {"role": "system", "content": UNIVERSAL_SYSTEM_PROMPT},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=1,
        max_tokens=MODEL_VIZ_FIX_MAX_TOKENS,
        step_label="draft_fix",
        task=LLMTask.VIZ_RUNTIME_FIX,
    )
    return extract_html(raw)


def run_draft_phase(topic: str, brief: str, project_dir: Path) -> DraftResult:
    """Generate + validate the initial viz. One fix-loop iteration on failure."""
    log.info("[draft] generating initial HTML for '%s'...", topic)
    html = _llm_call_draft(topic, brief)

    log.info("[validate] first attempt...")
    result = validate(html, project_dir)
    if result.success:
        log.info("  ✅ draft validated on first attempt.")
        return DraftResult(
            success=True, html=html, attempts=1,
            screenshot_path=result.screenshot_path,
        )

    print_error_block("Draft validation errors", result.error_log)
    log.info("[draft] running one fix iteration...")
    html_fixed = _llm_call_fix(topic, html, result.error_log)

    log.info("[validate] post-fix attempt...")
    result2 = validate(html_fixed, project_dir)
    if result2.success:
        log.info("  ✅ draft validated after one fix iteration.")
        return DraftResult(
            success=True, html=html_fixed, attempts=2,
            screenshot_path=result2.screenshot_path,
        )

    log.info("  ❌ draft failed after one fix iteration.")
    print_error_block("Post-fix validation errors", result2.error_log)
    return DraftResult(
        success=False, html=html_fixed, attempts=2,
        error_log=result2.error_log,
    )
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/unit/viz_generator/phases/test_draft.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/viz_generator/phases/draft.py tests/unit/viz_generator/phases/__init__.py tests/unit/viz_generator/phases/test_draft.py
git commit -m "feat(viz): rewrite draft phase — single LLM call + one fix iteration max"
```

---

### Task 10: Rewrite `phases/polish.py` — operate on HTML; re-validate; fall back on regress

**Files:**
- Modify (full rewrite): `backend/viz_generator/phases/polish.py`
- Test: `tests/unit/viz_generator/phases/test_polish.py` (new)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/viz_generator/phases/test_polish.py`:

```python
"""Unit tests for phases.polish.run_polish_phase.

LLM + validator mocked. Verifies the fallback-on-regress contract: if
polish breaks the viz, we ship the pre-polish HTML, not the broken one.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.viz_generator.phases.polish import run_polish_phase, PolishResult
from backend.viz_generator.validator import ValidationResult


PRE_HTML = "<!doctype html><html><body>pre</body></html>"
POST_HTML = "<!doctype html><html><body>polished</body></html>"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "viz"


def test_polish_succeeds_and_returns_polished_html(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.polish._llm_call_polish",
        return_value=POST_HTML,
    ), patch(
        "backend.viz_generator.phases.polish.validate",
        return_value=ValidationResult(success=True, screenshot_path="x"),
    ) as mock_validate:
        result = run_polish_phase("topic", PRE_HTML, project_dir)

    assert isinstance(result, PolishResult)
    assert result.html == POST_HTML
    assert result.polished is True
    assert result.fallback_used is False
    mock_validate.assert_called_once()


def test_polish_falls_back_to_pre_polish_when_validation_regresses(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.polish._llm_call_polish",
        return_value=POST_HTML,
    ), patch(
        "backend.viz_generator.phases.polish.validate",
        return_value=ValidationResult(success=False, error_log="polish broke it"),
    ):
        result = run_polish_phase("topic", PRE_HTML, project_dir)

    assert result.html == PRE_HTML
    assert result.polished is False
    assert result.fallback_used is True
    assert "polish broke it" in result.error_log
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/viz_generator/phases/test_polish.py -v`
Expected: FAIL — `run_polish_phase` / `PolishResult` don't exist in the new shape.

- [ ] **Step 3: Rewrite the module**

Overwrite [backend/viz_generator/phases/polish.py](../../../backend/viz_generator/phases/polish.py):

```python
"""Polish phase: one LLM call refines the working HTML's visual design.

After polish we re-validate once. If polish broke the viz, we fall back to
the pre-polish HTML (we'd rather ship an unpolished working viz than fail).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from backend.llm import LLMTask
from backend.viz_generator.files import extract_html, write_html_to_disk
from backend.viz_generator.llm import llm_call
from backend.viz_generator.prompts import POLISH_RUBRIC, UNIVERSAL_SYSTEM_PROMPT
from backend.viz_generator.validator import validate

log = logging.getLogger("viz_agent")

MODEL_VIZ_POLISH_MAX_TOKENS: int = 16_000


@dataclass(frozen=True)
class PolishResult:
    html: str            # final HTML actually written to disk
    polished: bool       # True if the polished HTML validated cleanly
    fallback_used: bool  # True if we reverted to pre_html after a regress
    error_log: str = ""


def _llm_call_polish(topic: str, pre_html: str) -> str:
    user_prompt = f"""{POLISH_RUBRIC}

TOPIC: "{topic}"

CURRENT HTML (working — do not break it):
{pre_html}

Output the complete polished HTML document only. No prose. No code fences."""
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
    return extract_html(raw)


def run_polish_phase(topic: str, pre_html: str, project_dir: Path) -> PolishResult:
    """Refine pre_html visually, re-validate, fall back to pre_html on regress."""
    log.info("[polish] refining design for '%s'...", topic)
    polished_html = _llm_call_polish(topic, pre_html)

    log.info("[polish] re-validating polished HTML...")
    result = validate(polished_html, project_dir)
    if result.success:
        log.info("  ✅ polish applied + validated.")
        return PolishResult(html=polished_html, polished=True, fallback_used=False)

    log.info("  ⚠️  polish regressed validation — reverting to pre-polish HTML.")
    # Rewrite pre_html to disk so screenshot.png stays in sync with what we ship.
    write_html_to_disk(project_dir, pre_html)
    # Re-screenshot the working HTML so disk reflects the final shipped state.
    validate(pre_html, project_dir)
    return PolishResult(
        html=pre_html, polished=False, fallback_used=True,
        error_log=result.error_log,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/viz_generator/phases/test_polish.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/phases/polish.py tests/unit/viz_generator/phases/test_polish.py
git commit -m "feat(viz): rewrite polish phase — single LLM call with fallback on validation regress"
```

---

## Phase 3 — Rewrite cli.py to drive the new pipeline

### Task 11: Rewrite `backend/viz_generator/cli.py`

The argv contract (`--topic`, `--polish`) is fixed by `tests/contract/test_viz_cli.py`. We keep both flags. The internal pipeline is now: classify → draft → polish (if `--polish`).

We also keep the slug logic that produces `<topic>-viz` as the project directory name — `backend/orchestrator.py` finds the new project dir via a diff snapshot, so the exact slug isn't a hard contract, but we keep it for compatibility with the snapshot heuristic in [orchestrator.py:262-288](../../../backend/orchestrator.py#L262-L288).

**Files:**
- Modify (full rewrite): `backend/viz_generator/cli.py`

- [ ] **Step 1: Confirm the contract tests still apply**

Run: `pytest tests/contract/test_viz_cli.py -v`
Expected: PASS — these tests should still pass since we haven't touched `cli.py` yet, but they currently pass against the OLD cli (which still imports from soon-to-be-deleted files). After this task, they should still pass.

- [ ] **Step 2: Rewrite the module**

Overwrite [backend/viz_generator/cli.py](../../../backend/viz_generator/cli.py):

```python
"""Vanilla viz generator subprocess entry point.

The argv contract (--topic [required], --polish [flag]) is locked by
tests/contract/test_viz_cli.py and must not change without updating the
orchestrator + contract tests in lockstep.

Pipeline (vanilla-viz-stage-1):
  1. Classify topic                          → printed as [STATUS] STEP 1
  2. Draft + validate (with 1-shot fix loop) → printed as [STATUS] STEP 2
  3. Polish + re-validate (if --polish)      → printed as [STATUS] STEP 3
  4. (Publish happens in backend, not here.)
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from backend.viz_generator.files import write_html_to_disk
from backend.viz_generator.llm import (
    LLM_PROVIDER,
    MODEL_NAME,
    TOKEN_BUDGET,
    _init_client,
    status,
    token_tracker,
)
from backend.viz_generator.phases.draft import run_draft_phase
from backend.viz_generator.phases.polish import run_polish_phase
from backend.viz_generator.topic import classify_topic

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viz_agent")


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser (exposed for contract tests)."""
    p = argparse.ArgumentParser(
        description="Universal Visualization Builder (vanilla) — single-file HTML viz",
    )
    p.add_argument(
        "--topic", required=True,
        help="Topic to visualize (e.g. 'gradient descent', 'AVL tree', 'TCP handshake')",
    )
    p.add_argument(
        "--polish", action="store_true",
        help="Run design polish pass after validation",
    )
    return p


def _slug_for_topic(topic: str) -> str:
    """`<topic>-viz` — same shape as the previous CLI for snapshot-diff compat."""
    return re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-") + "-viz"


def main() -> None:
    args = build_parser().parse_args()

    _init_client()

    log.info("=" * 60)
    log.info("  Vanilla Viz Agent — '%s'", args.topic)
    log.info("=" * 60)

    status("STEP 1", "CLASSIFY TOPIC")
    pattern_name, _pattern = classify_topic(args.topic)

    slug = _slug_for_topic(args.topic)
    project_dir = Path.cwd() / slug

    log.info(
        "[Config] Token budget: %d  Model: %s  Provider: %s",
        TOKEN_BUDGET, MODEL_NAME, LLM_PROVIDER,
    )

    status("STEP 2", "DRAFT + VALIDATE")
    draft = run_draft_phase(args.topic, args.topic, project_dir)
    if not draft.success:
        log.error("[FATAL] draft failed after one fix iteration. See errors above.")
        token_tracker.print_summary()
        sys.exit(4)

    # Ensure the working HTML is on disk in case downstream picks the file
    # before polish runs.
    write_html_to_disk(project_dir, draft.html)

    if args.polish:
        status("STEP 3", "POLISH")
        polish = run_polish_phase(args.topic, draft.html, project_dir)
        final_html = polish.html
        if polish.fallback_used:
            log.info("  ⚠️  polish reverted — shipping pre-polish HTML.")
    else:
        log.info("[polish] skipped (--polish not set)")
        final_html = draft.html

    # write_html_to_disk + validator already wrote the final files, but write
    # again for clarity; idempotent.
    write_html_to_disk(project_dir, final_html)

    log.info("\n" + "=" * 60)
    log.info("  DONE!")
    log.info("  Topic:   %s", args.topic)
    log.info("  Pattern: %s", pattern_name)
    log.info("  Project: %s", project_dir)
    log.info("  HTML:    %s", project_dir / "index.html")
    log.info("  PNG:     %s", project_dir / "screenshot.png")
    log.info("=" * 60)

    token_tracker.print_summary()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the contract tests**

Run: `pytest tests/contract/test_viz_cli.py -v`
Expected: PASS (5 tests). The argv shape is preserved.

- [ ] **Step 4: Run all viz_generator unit tests together**

Run: `pytest tests/unit/viz_generator -v`
Expected: PASS for the new modules. **Some old test files** (`test_llm.py`, `test_postprocess.py`, `test_select.py`, `test_parsing.py`) will still pass because the old modules still exist — we delete them in Phase 7.

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/cli.py
git commit -m "feat(viz): rewrite cli to drive vanilla pipeline (classify -> draft -> polish)"
```

---

## Phase 4 — Update backend orchestrator + build_orchestrator

### Task 12: Update `backend/orchestrator.py` phase detection regexes

The orchestrator parses the subprocess's stdout lines to detect phase transitions. New CLI prints `[STATUS] STEP 1 ... STEP 3` plus `[draft]`, `[validate]`, `[polish]`, and `DONE!`.

**Files:**
- Modify: `backend/orchestrator.py:48-67` (the `_PHASE_PATTERNS` table + `detect_phase_from_line` use)
- Modify: `backend/orchestrator.py:290-296` (the success-detection conditional that mentions phase names)
- Modify: `backend/orchestrator.py:191` (pass `--polish` so polish always runs)

There are no existing unit tests for `orchestrator.py` (only `tests/unit/services/test_build_orchestrator.py` for the orchestration service). We add one.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_orchestrator_phase.py`:

```python
"""Locks the vanilla-viz phase detection in backend.orchestrator."""
import pytest

from backend.orchestrator import detect_phase_from_line


@pytest.mark.parametrize("line,phase", [
    ("──── [STATUS] STEP 1 — CLASSIFY TOPIC ────", "draft"),
    ("──── [STATUS] STEP 2 — DRAFT + VALIDATE ────", "draft"),
    ("[draft] generating initial HTML for 'x'...", "draft"),
    ("[validate] first attempt...", "validate"),
    ("──── [STATUS] STEP 3 — POLISH ────", "polish"),
    ("[polish] refining design for 'x'...", "polish"),
    ("  DONE!", "done"),
])
def test_detect_phase_from_line_recognises_vanilla_markers(line, phase):
    assert detect_phase_from_line(line) == phase


def test_detect_phase_from_line_returns_none_for_unrelated_lines():
    assert detect_phase_from_line("random log noise") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_orchestrator_phase.py -v`
Expected: FAIL — current patterns return `step1_generate` / `step2_build` etc.

- [ ] **Step 3: Update the patterns + success conditional**

In [backend/orchestrator.py:48-60](../../../backend/orchestrator.py#L48-L60), replace `_PHASE_PATTERNS` with:

```python
_PHASE_PATTERNS: list[tuple[str, str]] = [
    (r"\[STATUS\]\s*STEP\s*1",         "draft"),    # classify is part of draft phase from SPA POV
    (r"\[STATUS\]\s*STEP\s*2",         "draft"),
    (r"\[draft\]",                      "draft"),
    (r"\[validate\]",                   "validate"),
    (r"\[STATUS\]\s*STEP\s*3",         "polish"),
    (r"\[polish\]",                     "polish"),
    (r"\bDONE!",                        "done"),
]
```

In [backend/orchestrator.py:290-296](../../../backend/orchestrator.py#L290-L296), update the success conditional:

```python
    if (
        proc.returncode == 0
        and result.project_dir
        and last_phase in ("done", "polish", "validate")
    ):
        result.success = True
```

In [backend/orchestrator.py:191](../../../backend/orchestrator.py#L191), pass `--polish` so polish always runs as part of the standard pipeline:

```python
    cmd = [sys.executable, str(FIXED_MAIN_PATH), "--topic", topic_brief, "--polish"]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/test_orchestrator_phase.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator.py tests/unit/test_orchestrator_phase.py
git commit -m "feat(orchestrator): detect vanilla viz phases; pass --polish by default"
```

---

### Task 13: Update `backend/services/build_orchestrator.py` — drop postprocess, swap publisher

The build_orchestrator currently:
- Imports `_inject_error_boundary` and `_patch_vite_config_base` from `backend.viz_generator.postprocess` (to be deleted).
- Imports `publish_viz_repo` from `backend.github_publisher` (to be replaced with `publish_viz_to_monorepo` in Task 14).
- Sets `task.phase = "completed"` / `"failed"` (now `"done"` / `"failed"`).
- Reads `task.short_topic` for slug.

We update the imports, phase strings, and publisher call site in this task. The publisher itself is rewritten in Phase 5.

**Files:**
- Modify: `backend/services/build_orchestrator.py:18-24` (imports)
- Modify: `backend/services/build_orchestrator.py:84-112` (drop postprocess hooks; phase strings)
- Modify: `backend/services/build_orchestrator.py:114-148` (publisher call site)
- Modify: `backend/services/build_orchestrator.py:146-152` (success aggregation: `"completed"` → `"done"`)
- Modify: `tests/unit/services/test_build_orchestrator.py` (update phase string assertions + publisher mock target)

- [ ] **Step 1: Read the existing test to plan updates**

Run: `pytest tests/unit/services/test_build_orchestrator.py -v --tb=no -q` to see current test names.

- [ ] **Step 2: Update imports + delete postprocess + phase strings**

In [backend/services/build_orchestrator.py:18-24](../../../backend/services/build_orchestrator.py#L18-L24), replace the imports block:

```python
from backend.config import settings
from backend.services.manifest_builder import build_manifest
from backend.github_publisher import publish_viz_to_monorepo
from backend.models import JobStatus
from backend.orchestrator import run_viz_build
from backend.store import job_store
```

In [backend/services/build_orchestrator.py:84-112](../../../backend/services/build_orchestrator.py#L84-L112), delete the entire postprocess block (lines 90-112 in the original) and update the phase string. The body should become:

```python
    task.completed_at = result.completed_at or datetime.utcnow()
    task.project_dir = result.project_dir
    task.screenshot_path = result.screenshot_path
    task.error = result.error or ""
    task.phase = "done" if result.success else "failed"
```

In [backend/services/build_orchestrator.py:114-148](../../../backend/services/build_orchestrator.py#L114-L148), replace the publish block:

```python
    # ── Publish the viz to the monorepo (one subdir per viz) ──
    if result.success and result.project_dir and settings.publish_to_github:
        if not settings.github_token:
            task.github_status = "skipped"
            task.github_error = "GITHUB_TOKEN not set"
            on_log("[GitHub] skipped — GITHUB_TOKEN not set")
        elif not settings.viz_monorepo_name:
            task.github_status = "skipped"
            task.github_error = "VIZ_MONOREPO_NAME not set"
            on_log("[GitHub] skipped — VIZ_MONOREPO_NAME not set")
        else:
            _status("PUBLISH", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
            task.phase = "publish"  # type: ignore[assignment]
            task.github_status = "publishing"
            try:
                slug = task.short_topic or Path(result.project_dir).name
                pub = publish_viz_to_monorepo(
                    project_dir=result.project_dir,
                    slug=slug,
                    description=(task.final_viz_brief or slug)[:300],
                    private=settings.github_repos_private,
                    on_log=on_log,
                )
                task.github_status = "published"
                task.github_repo_url = pub.html_url
                task.github_clone_url = pub.clone_url
                task.github_repo_name = pub.repo_name
                task.github_commit_sha = pub.commit_sha
                task.embed_url = pub.embed_url
                task.repo_edit_url = pub.repo_edit_url
                task.monorepo_name = pub.repo_name
                task.phase = "done"  # type: ignore[assignment]
                logger.info("[Build %s] Published to %s",
                            topic_id, pub.embed_url)
            except Exception as exc:                # noqa: BLE001 — publish must never crash the build
                logger.exception("[Build %s] GitHub publish failed: %s", topic_id, exc)
                task.github_status = "failed"
                task.github_error = str(exc)[:500]
                task.phase = "done"  # type: ignore[assignment]
                on_log(f"[GitHub] FAILED — {exc}")
```

In [backend/services/build_orchestrator.py:146-152](../../../backend/services/build_orchestrator.py#L146-L152), update the aggregator:

```python
    all_builds_finished = all(
        b.phase in ("done", "failed") for b in job.builds.values()
    )
    if all_builds_finished:
        any_failed = any(b.phase == "failed" for b in job.builds.values())
        new_status = JobStatus.DONE if not any_failed else JobStatus.FAILED
```

- [ ] **Step 3: Update test fixture + assertions**

In `tests/unit/services/test_build_orchestrator.py`, update:

- Module-level constants: change `_PATCH_PUBLISH = f"{_MOD}.publish_viz_repo"` to `f"{_MOD}.publish_viz_to_monorepo"`.
- Any `task.phase == "completed"` assertions → `"done"`.
- Any patch of `_patch_vite_config_base` / `_inject_error_boundary` → remove (those imports are gone).
- Add `monkeypatch.setattr(settings, "viz_monorepo_name", "monorepo")` (or equivalent SimpleNamespace stub) in tests that exercise the publish branch.
- For the success test, ensure the mock `PublishResult` has the new `embed_url` and `repo_edit_url` fields:

```python
fake_pub = SimpleNamespace(
    repo_name="monorepo",
    owner="user",
    html_url="https://github.com/user/monorepo",
    clone_url="https://github.com/user/monorepo.git",
    commit_sha="abc",
    file_count=2,
    embed_url="https://user.github.io/monorepo/slug/",
    repo_edit_url="https://github.com/user/monorepo/tree/main/slug",
)
```

- [ ] **Step 4: Run service tests**

Run: `pytest tests/unit/services/test_build_orchestrator.py -v`
Expected: PASS for all branches (success → publish, build failure → no publish, publish disabled, publish raises).

- [ ] **Step 5: Commit**

```bash
git add backend/services/build_orchestrator.py tests/unit/services/test_build_orchestrator.py
git commit -m "feat(orchestrator): swap to monorepo publisher; drop postprocess hooks"
```

---

### Task 14: Update `backend/services/manifest_builder.py` — populate `embed_url`

**Files:**
- Modify: `backend/services/manifest_builder.py`
- Modify: `tests/unit/services/test_manifest_builder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/services/test_manifest_builder.py`:

```python
def test_manifest_includes_embed_url_when_published():
    from backend.services.manifest_builder import build_manifest
    from backend.models import JobState, ExtractedTopic, BuildTask

    job = JobState(
        job_id="j1", script_name="s",
        topics=[ExtractedTopic(
            id="t1", section="## S", topic="T",
            embed_after_sentence="x.", why_visual_helps="y",
            surrounding_context="",
        )],
        builds={"t1": BuildTask(
            id="b1", topic_id="t1", phase="done",
            final_viz_brief="b",
            github_status="published",
            github_repo_url="https://github.com/u/m",
            embed_url="https://u.github.io/m/t/",
            repo_edit_url="https://github.com/u/m/tree/main/t",
        )},
    )
    entries = build_manifest(job)
    assert len(entries) == 1
    e = entries[0]
    assert e.embed_url == "https://u.github.io/m/t/"
    assert e.repo_edit_url == "https://github.com/u/m/tree/main/t"
    assert e.status == "ok"


def test_manifest_status_done_maps_to_ok():
    """build_manifest must accept the new 'done' phase, not the old 'completed'."""
    from backend.services.manifest_builder import build_manifest
    from backend.models import JobState, ExtractedTopic, BuildTask

    job = JobState(
        job_id="j1", script_name="s",
        topics=[ExtractedTopic(
            id="t1", section="## S", topic="T",
            embed_after_sentence="x.", why_visual_helps="y",
            surrounding_context="",
        )],
        builds={"t1": BuildTask(id="b1", topic_id="t1", phase="done")},
    )
    entries = build_manifest(job)
    assert entries[0].status == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/services/test_manifest_builder.py -v -k "embed_url or done_maps"`
Expected: FAIL (`embed_url` not populated; old code checks `task.phase == "completed"`).

- [ ] **Step 3: Update `build_manifest`**

In [backend/services/manifest_builder.py:33-44](../../../backend/services/manifest_builder.py#L33-L44), update the entry construction:

```python
        entries.append(EmbedManifestEntry(
            section=topic.section,
            embed_after_sentence=topic.embed_after_sentence,
            topic=topic.topic,
            why_visual_helps=topic.why_visual_helps,
            viz_title=viz_title,
            viz_brief=task.final_viz_brief,
            project_dir=task.project_dir,
            screenshot_path=task.screenshot_path,
            github_repo_url=task.github_repo_url if task.github_status == "published" else "",
            embed_url=task.embed_url if task.github_status == "published" else "",
            repo_edit_url=task.repo_edit_url if task.github_status == "published" else "",
            status="ok" if task.phase == "done" else "failed",
        ))
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/unit/services/test_manifest_builder.py -v`
Expected: PASS for the new tests AND any existing ones that expected `"completed"` → update them to `"done"` (they should already be passing if the original used "completed" string — verify and fix any breakage).

- [ ] **Step 5: Commit**

```bash
git add backend/services/manifest_builder.py tests/unit/services/test_manifest_builder.py
git commit -m "feat(manifest): include embed_url/repo_edit_url; map new 'done' phase to ok"
```

---

## Phase 5 — Rewrite `github_publisher.py` for monorepo + Pages

### Task 15: Failing tests for `publish_viz_to_monorepo`

There are currently no direct unit tests for `github_publisher.py` (only via `test_build_orchestrator.py`). We add a new test file using `unittest.mock.patch` on the `requests` module — matches the existing house mocking style (no new dep). The spec mentions the `responses` library; we deviate to keep test deps unchanged.

**Files:**
- Create: `tests/unit/github/__init__.py`
- Create: `tests/unit/github/test_publisher.py`

- [ ] **Step 1: Create test dir**

```bash
mkdir -p tests/unit/github
touch tests/unit/github/__init__.py
```

- [ ] **Step 2: Write failing tests**

Create `tests/unit/github/test_publisher.py`:

```python
"""Unit tests for the monorepo + GitHub Pages publisher.

We mock requests at the module level (matches the rest of the suite) and
verify the high-level flow: monorepo creation, Pages enablement, slug
collision suffixing, two-file commit, embed_url shape, retry on stale ref.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest


def _fake_response(status_code: int, json_body: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    r.text = text or ""
    return r


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "viz"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><html><body>x</body></html>", encoding="utf-8")
    (d / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00fake")
    return d


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    """Force the publisher to read a predictable github token + owner."""
    from backend.config import settings
    monkeypatch.setattr(settings, "github_token", "ghp_fake", raising=False)
    monkeypatch.setattr(settings, "github_owner", "tester", raising=False)
    monkeypatch.setattr(settings, "viz_monorepo_name", "monorepo", raising=False)
    yield


def test_creates_monorepo_when_missing(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    side_effects_get = [
        _fake_response(200, {"type": "User", "login": "tester"}),         # owner lookup
        _fake_response(404),                                              # repo exists?
        _fake_response(404),                                              # pages enabled?
        _fake_response(404),                                              # subdir exists?
        _fake_response(200, {"object": {"sha": "parent"}}),              # ref
        _fake_response(200, {"sha": "tree-base"}),                       # base tree
    ]
    side_effects_post = [
        _fake_response(201, {"default_branch": "main", "name": "monorepo"}),   # create repo
        _fake_response(201),                                                    # enable pages
        _fake_response(201, {"sha": "blob1"}),                                  # blob index.html
        _fake_response(201, {"sha": "blob2"}),                                  # blob screenshot
        _fake_response(201, {"sha": "tree-new"}),                               # new tree
        _fake_response(201, {"sha": "commit-1"}),                               # commit
    ]
    side_effects_patch = [
        _fake_response(200),  # update ref
    ]

    with patch("backend.github_publisher.requests.get", side_effect=side_effects_get) as mget, \
         patch("backend.github_publisher.requests.post", side_effect=side_effects_post) as mpost, \
         patch("backend.github_publisher.requests.patch", side_effect=side_effects_patch) as mpatch:
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir),
            slug="binary-search",
            description="d",
        )

    assert result.embed_url == "https://tester.github.io/monorepo/binary-search/"
    assert result.repo_edit_url == "https://github.com/tester/monorepo/tree/main/binary-search"
    assert result.commit_sha == "commit-1"
    assert result.file_count == 2


def test_skips_repo_create_when_monorepo_exists(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {"default_branch": "main"}),                  # repo exists
        _fake_response(200, {"source": {"branch": "main"}}),              # pages enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls) as mget, \
         patch("backend.github_publisher.requests.post", side_effect=post_calls) as mpost, \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )

    posted_urls = [c.args[0] for c in mpost.call_args_list]
    # No POST to /user/repos or /orgs/.../repos
    assert not any(u.endswith("/user/repos") or "/repos" == u[-6:] for u in posted_urls)
    assert result.embed_url == "https://tester.github.io/monorepo/bs/"


def test_treats_422_pages_already_exists_as_success(project_dir: Path):
    """When Pages enable returns 422 (already on), continue without raising."""
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(404),                                              # pages NOT enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(422, text="Pages already exists"),                 # enable -> race
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.commit_sha == "commit-1"


def test_subdir_collision_suffixes_with_2(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(200, {}),                                          # pages enabled
        _fake_response(200, {}),                                          # subdir "bs" exists
        _fake_response(404),                                              # "bs-2" vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.embed_url.endswith("/bs-2/")


def test_retries_on_stale_parent_sha(project_dir: Path):
    """Concurrent push race: ref update returns 422; we refetch ref + retry."""
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(200, {}),                                          # pages enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent-A"}}),            # ref (attempt 1)
        _fake_response(200, {"sha": "tree-base-A"}),
        _fake_response(200, {"object": {"sha": "parent-B"}}),            # ref refresh after 422
        _fake_response(200, {"sha": "tree-base-B"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new-A"}),
        _fake_response(201, {"sha": "commit-A"}),
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new-B"}),
        _fake_response(201, {"sha": "commit-B"}),
    ]
    patch_calls = [
        _fake_response(422, text="ref not at expected SHA"),
        _fake_response(200),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", side_effect=patch_calls):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.commit_sha == "commit-B"


def test_raises_when_monorepo_name_not_configured(project_dir: Path, monkeypatch):
    from backend.config import settings
    from backend.github_publisher import publish_viz_to_monorepo
    monkeypatch.setattr(settings, "viz_monorepo_name", "", raising=False)
    with pytest.raises(RuntimeError, match="VIZ_MONOREPO_NAME"):
        publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/github/test_publisher.py -v`
Expected: FAIL with `ImportError` on `publish_viz_to_monorepo`.

- [ ] **Step 4: Commit failing tests**

```bash
git add tests/unit/github/__init__.py tests/unit/github/test_publisher.py
git commit -m "test(github): failing tests for monorepo + Pages publisher"
```

---

### Task 16: Rewrite `backend/github_publisher.py` to implement `publish_viz_to_monorepo`

**Files:**
- Modify (full rewrite): `backend/github_publisher.py`

We replace the per-viz-repo publisher with a monorepo+subdir publisher. The old `publish_viz_repo` and `PublishResult` are renamed; the old per-repo helpers (`sanitize_repo_name`, `_pick_unique_repo_name`, `_iter_publishable_files`) are removed.

- [ ] **Step 1: Rewrite the module**

Overwrite [backend/github_publisher.py](../../../backend/github_publisher.py):

```python
"""GitHub publisher — push each viz as a subdirectory in a single monorepo.

One monorepo (settings.viz_monorepo_name) holds every viz the user has ever
generated; each viz lives at <slug>/{index.html,screenshot.png}. The
monorepo has GitHub Pages enabled, so each viz is served at
https://<owner>.github.io/<monorepo>/<slug>/.

Design notes:
  • GITHUB_TOKEN is read lazily inside `_h()`, never at import time.
  • Concurrent pushes race on the ref update — handled by retrying with
    a refreshed parent SHA (up to MAX_REF_RETRIES).
  • Pages enable: 404 → enable; 422 "already exists" → treat as success
    (idempotent for the race where another worker enabled it first).
  • Monorepo creation uses auto_init=True so refs/heads/main exists
    before we attempt to create blobs against it.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from backend.config import settings

logger = logging.getLogger("hackmd-orch.github")

GITHUB_API = "https://api.github.com"

# Max ref-update retries on stale parent SHA. 3 is generous given that
# per-job builds are sequential and the only racers are concurrent users.
MAX_REF_RETRIES: int = 3

LogFn = Optional[Callable[[str], None]]


# ── small helpers ─────────────────────────────────────────────────────────────

def _log(on_log: LogFn, msg: str) -> None:
    logger.info(msg)
    if on_log:
        try:
            on_log(msg)
        except Exception:  # noqa: BLE001
            pass


def _h() -> dict[str, str]:
    token = settings.github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN env var not set — cannot publish to GitHub")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _err(resp: requests.Response, action: str) -> RuntimeError:
    body = resp.text[:400] if resp.text else "<empty>"
    return RuntimeError(f"GitHub {action} failed: HTTP {resp.status_code} {body}")


def sanitize_subdir_name(s: str) -> str:
    """URL-clean subdir name: lower, alnum + dash only."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "viz"
    return s[:90]


# ── owner resolution ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Owner:
    name: str
    is_org: bool


def _resolve_owner(on_log: LogFn) -> Owner:
    override = settings.github_owner
    if override:
        r = requests.get(f"{GITHUB_API}/users/{override}", headers=_h(), timeout=10)
        if r.status_code != 200:
            raise _err(r, f"lookup owner {override!r}")
        is_org = r.json().get("type", "User") == "Organization"
        _log(on_log, f"[GitHub] owner={override} ({'org' if is_org else 'user'})")
        return Owner(name=override, is_org=is_org)

    r = requests.get(f"{GITHUB_API}/user", headers=_h(), timeout=10)
    if r.status_code == 401:
        raise RuntimeError("GitHub auth failed (401) — check GITHUB_TOKEN scope")
    if r.status_code != 200:
        raise _err(r, "lookup authenticated user")
    login = r.json().get("login")
    if not login:
        raise RuntimeError("GitHub /user returned no login field")
    return Owner(name=login, is_org=False)


# ── monorepo + pages ──────────────────────────────────────────────────────────

def _ensure_monorepo_exists(
    owner: Owner, name: str, description: str, private: bool, on_log: LogFn,
) -> None:
    r = requests.get(f"{GITHUB_API}/repos/{owner.name}/{name}", headers=_h(), timeout=10)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise _err(r, f"check repo {owner.name}/{name}")

    create_url = (
        f"{GITHUB_API}/orgs/{owner.name}/repos" if owner.is_org
        else f"{GITHUB_API}/user/repos"
    )
    r = requests.post(
        create_url, headers=_h(), timeout=20,
        json={
            "name": name, "private": private,
            "auto_init": True,
            "description": description[:350] or f"Visualizations: {name}",
            "has_issues": True, "has_wiki": False,
        },
    )
    if r.status_code not in (200, 201):
        raise _err(r, f"create monorepo {owner.name}/{name}")
    _log(on_log, f"[GitHub] created monorepo {owner.name}/{name}")


def _ensure_pages_enabled(owner: Owner, name: str, on_log: LogFn) -> None:
    r = requests.get(f"{GITHUB_API}/repos/{owner.name}/{name}/pages", headers=_h(), timeout=10)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise _err(r, f"check pages {owner.name}/{name}")
    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{name}/pages",
        headers=_h(), timeout=20,
        json={"source": {"branch": "main", "path": "/"}},
    )
    if r.status_code == 422:
        # Race: another worker enabled Pages between our GET and POST.
        _log(on_log, f"[GitHub] pages already enabled on {owner.name}/{name}")
        return
    if r.status_code not in (200, 201):
        raise _err(r, f"enable pages {owner.name}/{name}")
    _log(on_log, f"[GitHub] enabled Pages on {owner.name}/{name}")


def _subdir_exists(owner: Owner, name: str, subdir: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{owner.name}/{name}/contents/{subdir}",
        headers=_h(), timeout=10,
    )
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    raise _err(r, f"check subdir {subdir}")


def _pick_unique_subdir(owner: Owner, name: str, base: str, on_log: LogFn) -> str:
    if not _subdir_exists(owner, name, base):
        return base
    for i in range(2, 50):
        candidate = f"{base}-{i}"
        _log(on_log, f"[GitHub] {base} taken, trying {candidate}")
        if not _subdir_exists(owner, name, candidate):
            return candidate
    raise RuntimeError(f"Could not find an available subdir starting from {base!r}")


# ── atomic commit (with retry on stale ref) ──────────────────────────────────

def _create_blob(owner: Owner, repo: str, data: bytes) -> str:
    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/blobs",
        headers=_h(), timeout=60,
        json={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
    )
    if r.status_code not in (200, 201):
        raise _err(r, "create blob")
    return r.json()["sha"]


def _push_subdir_commit(
    owner: Owner, repo: str, subdir: str,
    html_bytes: bytes, png_bytes: bytes,
    commit_message: str, on_log: LogFn,
) -> str:
    """blobs → ref → base tree → new tree → commit → ref update.

    Retries the whole sequence on stale-ref 422 up to MAX_REF_RETRIES times.
    Blobs are re-created each retry — that's slower but correct (the tree
    references blob SHAs that exist in the repo regardless of ref state).
    """
    last_exc: RuntimeError | None = None
    for attempt in range(1, MAX_REF_RETRIES + 1):
        try:
            blob_html = _create_blob(owner, repo, html_bytes)
            blob_png = _create_blob(owner, repo, png_bytes)

            r = requests.get(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs/heads/main",
                headers=_h(), timeout=15,
            )
            if r.status_code != 200:
                raise _err(r, "get refs/heads/main")
            parent_sha = r.json()["object"]["sha"]

            r = requests.get(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/trees/{parent_sha}",
                headers=_h(), timeout=15,
            )
            if r.status_code != 200:
                raise _err(r, "get base tree")
            base_tree_sha = r.json()["sha"]

            r = requests.post(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/trees",
                headers=_h(), timeout=30,
                json={
                    "base_tree": base_tree_sha,
                    "tree": [
                        {"path": f"{subdir}/index.html", "mode": "100644", "type": "blob", "sha": blob_html},
                        {"path": f"{subdir}/screenshot.png", "mode": "100644", "type": "blob", "sha": blob_png},
                    ],
                },
            )
            if r.status_code not in (200, 201):
                raise _err(r, "create tree")
            tree_sha = r.json()["sha"]

            r = requests.post(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/commits",
                headers=_h(), timeout=30,
                json={"message": commit_message, "tree": tree_sha, "parents": [parent_sha]},
            )
            if r.status_code not in (200, 201):
                raise _err(r, "create commit")
            commit_sha = r.json()["sha"]

            r = requests.patch(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs/heads/main",
                headers=_h(), timeout=30,
                json={"sha": commit_sha, "force": False},
            )
            if r.status_code == 422:
                _log(on_log, f"[GitHub] ref update raced (attempt {attempt}) — retrying")
                last_exc = _err(r, "fast-forward refs/heads/main")
                continue
            if r.status_code not in (200, 201):
                raise _err(r, "fast-forward refs/heads/main")

            _log(on_log, f"[GitHub] committed {subdir}/ @ {commit_sha[:7]}")
            return commit_sha
        except RuntimeError as e:
            last_exc = e
            # Only retry on the ref-update path; other failures bail immediately.
            if "fast-forward refs/heads/main" not in str(e):
                raise

    assert last_exc is not None
    raise last_exc


# ── public API ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublishResult:
    repo_name: str           # the monorepo name (used by build_orchestrator for back-compat)
    owner: str
    html_url: str            # https://github.com/<owner>/<monorepo>
    clone_url: str           # https://github.com/<owner>/<monorepo>.git
    commit_sha: str
    file_count: int          # always 2 for vanilla (index.html + screenshot.png)
    embed_url: str           # https://<owner>.github.io/<monorepo>/<slug>/
    repo_edit_url: str       # https://github.com/<owner>/<monorepo>/tree/main/<slug>


def publish_viz_to_monorepo(
    project_dir: str,
    slug: str,
    description: str = "",
    private: bool = False,
    on_log: LogFn = None,
) -> PublishResult:
    """Push project_dir/{index.html,screenshot.png} into the monorepo as <slug>/."""
    monorepo = settings.viz_monorepo_name
    if not monorepo:
        raise RuntimeError(
            "VIZ_MONOREPO_NAME not set — cannot publish without a monorepo name"
        )

    root = Path(project_dir).resolve()
    html_path = root / "index.html"
    png_path = root / "screenshot.png"
    if not html_path.is_file():
        raise RuntimeError(f"index.html missing in {root}")
    if not png_path.is_file():
        raise RuntimeError(f"screenshot.png missing in {root}")

    owner = _resolve_owner(on_log)
    _ensure_monorepo_exists(owner, monorepo, description, private, on_log)
    _ensure_pages_enabled(owner, monorepo, on_log)

    base_subdir = sanitize_subdir_name(slug)
    subdir = _pick_unique_subdir(owner, monorepo, base_subdir, on_log)

    commit_sha = _push_subdir_commit(
        owner=owner, repo=monorepo, subdir=subdir,
        html_bytes=html_path.read_bytes(),
        png_bytes=png_path.read_bytes(),
        commit_message=f"Add viz: {subdir}",
        on_log=on_log,
    )

    html_url = f"https://github.com/{owner.name}/{monorepo}"
    return PublishResult(
        repo_name=monorepo,
        owner=owner.name,
        html_url=html_url,
        clone_url=f"{html_url}.git",
        commit_sha=commit_sha,
        file_count=2,
        embed_url=f"https://{owner.name}.github.io/{monorepo}/{subdir}/",
        repo_edit_url=f"{html_url}/tree/main/{subdir}",
    )
```

- [ ] **Step 2: Run publisher tests**

Run: `pytest tests/unit/github/test_publisher.py -v`
Expected: PASS (6 tests).

- [ ] **Step 3: Run build_orchestrator tests again to verify import chain**

Run: `pytest tests/unit/services/test_build_orchestrator.py -v`
Expected: PASS — the rewrite from Task 13 imports `publish_viz_to_monorepo` which now exists.

- [ ] **Step 4: Commit**

```bash
git add backend/github_publisher.py
git commit -m "feat(github): rewrite publisher for monorepo + Pages (publish_viz_to_monorepo)"
```

---

## Phase 6 — Frontend BuildCard phase labels

### Task 17: Update `frontend/src/components/BuildCard.tsx`

**Files:**
- Modify: `frontend/src/components/BuildCard.tsx:7-25` (PHASE_LABELS + PHASE_ORDER)

- [ ] **Step 1: Update the constants**

In [frontend/src/components/BuildCard.tsx:7-25](../../../frontend/src/components/BuildCard.tsx#L7-L25), replace:

```tsx
const PHASE_LABELS: Record<string, string> = {
  queued:    'Queued',
  draft:     'Generating draft',
  validate:  'Validating',
  polish:    'Polishing design',
  publish:   'Publishing to GitHub',
  done:      'Done',
  failed:    'Failed',
};

const PHASE_ORDER = [
  'queued',
  'draft',
  'validate',
  'polish',
  'publish',
  'done',
];
```

- [ ] **Step 2: Type-check + build the frontend**

Run: `cd frontend && npm run build`
Expected: clean tsc + Vite build.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/BuildCard.tsx
git commit -m "feat(spa): update BuildCard phase labels for vanilla viz pipeline"
```

---

## Phase 7 — Delete the old files + their tests; verify

### Task 18: Delete the dropped backend modules

**Files (delete):**
- `backend/viz_generator/npm.py`
- `backend/viz_generator/postprocess.py`
- `backend/viz_generator/select.py`
- `backend/viz_generator/llm.py` — **wait**: this is the LLM client used by the new draft/polish too (`from backend.viz_generator.llm import llm_call`). Re-read the spec:

> "DELETED: npm.py, postprocess.py, select.py, llm.py (multi-provider client; consolidate by routing all viz-generator LLM calls through backend/llm/), screenshot.py"

The spec wants `llm.py` consolidated into `backend/llm/`. That is a larger refactor (multi-provider Gemini path, token tracker, status helper, etc.). To keep this PR focused on the vanilla-viz rewrite, we **DEFER** deleting `backend/viz_generator/llm.py`. Note this explicitly in the commit and add a follow-up task in the spec's "related future work".

- [ ] **Step 1: Delete files**

```bash
git rm backend/viz_generator/npm.py \
       backend/viz_generator/postprocess.py \
       backend/viz_generator/select.py \
       backend/viz_generator/screenshot.py \
       backend/viz_generator/phases/build_loop.py \
       backend/viz_generator/phases/runtime_loop.py
```

- [ ] **Step 2: Delete corresponding tests**

```bash
git rm tests/unit/viz_generator/test_llm.py \
       tests/unit/viz_generator/test_postprocess.py \
       tests/unit/viz_generator/test_select.py
```

`tests/unit/viz_generator/test_parsing.py` still tests `backend.viz_generator.parsing` which the spec says is KEPT (it's no longer called by the new pipeline but is preserved for any future multi-file viz). Leave it in place.

`tests/unit/viz_generator/test_llm_task_routing.py` tests `backend.llm.tasks.resolve_model` — keep.

- [ ] **Step 3: Run the import-linter**

Run: `lint-imports`
Expected: PASS. The contracts in `.importlinter` don't change, but this catches any dangling references in the deleted modules' former importers.

- [ ] **Step 4: Run the full unit test suite**

Run: `pytest tests/unit -q`
Expected: PASS. Any failure here means a deleted symbol is still referenced — fix and re-run.

- [ ] **Step 5: Run the full contract test suite**

Run: `pytest tests/contract -q`
Expected: PASS. The contract tests for `cli.py` should still pass (Task 11 preserved the argv shape).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore(viz): remove React/Vite pipeline modules (npm/postprocess/select/screenshot/build_loop/runtime_loop)"
```

---

### Task 19: Search for any lingering references to deleted symbols

- [ ] **Step 1: Grep for deleted module names**

Run:
```bash
rg -n "viz_generator\.(npm|postprocess|select|screenshot)|viz_generator\.phases\.(build_loop|runtime_loop)|publish_viz_repo|_inject_error_boundary|_patch_vite_config_base|enforce_pinned_deps|_run_npm_build" backend tests frontend 2>/dev/null
```
Expected: empty.

- [ ] **Step 2: If any references remain, fix them and commit**

If grep returns lines, open each file and remove the reference. Re-run the suite (`pytest -q`) after each fix.

```bash
git commit -m "chore(viz): clean up lingering references to deleted pipeline modules"
```

(skip the commit if no fixes were needed)

---

## Phase 8 — Dockerfile + end-to-end + prompt iteration

### Task 20: Drop NodeSource from the runtime Docker stage

**Files:**
- Modify: `Dockerfile:18-23` (the NodeSource install block in stage 3)

The frontend-builder stage (stage 1) keeps Node 20 — that's where we build the SPA. The runtime stage no longer needs Node since the vanilla pipeline doesn't run `npm install` / `npm build`.

- [ ] **Step 1: Update the Dockerfile**

In [Dockerfile:18-23](../../../Dockerfile#L18-L23), remove the NodeSource install. Replace the block with a minimal `apt-get update` purely for Playwright's `--with-deps`. The full stage-3 prefix should read:

```dockerfile
# ── Stage 3: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Install only what Playwright needs to fetch chromium deps. Node is gone:
# the vanilla viz pipeline emits one self-contained HTML file and does not
# run `npm install` or `npm build`.
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

WORKDIR /app
```

The rest of the file is unchanged.

- [ ] **Step 2: Build the image**

Run: `docker build -t universal-viz:vanilla-stage-1 .`
Expected: build succeeds. Note the runtime image size with `docker images universal-viz:vanilla-stage-1 --format '{{.Size}}'` — expect roughly 500 MB (down from ~900 MB).

- [ ] **Step 3: Smoke-test the container**

Run:
```bash
docker run --rm -p 8001:8001 \
  -e OPENAI_API_KEY="${OPENAI_API_KEY}" \
  -e GITHUB_TOKEN="${GITHUB_TOKEN:-}" \
  -e VIZ_MONOREPO_NAME=test-monorepo \
  universal-viz:vanilla-stage-1 &
sleep 5 && curl -fsSL http://localhost:8001/health
```
Expected: `{"ok": true, ...}`.

Then `kill %1` to stop it.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile
git commit -m "build: drop Node 20 from runtime stage; vanilla pipeline ships chromium-only image"
```

---

### Task 21: Minimal end-to-end smoke test

**Files:**
- Create: `tests/e2e/test_vanilla_pipeline.py`
- Create: `tests/e2e/__init__.py` (if missing)
- Modify: `pyproject.toml` to register the `slow` marker (if not already there)

The spec calls for a slow-marked e2e that exercises the real OpenAI path. Gate it behind `pytest -m slow` so CI doesn't pay the cost on every run.

- [ ] **Step 1: Register the marker**

Check `pyproject.toml` / `pytest.ini` / `conftest.py` for a `[tool.pytest.ini_options]` block with `markers`. If absent, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "slow: end-to-end tests that hit real external services (OpenAI, Playwright)",
]
```

- [ ] **Step 2: Write the e2e test**

Create `tests/e2e/__init__.py` (empty) and `tests/e2e/test_vanilla_pipeline.py`:

```python
"""End-to-end smoke for the vanilla viz CLI.

Hits real OpenAI; gated behind `pytest -m slow`. Run locally with
  pytest -m slow tests/e2e/test_vanilla_pipeline.py
Requires OPENAI_API_KEY in the environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def test_cli_produces_index_and_screenshot(tmp_path: Path):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    r = subprocess.run(
        [sys.executable, "-m", "backend.viz_generator.cli",
         "--topic", "binary search", "--polish"],
        cwd=str(tmp_path),
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout}\n---stderr:\n{r.stderr}"

    # The CLI writes <slug>-viz/{index.html,screenshot.png} inside cwd.
    projects = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.endswith("-viz")]
    assert len(projects) == 1, f"expected one viz dir, got: {projects}"
    viz = projects[0]
    assert (viz / "index.html").stat().st_size > 0
    assert (viz / "screenshot.png").stat().st_size > 0

    html = (viz / "index.html").read_text(encoding="utf-8")
    assert html.lower().startswith("<!doctype") or html.lower().startswith("<html")
```

- [ ] **Step 3: Run the e2e**

Run: `pytest -m slow tests/e2e/test_vanilla_pipeline.py -v`
Expected: PASS in ~10–60 seconds, depending on OpenAI latency.

If it fails: the failure mode tells you what to fix. Common cases:
- `RuntimeError: viz_monorepo_name not set` — irrelevant for the CLI test; ignore (publisher isn't called here).
- LLM output doesn't look like HTML → tighten `prompts.py` (Task 22).
- Playwright errors → check `playwright install chromium` succeeded.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/__init__.py tests/e2e/test_vanilla_pipeline.py pyproject.toml
git commit -m "test(e2e): vanilla pipeline CLI smoke (real OpenAI, gated by -m slow)"
```

---

### Task 22: Prompt iteration (manual, 5 real briefs)

The spec calls out prompt tuning as part of this same PR. We run the new pipeline against ~5 real lecture briefs, look at the output, and iterate on `prompts.py`.

**Files:**
- Modify: `backend/viz_generator/prompts.py` (iterations)
- (Optional) `backend/viz_generator/phases/draft.py` if the user prompt needs adjusting

- [ ] **Step 1: Pick 5 representative topics**

Choose one topic per `topic.py` pattern: `stepped_algorithm`, `optimization`, `tree_graph`, `neural_network`, `mathematical`. For example:
1. "binary search on a sorted array"
2. "gradient descent on a parabola"
3. "AVL tree rotations"
4. "two-layer perceptron forward pass"
5. "Fourier series approximation of a square wave"

- [ ] **Step 2: Run each, look at the output**

For each topic, run:
```bash
mkdir -p /tmp/viz-eval/$topic && cd /tmp/viz-eval/$topic
python -m backend.viz_generator.cli --topic "<topic>" --polish
open ./*-viz/index.html
```
Inspect: does it render meaningfully? Does it animate? Is the design coherent? Are there console errors?

- [ ] **Step 3: Iterate on prompts**

For each weakness observed (e.g., bland palette, missing controls, blank panels for some topics), tighten `UNIVERSAL_SYSTEM_PROMPT` or `POLISH_RUBRIC`. Re-run the affected topics. Repeat until quality matches or beats the React version's typical output.

Run `pytest tests/unit/viz_generator/test_prompts.py -v` after each prompt change to make sure the regression guards still pass.

- [ ] **Step 4: Commit prompt changes**

Commit each substantive prompt iteration separately for traceable history:

```bash
git add backend/viz_generator/prompts.py
git commit -m "prompt(viz): <one-line description of the iteration>"
```

- [ ] **Step 5: Final sweep**

Run the full suite one last time:
```bash
pytest -q
cd frontend && npm run build && cd ..
lint-imports
```
Expected: all green.

- [ ] **Step 6: Tag**

Once the user has validated viz quality manually:
```bash
git tag vanilla-viz-stage-1
```

(Don't push the tag until the user explicitly OKs the merge.)

---

## Self-Review

After writing the plan, I checked it against the spec:

**Spec coverage:**
- §1 architecture (new file layout) → Phases 2, 5, 7.
- §2.1 draft phase + hard constraints → Tasks 7 (prompts), 9 (draft phase).
- §2.2 validate + screenshot single launch → Tasks 5, 6 (validator.py).
- §2.3 polish phase + fallback on regress → Task 10.
- §2.4 publish phase → Tasks 15, 16.
- §2.5 phase enum rename → Tasks 2, 12, 13, 14, 17.
- §3 github_publisher rewrite (all subsections) → Tasks 15, 16. Concurrency retry covered in Task 15 test + Task 16 `_push_subdir_commit` retry loop.
- §4 migration (file deletes, .importlinter, manifests) → Tasks 18, 19. (`.importlinter` unchanged; spec said no changes needed — verified.)
- §5 testing strategy → Tasks 5/6/7/8/9/10/13/14/15/21. Old test deletes in Task 18.
- §6 risks → addressed in Task 22 (prompt iteration) and explicit retry handling in Task 16.
- §7 implementation phasing — one PR — entire plan is one branch.

**Known deviation from spec:**
- Spec §4.1 lists `backend/viz_generator/llm.py` for deletion. We DEFER it (documented in Task 18). The new draft/polish phases still import `llm_call` and the token tracker from there, and the consolidation into `backend/llm/` is a larger refactor that warrants its own plan (multi-provider Gemini path, etc.). Recommended follow-up: "consolidate viz_generator/llm.py into backend/llm/".
- Spec mentions the `responses` library for HTTP mocking. We used `unittest.mock.patch` on `requests` to match the existing test style and avoid adding a dev dep.
- Spec §5.5 says "no CI changes." Confirmed — no CI files touched.

**Placeholder/ambiguity check:** every step has explicit code, exact file paths, and concrete commands. The polish-on-no-flag default is resolved by Task 12 (orchestrator passes `--polish`).

**Type consistency:** `ValidationResult`, `DraftResult`, `PolishResult`, `PublishResult`, `BuildPhase`, and `Owner` are each defined once and referenced consistently across tests and call sites. The `embed_url`/`repo_edit_url`/`monorepo_name` triple is added to `BuildTask` (Task 3), populated by the publisher (Task 13/16), serialized into the manifest (Task 14), and is consumed by the SPA's existing manifest renderer (no SPA change needed beyond labels).
