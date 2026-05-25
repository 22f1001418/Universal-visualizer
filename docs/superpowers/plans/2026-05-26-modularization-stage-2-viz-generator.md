# Modularization Stage 2 — viz_generator/ Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the 2,541-line `fixed_main_v6.py` monolith into a `backend/viz_generator/` package of single-responsibility modules. Preserve the subprocess argv contract exactly. Wire per-task `LLMTask` routing at the new viz call sites for cost savings. Introduce `backend/config.py` as a single source of truth for the env vars Stage 1 left scattered.

**Architecture:** Strangler-fig step #2 from the modularization spec (`docs/superpowers/specs/2026-05-25-modularization-design.md`). `fixed_main_v6.py` becomes a 2-line stub at the repo root that delegates to `backend.viz_generator.cli.main`. Every other consumer of the subprocess (`orchestrator.py`, the Dockerfile `FIXED_MAIN_PATH`) keeps working unchanged. The LLM machinery inside the viz generator stays multi-provider (OpenAI + Gemini) — that's a deliberate scoping decision documented below.

**Tech Stack:** Python 3.12, OpenAI SDK, google-generativeai (Gemini), pytest. No new dependencies introduced by this stage.

**Spec sections this plan implements:**
- Section 1 — `backend/viz_generator/` package layout (the table in Section 1 maps each spec module to a Stage-2 task)
- Section 1 — `backend/config.py` (was listed in Stage 1's file structure but never implemented; landing here)
- Section 2 — "Code maintainability" sub-bullets (single-responsibility per file, configuration consolidated)
- Section 4 — Stage 2 cutover (stub at repo root, feature-flag scaffolding default-OFF)

**Out of scope for this plan (deferred to a Stage-2b follow-up plan):**
- Performance optimizations from spec Section 2 — npm template cache, deterministic file-selection heuristic, parallel npm-install, dev-server reuse, build concurrency. Each lands as its own small PR with its own feature flag after Stage 2 ships. Why: they require running real builds to validate, which is best done against a stable decomposed codebase rather than on top of a moving target.
- Unifying viz_generator's multi-provider LLM client with `backend/llm` — Stage-1 finding established this divergence; unifying it is a Stage-3 or later concern when the orchestrator may also need multi-provider.

**Scoping decision: multi-provider LLM machinery**

`fixed_main_v6.py` supports both OpenAI and Gemini via `LLM_PROVIDER` env var, with a `messages: list[dict]` call interface that differs from `backend/llm`'s `system_prompt + user_prompt` shape. Stage 1 deduplicated what was safe (pricing table, reasoning detection, error parsing). Stage 2 **does not** force unification:

- The multi-provider LLM machinery moves into `backend/viz_generator/llm.py` as a self-contained module.
- It continues to import the deduplicated helpers from `backend/llm.pricing`, `backend/llm.reasoning`, `backend/llm.errors`.
- It does **not** route through `backend/llm.client`.
- The orchestrator side (`agents.py`, `main.py`) keeps using `backend/llm` (OpenAI-only).
- The two LLM call paths coexist. Unification is a separate future decision when there's a concrete driver (e.g., the orchestrator wants Gemini too).

This preserves a working multi-provider feature without inventing a new abstraction layer.

---

## File structure (Stage 2 end state)

**Created:**
```
backend/
  config.py                              # pydantic-settings — central env vars (start with OpenAI + LLMTask MODEL_* + Stage-2 flags)
  viz_generator/
    __init__.py                          # re-exports cli.main for the stub at repo root
    cli.py                               # main() entry — preserves argv contract
    llm.py                               # local TokenUsageTracker, _init_client, _get_client, llm_call (multi-provider)
    topic.py                             # classify_topic
    parsing.py                           # parse_files + marker/codeblock parsers, format_files_for_prompt, _extract_filename
    files.py                             # write_to_disk, _validate_filepath, enforce_pinned_deps, _filter_bogus_files, print_error_block
    select.py                            # select_relevant_files, format_files_compact, merge_patches
    npm.py                               # _run_npm_install, _run_npm_build, _pick_free_port, _wait_for_server
    screenshot.py                        # _playwright_available, run_semantic_checks, _evaluate_assertion (Playwright-driven)
    postprocess.py                       # _patch_vite_config_base, _inject_error_boundary (moved from main.py)
    phases/
      __init__.py
      draft.py                           # generate_draft_code
      build_loop.py                      # build_error_loop
      runtime_loop.py                    # runtime_fix_loop
      polish.py                          # design_polish_pass
tests/
  unit/
    config/
      __init__.py
      test_settings.py                   # 4-6 tests: env loading, defaults, MODEL_* overrides
    viz_generator/
      __init__.py
      test_parsing.py                    # tests for marker + codeblock parsers
      test_files.py                      # _validate_filepath path-traversal, enforce_pinned_deps
      test_select.py                     # merge_patches, format_files_compact
      test_postprocess.py                # _patch_vite_config_base for .ts and .js, _inject_error_boundary
  contract/
    test_viz_cli.py                      # argparse contract — locks the subprocess argv
```

**Modified:**
- `fixed_main_v6.py` — replaced entirely with a 2-line compatibility stub that calls `backend.viz_generator.cli.main()`
- `main.py` — three small edits:
  - Remove `_patch_vite_config_base` and `_inject_error_boundary` (moved to `viz_generator/postprocess.py`)
  - Change the two call sites to import from the new location
  - Replace scattered `os.getenv(...)` in this file with `from backend.config import settings`
- `agents.py` — unchanged (Stage 1 already wired LLMTask here)
- `orchestrator.py` — verified unchanged; the `FIXED_MAIN_PATH=/app/fixed_main_v6.py` env var still resolves to the stub

**Unchanged:** `backend/llm/*`, `dev_server.py`, `github_publisher.py`, `agents.py`, `models.py`, `store.py`, `index.html`, the Dockerfile, `railway.toml`.

---

## Conventions used in this plan

- "Move function X from `fixed_main_v6.py` to `backend/viz_generator/Y.py`" means: copy the function verbatim into the new file, fix imports at the top of the new file, then delete the function from `fixed_main_v6.py` (or leave a brief comment marker if other code in the file still references it during the migration — final cleanup happens in Task 12 when the file is reduced to a stub).
- For pure code moves, I do **not** paste the full function body in the plan. The plan references line ranges; the implementer reads, copies, validates.
- For new code (`__init__.py`, `cli.py`, `config.py`, tests), the full code is shown.
- Each task ends with running `.venv/bin/pytest tests/ -q` and confirming no regressions. After this stage, the test count grows from 45 to ~70.
- Commit messages use Conventional Commits.

---

## Pre-flight check

Before starting Task 1, verify the workspace:

```bash
cd /Users/pulkitmangal/Universal-visualizer
git checkout main && git pull --ff-only
.venv/bin/pytest tests/ -q
```

Expected: on `main`, 45 passed. The plan branches off main; create a feature branch `feat/modularization-stage-2` for the implementation.

---

## Task 1 — Scaffold `backend/viz_generator/` + subprocess contract test

**Files:**
- Create: `backend/viz_generator/__init__.py`
- Create: `backend/viz_generator/phases/__init__.py`
- Create: `tests/unit/viz_generator/__init__.py`
- Create: `tests/contract/test_viz_cli.py`

- [ ] **Step 1: Create the empty package skeletons**

```
backend/viz_generator/__init__.py            (empty for now; populated in Task 12)
backend/viz_generator/phases/__init__.py     (empty)
tests/unit/viz_generator/__init__.py         (empty)
```

- [ ] **Step 2: Write the subprocess contract test**

Capture the **current** argparse interface of `fixed_main_v6.py` so any change to it during Stage 2 fails fast.

First, find the argparse setup:
```bash
grep -n "ArgumentParser\|add_argument" fixed_main_v6.py | head -30
```

Read the lines around `add_argument` calls to enumerate the exact flags + types.

Create `tests/contract/test_viz_cli.py`:
```python
"""Subprocess argv contract — locks the CLI interface of fixed_main_v6.py
as it exists at the start of Stage 2. Migration into backend.viz_generator.cli
must preserve every flag, dest, type, and default.
"""
from __future__ import annotations

import subprocess
import sys


def test_help_exits_zero_and_mentions_topic():
    """Help screen must mention --topic at minimum."""
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py", "--help"],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, r.stderr
    assert "--topic" in r.stdout


def test_topic_is_required():
    """Invoking with no args should fail (or print usage + nonzero)."""
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py"],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode != 0


def test_known_flags_present_in_help():
    """Every flag the orchestrator relies on must show in --help.

    This list is captured from the current fixed_main_v6.py during Task 1.
    Tighten as Task 1 inspects the actual argparse setup — replace this
    permissive list with the exact flag set the file exposes today.
    """
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py", "--help"],
        capture_output=True, text=True, timeout=20,
    )
    help_text = r.stdout
    assert "--topic" in help_text
    assert "--polish" in help_text
    # NOTE: After reading fixed_main_v6.py's argparse, add asserts for each
    # additional flag (e.g., --brief, --output-dir, --job-id, --short-topic).
    # Lock them here so Task 12's CLI rewrite cannot silently drop a flag.
```

After reading the actual argparse code, tighten the third test to assert every documented flag. The implementer should run the test after each tightening to confirm the assertion holds against current code.

- [ ] **Step 3: Run the contract test**

```bash
.venv/bin/pytest tests/contract/test_viz_cli.py -v
```

Expected: 3 passed against the unmodified `fixed_main_v6.py`. If `test_topic_is_required` is wrong because the file allows no-args invocation, adjust to match observed behavior.

- [ ] **Step 4: Full suite green check**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 48 passed (45 prior + 3 new contract tests).

- [ ] **Step 5: Commit**

```bash
git add backend/viz_generator/ tests/unit/viz_generator/ tests/contract/test_viz_cli.py
git commit -m "test(viz): lock current fixed_main_v6.py CLI contract + scaffold viz_generator/

Stage 2 of modularization. Captures the argparse interface as-is so
the upcoming decomposition into backend.viz_generator cannot silently
break the subprocess argv contract that main.py spawns it with."
```

---

## Task 2 — Move LLM machinery to `backend/viz_generator/llm.py`

**Goal:** Self-contained multi-provider (OpenAI + Gemini) LLM client. Lives entirely inside `viz_generator/` because its `messages: list[dict]` shape and `LLM_PROVIDER` switching diverge from `backend/llm`.

**Files:**
- Create: `backend/viz_generator/llm.py`
- Modify: `fixed_main_v6.py`

Source ranges to move (post-Stage-1 line numbers):
- `LLM_PROVIDER`, `DEFAULT_MODELS`, `MODEL_NAME`, `TOKEN_BUDGET` constants — around lines 110–135
- `_REASONING_MODELS_BLOCKLIST` (if any) + `_is_reasoning_model` wrapper — around lines 166–191
- `class TokenUsageTracker` — around lines 863–933
- `token_tracker` module singleton — around line 933
- `status()` helper — around lines 939–950
- `_init_client()` and `_get_client()` — around lines 952–1042
- `llm_call()` — around lines 1395–1675

- [ ] **Step 1: Identify the exact import block at top of fixed_main_v6.py to preserve**

```bash
sed -n '1,90p' fixed_main_v6.py
```

Note the imports (OpenAI client, google.generativeai, etc.) — the new `viz_generator/llm.py` needs the same imports.

- [ ] **Step 2: Create `backend/viz_generator/llm.py`**

The file contents are: the imports from fixed_main_v6.py's header that are LLM-related (`openai`, `google.generativeai` if used, `os`, `time`, `logging`, `sys`), followed by the moved code blocks listed above. **Preserve every line verbatim** — this is a pure move, not a refactor. Re-use the deduplicated helpers Stage 1 already extracted:

```python
# At top of viz_generator/llm.py, after the existing imports, REPLACE the
# Stage-1 import line `from backend.llm import is_reasoning_model` (it's
# already present in fixed_main_v6.py) with the broader pull below:
from backend.llm import (
    PRICE_PER_1K,           # already imported as alias PRICE_PER_1K_TOKENS in source
    REASONING_PREFIXES,     # already imported as alias _REASONING_MODEL_PREFIXES
    cost_usd,
    extract_openai_error,
    is_reasoning_model,
)
```

Leave the aliases (`PRICE_PER_1K_TOKENS = PRICE_PER_1K`) intact at the top of `viz_generator/llm.py` because the moved code references them.

After moving each block, add a short module docstring at the top:

```python
"""LLM client used by the viz generator (Universal Visualizer subprocess).

Multi-provider (OpenAI + Gemini) via LLM_PROVIDER env var. Different from
backend/llm/client.py — which is OpenAI-only and uses (system_prompt,
user_prompt). This module uses messages: list[dict] and supports two
providers. The two coexist intentionally; unification is a Stage-3+
decision.

Stage-1 deduplicated helpers are pulled in from backend.llm:
  - PRICE_PER_1K (the pricing table)
  - REASONING_PREFIXES + is_reasoning_model (reasoning-model detection)
  - cost_usd (per-call cost computation)
  - extract_openai_error (OpenAI error body parsing)
"""
```

- [ ] **Step 3: Delete the moved blocks from `fixed_main_v6.py`**

For each block, remove the original definition. Replace it with a re-import line so the rest of `fixed_main_v6.py` (which still has many call sites referencing `llm_call`, `_get_client`, `TokenUsageTracker`, `status`, `_is_reasoning_model`, `_init_client`) can find them through the new module.

Add after the existing `from backend.llm import is_reasoning_model` line near the top of `fixed_main_v6.py`:

```python
from backend.viz_generator.llm import (
    LLM_PROVIDER,
    DEFAULT_MODELS,
    MODEL_NAME,
    TOKEN_BUDGET,
    TokenUsageTracker,
    token_tracker,
    status,
    _init_client,
    _get_client,
    llm_call,
    _is_reasoning_model,
)
```

Adjust the import list above to match the exact symbols `fixed_main_v6.py` currently exposes at module scope — read the source and confirm. Do not silently drop a symbol; if you find one not in the list above, add it.

- [ ] **Step 4: Verify the file parses**

```bash
.venv/bin/python -c "import ast; ast.parse(open('fixed_main_v6.py').read()); print('OK')"
.venv/bin/python -c "import ast; ast.parse(open('backend/viz_generator/llm.py').read()); print('OK')"
```

Expected: both `OK`.

- [ ] **Step 5: Smoke-test imports**

```bash
OPENAI_API_KEY=sk-test .venv/bin/python -c "
from backend.viz_generator import llm
from fixed_main_v6 import llm_call, TokenUsageTracker, token_tracker, status, _get_client
print('OK')
"
```

Expected: `OK` with no exceptions.

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: 48 passed (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/viz_generator/llm.py fixed_main_v6.py
git commit -m "refactor(viz): move LLM machinery into backend/viz_generator/llm.py

Pure relocation of the multi-provider LLM client (OpenAI + Gemini)
out of fixed_main_v6.py. The argv contract is unchanged because
fixed_main_v6.py re-imports the moved symbols for back-compat
during the rest of Stage 2."
```

---

## Task 3 — Move parsing + file helpers

**Files created:**
- `backend/viz_generator/parsing.py`
- `backend/viz_generator/files.py`
- `tests/unit/viz_generator/test_parsing.py`
- `tests/unit/viz_generator/test_files.py`

**Functions to move (post-Stage-1 line numbers in `fixed_main_v6.py`):**

To `parsing.py` (lines ~1159–1287):
- `parse_files`
- `_parse_marker_format`
- `_clean_file_content`
- `_parse_codeblock_format`
- `_extract_filename`
- `format_files_for_prompt`

To `files.py` (lines ~1145–1158 + 1288–1394):
- `_filter_bogus_files`
- `_validate_filepath`
- `write_to_disk`
- `enforce_pinned_deps`
- `print_error_block`

- [ ] **Step 1: Create `backend/viz_generator/parsing.py`**

Pure move. The functions don't reference module-globals other than constants like `ERROR_DISPLAY_MAX_LINES` (which lives near the top of `fixed_main_v6.py`). If a constant is referenced, **import it** into the new module from `fixed_main_v6` for now (final cleanup happens at Task 12 when constants centralize into the cli.py / a constants module).

Top of file:
```python
"""File-format parsing helpers for the viz generator.

The LLM outputs a multi-file React+Vite project in one of two formats —
the marker format (custom delimiters) or the fenced-codeblock format.
This module owns the parsers and the shared helpers (filename sniff,
content cleaning).
"""
from __future__ import annotations

import re
from typing import Optional
```

- [ ] **Step 2: Create `backend/viz_generator/files.py`**

Pure move. `write_to_disk` and `_validate_filepath` use `pathlib.Path` and standard imports.

Top of file:
```python
"""On-disk file operations for the viz generator.

Validates filepaths (no traversal), writes the parsed multi-file project to
the output directory, pins dependency versions in package.json, and filters
bogus filenames the LLM occasionally hallucinates.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterable
```

- [ ] **Step 3: Add re-import shim in `fixed_main_v6.py`**

After the existing imports, add:
```python
from backend.viz_generator.parsing import (
    parse_files,
    _parse_marker_format,
    _clean_file_content,
    _parse_codeblock_format,
    _extract_filename,
    format_files_for_prompt,
)
from backend.viz_generator.files import (
    _filter_bogus_files,
    _validate_filepath,
    write_to_disk,
    enforce_pinned_deps,
    print_error_block,
)
```

Then delete the original definitions from `fixed_main_v6.py`.

- [ ] **Step 4: Write unit tests for `parsing.py`**

Create `tests/unit/viz_generator/test_parsing.py`:
```python
"""Unit tests for backend.viz_generator.parsing."""
from __future__ import annotations

from backend.viz_generator.parsing import (
    parse_files,
    _parse_marker_format,
    _parse_codeblock_format,
    _extract_filename,
)


def test_extract_filename_from_codeblock_header():
    assert _extract_filename("```javascript src/App.jsx") == "src/App.jsx"
    assert _extract_filename("```ts file: src/main.ts") == "src/main.ts"
    assert _extract_filename("plain text") is None


def test_parse_files_handles_marker_format():
    text = """
=== FILE: src/App.jsx ===
import React from 'react';
export default function App() { return <div>hi</div>; }
=== END FILE ===
"""
    files = parse_files(text)
    assert "src/App.jsx" in files
    assert "import React" in files["src/App.jsx"]


def test_parse_files_handles_codeblock_format():
    text = '''
```jsx src/App.jsx
import React from 'react';
export default function App() { return <div>hi</div>; }
```
'''
    files = parse_files(text)
    assert "src/App.jsx" in files
    assert "import React" in files["src/App.jsx"]


def test_parse_files_returns_empty_for_unparseable():
    files = parse_files("just plain prose with no code blocks")
    assert files == {}
```

If the actual parse functions raise on unparseable input (rather than returning `{}`), tighten the assertion to match the actual behavior.

- [ ] **Step 5: Write unit tests for `files.py`**

Create `tests/unit/viz_generator/test_files.py`:
```python
"""Unit tests for backend.viz_generator.files."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.viz_generator.files import (
    _validate_filepath,
    enforce_pinned_deps,
)


def test_validate_filepath_rejects_traversal(tmp_path):
    with pytest.raises(Exception):
        _validate_filepath(tmp_path, "../../../etc/passwd")


def test_validate_filepath_accepts_normal_path(tmp_path):
    p = _validate_filepath(tmp_path, "src/App.jsx")
    assert p == tmp_path / "src" / "App.jsx"


def test_enforce_pinned_deps_overrides_package_json():
    files = {
        "package.json": '{"dependencies": {"react": "*", "vite": "latest"}}',
        "src/App.jsx": "// ...",
    }
    out = enforce_pinned_deps(files)
    pkg = out["package.json"]
    # The function pins specific versions; assert "*" and "latest" are gone.
    assert '"*"' not in pkg
    assert '"latest"' not in pkg
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/unit/viz_generator/ -v
```

Expected: ~7 passing (4 parsing + 3 files).

```bash
.venv/bin/pytest tests/ -q
```

Expected: ~55 passing (48 + 7).

- [ ] **Step 7: Commit**

```bash
git add backend/viz_generator/parsing.py backend/viz_generator/files.py tests/unit/viz_generator/test_parsing.py tests/unit/viz_generator/test_files.py fixed_main_v6.py
git commit -m "refactor(viz): extract parsing + files helpers into viz_generator/

Pure move. parse_files (both marker + codeblock formats) and write_to_disk
+ _validate_filepath + enforce_pinned_deps now live in dedicated modules
under backend/viz_generator/. Unit tests added for the high-leverage
helpers (path-traversal rejection, dep pinning, format-specific parsers)."
```

---

## Task 4 — Move file-selection helpers to `select.py`

**Files:**
- Create: `backend/viz_generator/select.py`
- Create: `tests/unit/viz_generator/test_select.py`
- Modify: `fixed_main_v6.py`

**Functions to move (lines ~1703–1794):**
- `select_relevant_files`
- `format_files_compact`
- `merge_patches`

- [ ] **Step 1: Create `backend/viz_generator/select.py`**

Top:
```python
"""File-selection helpers for the viz generator fix loops.

When a build or runtime error happens, the fix loop asks the LLM which
subset of project files needs to be re-emitted. These helpers ask the LLM
(select_relevant_files), format a compact representation of the project
(format_files_compact), and merge LLM-emitted patches back over the
original files (merge_patches).
"""
from __future__ import annotations

import json
import logging
from typing import Any
```

Pure move; preserve the function bodies. They call `llm_call` — import it from the new location: `from backend.viz_generator.llm import llm_call`.

- [ ] **Step 2: Re-import in `fixed_main_v6.py`**

```python
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)
```

Delete the original definitions.

- [ ] **Step 3: Write unit tests**

Create `tests/unit/viz_generator/test_select.py`:
```python
"""Unit tests for backend.viz_generator.select."""
from __future__ import annotations

from backend.viz_generator.select import merge_patches, format_files_compact


def test_merge_patches_overrides_existing_file():
    original = {"src/App.jsx": "old", "src/main.tsx": "unchanged"}
    patches = {"src/App.jsx": "new"}
    merged = merge_patches(original, patches)
    assert merged["src/App.jsx"] == "new"
    assert merged["src/main.tsx"] == "unchanged"


def test_merge_patches_adds_new_file():
    original = {"src/App.jsx": "x"}
    patches = {"src/utils.ts": "export const a = 1;"}
    merged = merge_patches(original, patches)
    assert "src/utils.ts" in merged
    assert "src/App.jsx" in merged


def test_format_files_compact_lists_paths():
    files = {"src/App.jsx": "// code", "package.json": "{}"}
    out = format_files_compact(files)
    assert "src/App.jsx" in out
    assert "package.json" in out
```

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/select.py tests/unit/viz_generator/test_select.py fixed_main_v6.py
git commit -m "refactor(viz): extract file-selection helpers into viz_generator/select.py"
```

---

## Task 5 — Move topic classification to `topic.py`

**Files:**
- Create: `backend/viz_generator/topic.py`
- Modify: `fixed_main_v6.py`

**Function to move (lines ~1047–1122):**
- `classify_topic`

This is one focused function. It calls `llm_call`. Pure move.

- [ ] **Step 1: Create `backend/viz_generator/topic.py`**

```python
"""Topic classification for the viz generator.

The first LLM call in the pipeline. Maps a free-text topic + brief into
a structured (topic_kind, metadata) tuple that the subsequent phases use
to pick prompts and templates.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from backend.viz_generator.llm import llm_call
```

Move the function body verbatim.

- [ ] **Step 2: Re-import in `fixed_main_v6.py`**

```python
from backend.viz_generator.topic import classify_topic
```

Delete the original.

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/topic.py fixed_main_v6.py
git commit -m "refactor(viz): extract classify_topic into viz_generator/topic.py"
```

No unit test for `classify_topic` — it's primarily an LLM call with prompt construction. Covered by integration/E2E.

---

## Task 6 — Move npm + screenshot helpers

**Files:**
- Create: `backend/viz_generator/npm.py`
- Create: `backend/viz_generator/screenshot.py`
- Modify: `fixed_main_v6.py`

**To `npm.py` (lines ~1867–1903 + 2033–2057):**
- `_run_npm_install`
- `_run_npm_build`
- `_pick_free_port`
- `_wait_for_server`

**To `screenshot.py` (lines ~2021–2031 + 2058–2216):**
- `_playwright_available`
- `run_semantic_checks`
- `_evaluate_assertion`

`build_error_loop` (lines 1905–2016) and `runtime_fix_loop` (lines 2217–2408) stay in `fixed_main_v6.py` for now; they move in Tasks 7-8.

- [ ] **Step 1: Create `backend/viz_generator/npm.py`**

```python
"""npm + port management for the viz generator.

Runs `npm install` and `npm run build` in a project directory, picks a
free port for the preview dev server, and waits for the server to become
reachable.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import time
from pathlib import Path
```

Move the four functions.

- [ ] **Step 2: Create `backend/viz_generator/screenshot.py`**

```python
"""Playwright-driven runtime validation + semantic checks for built vizes.

After a successful npm build + dev server, this module navigates the
running app and runs assertion DSL checks (semantic checks) against
the live DOM and JS state to confirm the viz actually works.
"""
from __future__ import annotations

import logging
from typing import Any
```

Move the three functions. They may reference module-level constants from `fixed_main_v6.py` (e.g., `PREVIEW_STARTUP_WAIT`); import those into the new module from `fixed_main_v6` for now, or — if they're simple ints — duplicate the literal in the new module with a comment "// pinned from fixed_main_v6.py PREVIEW_STARTUP_WAIT".

- [ ] **Step 3: Re-import in `fixed_main_v6.py`**

```python
from backend.viz_generator.npm import (
    _run_npm_install,
    _run_npm_build,
    _pick_free_port,
    _wait_for_server,
)
from backend.viz_generator.screenshot import (
    _playwright_available,
    run_semantic_checks,
    _evaluate_assertion,
)
```

Delete the originals.

- [ ] **Step 4: Run tests + smoke-import + commit**

```bash
.venv/bin/pytest tests/ -q
.venv/bin/python -c "from backend.viz_generator import npm, screenshot; print('OK')"
git add backend/viz_generator/npm.py backend/viz_generator/screenshot.py fixed_main_v6.py
git commit -m "refactor(viz): extract npm + screenshot helpers into viz_generator/

npm.py: install/build/port-pick/server-wait
screenshot.py: playwright availability check + semantic-check DSL

build_error_loop and runtime_fix_loop still in fixed_main_v6.py;
they migrate to viz_generator/phases/ in Tasks 7-8."
```

---

## Task 7 — Move `generate_draft_code` to `phases/draft.py`

**Files:**
- Create: `backend/viz_generator/phases/draft.py`
- Modify: `fixed_main_v6.py`

**Function to move (lines ~1799–1862):**
- `generate_draft_code`

- [ ] **Step 1: Create `backend/viz_generator/phases/draft.py`**

```python
"""Step 1 of the viz pipeline: generate the initial multi-file React+Vite
project from the topic + brief.

This is the most expensive LLM call in the entire viz pipeline — it
generates ~10-30 files of React/TypeScript code in one shot. Stage 2
adds task=LLMTask.VIZ_DRAFT routing so this call uses gpt-4o by default
(see Task 13).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import llm_call
from backend.viz_generator.parsing import parse_files, format_files_for_prompt
from backend.viz_generator.files import enforce_pinned_deps, write_to_disk
```

Move `generate_draft_code` verbatim. It uses `parse_files`, `write_to_disk`, `enforce_pinned_deps`, `print_error_block` — import each from the new location.

- [ ] **Step 2: Re-import in `fixed_main_v6.py`**

```python
from backend.viz_generator.phases.draft import generate_draft_code
```

Delete the original.

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/phases/draft.py fixed_main_v6.py
git commit -m "refactor(viz): extract step1 generate_draft_code into phases/draft.py"
```

---

## Task 8 — Move `build_error_loop` to `phases/build_loop.py`

**Files:**
- Create: `backend/viz_generator/phases/build_loop.py`
- Modify: `fixed_main_v6.py`

**Function to move (lines ~1905–2016):**
- `build_error_loop`

- [ ] **Step 1: Create the file**

```python
"""Step 2 of the viz pipeline: build the project with Vite and iteratively
fix build errors using the LLM until `npm run build` succeeds (or the loop
exhausts its budget).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import llm_call
from backend.viz_generator.npm import _run_npm_install, _run_npm_build
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import write_to_disk, print_error_block
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)
```

Move `build_error_loop` verbatim. It coordinates `_run_npm_install`, `_run_npm_build`, `llm_call`, `select_relevant_files`, `merge_patches`, `write_to_disk`, and `print_error_block`.

- [ ] **Step 2: Re-import + delete from `fixed_main_v6.py`**

```python
from backend.viz_generator.phases.build_loop import build_error_loop
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/phases/build_loop.py fixed_main_v6.py
git commit -m "refactor(viz): extract step2 build_error_loop into phases/build_loop.py"
```

---

## Task 9 — Move `runtime_fix_loop` to `phases/runtime_loop.py`

**Files:**
- Create: `backend/viz_generator/phases/runtime_loop.py`
- Modify: `fixed_main_v6.py`

**Function to move (lines ~2217–2408):**
- `runtime_fix_loop`

- [ ] **Step 1: Create the file**

```python
"""Step 3 of the viz pipeline: launch the built dev server, run Playwright
semantic checks against the live app, and iteratively fix runtime errors
using the LLM until checks pass (or the loop exhausts its budget).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import llm_call
from backend.viz_generator.npm import _pick_free_port, _wait_for_server
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import write_to_disk, print_error_block
from backend.viz_generator.screenshot import (
    _playwright_available,
    run_semantic_checks,
    _evaluate_assertion,
)
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)
```

Move `runtime_fix_loop`. If it spawns its own dev server via `subprocess.Popen`, that code moves too.

- [ ] **Step 2: Re-import + delete**

```python
from backend.viz_generator.phases.runtime_loop import runtime_fix_loop
```

- [ ] **Step 3: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/phases/runtime_loop.py fixed_main_v6.py
git commit -m "refactor(viz): extract step3 runtime_fix_loop into phases/runtime_loop.py"
```

---

## Task 10 — Move `design_polish_pass` to `phases/polish.py`

**Files:**
- Create: `backend/viz_generator/phases/polish.py`
- Modify: `fixed_main_v6.py`

**Function to move (lines ~2414–2461):**
- `design_polish_pass`

- [ ] **Step 1: Create the file**

```python
"""Step 4 of the viz pipeline (optional, --polish flag): a final LLM pass
that improves visual design — typography, spacing, color, motion — on the
already-working viz.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import llm_call
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import write_to_disk
from backend.viz_generator.select import merge_patches
```

Move `design_polish_pass`.

- [ ] **Step 2: Re-import + delete + run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/phases/polish.py fixed_main_v6.py
git commit -m "refactor(viz): extract step4 design_polish_pass into phases/polish.py"
```

---

## Task 11 — Move postprocess from `main.py` to `viz_generator/postprocess.py`

**Files:**
- Create: `backend/viz_generator/postprocess.py`
- Create: `tests/unit/viz_generator/test_postprocess.py`
- Modify: `main.py`

**Functions to move (in `main.py`, lines ~437–625):**
- `_patch_vite_config_base`
- `_inject_error_boundary`

These currently live in `main.py` but are viz-output post-processing. They properly belong in `viz_generator/`. Their callers in `main.py` (around lines 700–750) need to be updated to import from the new location.

- [ ] **Step 1: Create `backend/viz_generator/postprocess.py`**

```python
"""Post-build patches applied to the generated viz project before it's
published or previewed.

_patch_vite_config_base: ensures vite.config.{ts,js} has `base: './'` so
the production build works when served from a sub-path (e.g., GitHub
Pages preview path).

_inject_error_boundary: wraps the app entry in a React ErrorBoundary so
runtime errors render a friendly fallback instead of a blank white screen.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable
```

Move both functions verbatim. Their `on_log` callable parameter is preserved.

- [ ] **Step 2: Update call sites in `main.py`**

Find the two call sites:
```bash
grep -n "_patch_vite_config_base\|_inject_error_boundary" main.py
```

Add at top of `main.py`:
```python
from backend.viz_generator.postprocess import _patch_vite_config_base, _inject_error_boundary
```

Delete the original definitions in `main.py`.

- [ ] **Step 3: Write unit tests**

Create `tests/unit/viz_generator/test_postprocess.py`:
```python
"""Unit tests for backend.viz_generator.postprocess.

Real bug history in this code path — see commits 0099c84
(base:'./' must apply to .js config too) and 68ddd9c (ErrorBoundary
injection). These tests are regression guards for both fixes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.viz_generator.postprocess import (
    _patch_vite_config_base,
    _inject_error_boundary,
)


def _noop_log(msg: str) -> None:
    pass


def test_patch_vite_config_base_for_ts(tmp_path):
    cfg = tmp_path / "vite.config.ts"
    cfg.write_text("""import { defineConfig } from 'vite';
export default defineConfig({
  plugins: [react()],
});
""")
    _patch_vite_config_base(tmp_path, _noop_log)
    out = cfg.read_text()
    assert "base:" in out
    assert "'./'" in out or '"./"' in out


def test_patch_vite_config_base_for_js(tmp_path):
    cfg = tmp_path / "vite.config.js"
    cfg.write_text("""import { defineConfig } from 'vite';
export default defineConfig({
  plugins: [react()],
});
""")
    _patch_vite_config_base(tmp_path, _noop_log)
    out = cfg.read_text()
    assert "base:" in out


def test_inject_error_boundary_creates_file(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    main = src / "main.tsx"
    main.write_text("""import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
""")
    _inject_error_boundary(tmp_path, _noop_log)
    # ErrorBoundary should now exist and main.tsx should reference it
    # (exact assertions depend on the implementation — tighten after running).
    assert (src / "ErrorBoundary.tsx").exists() or "ErrorBoundary" in main.read_text()
```

Tighten the assertions in `test_inject_error_boundary_creates_file` after running it against the actual implementation. The spec is "wraps the app entry"; the test verifies that wrapping happens, however it's structured.

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/viz_generator/postprocess.py tests/unit/viz_generator/test_postprocess.py main.py
git commit -m "refactor(viz): move postprocess from main.py to viz_generator/postprocess.py

_patch_vite_config_base and _inject_error_boundary properly belong with
the viz generator (they post-process its output). main.py just imports
them at the two call sites in the build orchestration.

Unit tests cover both real-bug fixes from prior commits 0099c84 and
68ddd9c."
```

---

## Task 12 — Move `main()` to `cli.py` and reduce `fixed_main_v6.py` to a stub

**Files:**
- Create: `backend/viz_generator/cli.py`
- Modify: `backend/viz_generator/__init__.py`
- Modify: `fixed_main_v6.py` (full replacement with 2-line stub)

**Function to move (lines ~2466–end):**
- `main`
- The `if __name__ == "__main__":` guard
- The `argparse.ArgumentParser` setup (which may be inside `main()` or top-level)

- [ ] **Step 1: Create `backend/viz_generator/cli.py`**

```python
"""CLI entry point for the viz generator subprocess.

The argv contract is unchanged from fixed_main_v6.py's original main().
The orchestrator (backend/orchestrator.py) spawns this via the
FIXED_MAIN_PATH env var which still points at fixed_main_v6.py — that
file is now a 2-line stub that calls back here.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from backend.viz_generator.llm import llm_call, status, token_tracker
from backend.viz_generator.topic import classify_topic
from backend.viz_generator.phases.draft import generate_draft_code
from backend.viz_generator.phases.build_loop import build_error_loop
from backend.viz_generator.phases.runtime_loop import runtime_fix_loop
from backend.viz_generator.phases.polish import design_polish_pass
```

Move `main()` and the argparse setup verbatim. Adjust the imports inside `main()` (if any) to use the new module paths.

The bottom of the file:
```python
if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Refactor argparse setup to expose `build_parser()`**

The contract test in Task 1 (`test_known_flags_present_in_help`) only checks `--help`. To make argparse testable directly without subprocess, extract the parser construction into a function:

```python
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(...)
    p.add_argument("--topic", required=True, help="...")
    # ... every other flag
    return p


def main() -> None:
    args = build_parser().parse_args()
    # ... existing main body
```

Then strengthen `tests/contract/test_viz_cli.py` with a programmatic argparse test:

```python
def test_build_parser_has_all_known_flags():
    from backend.viz_generator.cli import build_parser
    parser = build_parser()
    # Every documented flag must parse:
    args = parser.parse_args([
        "--topic", "test",
        # add every required flag the orchestrator passes today;
        # use the orchestrator.py call site as source of truth
    ])
    assert args.topic == "test"
```

Inspect `orchestrator.py` to see what flags it passes. Each one must appear in this test.

- [ ] **Step 3: Wire `backend/viz_generator/__init__.py`**

```python
"""Viz generator package — the subprocess that turns a topic + brief into
a working React+Vite visualization project.

Public surface: cli.main (the entry point invoked by the orchestrator
subprocess) and the modules listed in
docs/superpowers/specs/2026-05-25-modularization-design.md Section 1.
"""
from backend.viz_generator.cli import main, build_parser

__all__ = ["main", "build_parser"]
```

- [ ] **Step 4: Replace `fixed_main_v6.py` entirely with a 2-line stub**

```python
"""Compatibility stub — the viz generator now lives in
backend/viz_generator/. The orchestrator's FIXED_MAIN_PATH env var still
points here so the subprocess spawn contract is preserved.
"""
from backend.viz_generator.cli import main

if __name__ == "__main__":
    main()
```

Use the `Write` tool to fully overwrite the file.

- [ ] **Step 5: Smoke-test the subprocess works**

```bash
.venv/bin/python fixed_main_v6.py --help 2>&1 | head -20
.venv/bin/python -m backend.viz_generator.cli --help 2>&1 | head -20
```

Expected: both produce identical argparse help output.

- [ ] **Step 6: Run full suite**

```bash
.venv/bin/pytest tests/ -q
```

Expected: all tests still passing (the contract test from Task 1 covers --help; the new `test_build_parser_has_all_known_flags` covers the argparse structure).

- [ ] **Step 7: Commit**

```bash
git add backend/viz_generator/cli.py backend/viz_generator/__init__.py fixed_main_v6.py tests/contract/test_viz_cli.py
git commit -m "refactor(viz): main() → viz_generator/cli.py; fixed_main_v6.py → 2-line stub

Stage 2 decomposition complete. The 2,541-line fixed_main_v6.py is now
a stub that calls backend.viz_generator.cli.main. The argv contract is
preserved (verified by the strengthened contract test). The subprocess
spawn from orchestrator.py works unchanged (FIXED_MAIN_PATH still
resolves to fixed_main_v6.py)."
```

---

## Task 13 — Wire `LLMTask.VIZ_*` at the new viz call sites

This activates the per-task cost routing for the viz generator (the spec's Section 2 cost optimization). Defaults are: `VIZ_DRAFT` → `gpt-4o` (the heavy one), every other VIZ_* → `gpt-4o-mini`. Stage 1 already set these defaults in `backend/llm/tasks.py`; this task just plumbs the kwarg through the call chain.

**Files modified:**
- `backend/viz_generator/topic.py` — `classify_topic` passes `task=LLMTask.VIZ_TOPIC_CLASSIFY`
- `backend/viz_generator/phases/draft.py` — `generate_draft_code` passes `task=LLMTask.VIZ_DRAFT`
- `backend/viz_generator/phases/build_loop.py` — `build_error_loop` passes `task=LLMTask.VIZ_BUILD_FIX`
- `backend/viz_generator/phases/runtime_loop.py` — `runtime_fix_loop` passes `task=LLMTask.VIZ_RUNTIME_FIX`
- `backend/viz_generator/phases/polish.py` — `design_polish_pass` passes `task=LLMTask.VIZ_POLISH`
- `backend/viz_generator/select.py` — `select_relevant_files` passes `task=LLMTask.VIZ_BUILD_FIX` (it's used by the build loop)

**Important: viz_generator's `llm_call` must accept and forward `task`**

The viz `llm_call` (in `viz_generator/llm.py`) currently takes `messages: list[dict]` plus various kwargs. Add a `task: Optional[LLMTask] = None` parameter. When `LLM_PROVIDER == "openai"`, forward `task` resolution by calling `resolve_model(task)` from `backend.llm` and overriding the OpenAI model. When `LLM_PROVIDER == "gemini"`, ignore `task` (Gemini has its own model name flow via `MODEL_NAME` env).

- [ ] **Step 1: Add `task` parameter to `viz_generator.llm.llm_call`**

In `backend/viz_generator/llm.py`, change the `llm_call` signature:

```python
from typing import Optional
from backend.llm import LLMTask, resolve_model

def llm_call(
    messages: list[dict],
    step_label: str,
    # ... existing params ...
    task: Optional[LLMTask] = None,
) -> str:
    """... existing docstring ...

    `task` (new in Stage 2): if provided AND LLM_PROVIDER is openai,
    overrides the model with resolve_model(task). For gemini provider,
    `task` is ignored (Gemini doesn't use the per-task model map).
    """
    # Inside the function, BEFORE choosing the model:
    if task is not None and LLM_PROVIDER == "openai":
        openai_model = resolve_model(task)
    else:
        openai_model = MODEL_NAME  # existing behavior
    # ... rest of function uses `openai_model` where it currently uses MODEL_NAME ...
```

The exact integration point depends on the function's current structure — find where `MODEL_NAME` is used as the OpenAI model name and route through `openai_model` instead.

- [ ] **Step 2: Update each viz call site**

For each of the 5 phase files and topic.py, find the `llm_call(...)` call and add the appropriate `task=LLMTask.VIZ_*` kwarg.

Example for `phases/draft.py`:
```python
from backend.llm import LLMTask  # add to imports

# Existing call:
raw = llm_call(
    messages=messages,
    step_label="step1_generate",
    # ... existing kwargs ...
)
# Becomes:
raw = llm_call(
    messages=messages,
    step_label="step1_generate",
    # ... existing kwargs ...
    task=LLMTask.VIZ_DRAFT,
)
```

For `select.py`, where `select_relevant_files` is used by the build loop, use `task=LLMTask.VIZ_BUILD_FIX`.

- [ ] **Step 3: Add an integration test that verifies the kwarg is plumbed**

Create `tests/unit/viz_generator/test_llm_task_routing.py`:
```python
"""Verify each viz phase passes the correct LLMTask to llm_call."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.llm import LLMTask


@patch("backend.viz_generator.llm.llm_call")
def test_draft_passes_viz_draft_task(mock_llm_call):
    mock_llm_call.return_value = '=== FILE: src/App.jsx ===\nexport default function App() {}\n=== END FILE ==='
    from backend.viz_generator.phases.draft import generate_draft_code
    # Call with minimal valid inputs; the exact signature depends on the
    # current generate_draft_code — fill in the required args.
    try:
        generate_draft_code(
            topic="x", brief="y", project_dir=None,  # adjust to real signature
        )
    except Exception:
        pass  # may fail downstream of llm_call, that's fine
    # Verify llm_call was called with task=LLMTask.VIZ_DRAFT
    args, kwargs = mock_llm_call.call_args
    assert kwargs.get("task") == LLMTask.VIZ_DRAFT


# Repeat for each phase. If signatures vary too much, parametrize over
# (phase_function, expected_task) pairs.
```

Adjust the test to match each function's actual signature. The point is: assert the right `task` flows to `llm_call`. If a phase is hard to call directly because of side-effects, instead inspect the source for `task=LLMTask.VIZ_*` literal via a grep-style test:

```python
def test_phase_files_reference_correct_task():
    from pathlib import Path
    base = Path(__file__).resolve().parents[2] / "backend" / "viz_generator"
    expected = {
        "phases/draft.py": "LLMTask.VIZ_DRAFT",
        "phases/build_loop.py": "LLMTask.VIZ_BUILD_FIX",
        "phases/runtime_loop.py": "LLMTask.VIZ_RUNTIME_FIX",
        "phases/polish.py": "LLMTask.VIZ_POLISH",
        "topic.py": "LLMTask.VIZ_TOPIC_CLASSIFY",
    }
    for rel, expected_token in expected.items():
        text = (base / rel).read_text()
        assert expected_token in text, f"{rel} missing {expected_token}"
```

This second test is lower-cost and catches accidental removal in future refactors.

- [ ] **Step 4: Run tests + commit**

```bash
.venv/bin/pytest tests/ -q
```

```bash
git add backend/viz_generator/llm.py backend/viz_generator/topic.py backend/viz_generator/select.py backend/viz_generator/phases/ tests/unit/viz_generator/test_llm_task_routing.py
git commit -m "feat(viz): route each phase's llm_call through LLMTask

VIZ_DRAFT → gpt-4o (heavy); all other viz tasks → gpt-4o-mini.
Operators can override per task via MODEL_VIZ_DRAFT, MODEL_VIZ_FIX,
MODEL_VIZ_RUNTIME, MODEL_VIZ_POLISH, MODEL_VIZ_CLASSIFY.

For LLM_PROVIDER=gemini, task is ignored (Gemini uses its own model
selection via MODEL_NAME)."
```

---

## Task 14 — Introduce `backend/config.py`

The spec listed this in Stage 1 but it wasn't implemented; landing here. Replaces scattered `os.getenv(...)` calls in `backend/llm/client.py`, `backend/llm/tracker.py`, and `main.py` with a single `pydantic-settings` source of truth.

**Files:**
- Create: `backend/config.py`
- Create: `tests/unit/config/__init__.py`
- Create: `tests/unit/config/test_settings.py`
- Modify: `backend/llm/client.py` — use `settings` for `REASONING_EFFORT` and `MAX_OUTPUT_TOKENS_DEFAULT`
- Modify: `backend/llm/tracker.py` — use `settings.token_budget_per_job`
- Modify: `main.py` — replace 5–10 `os.getenv` calls with `settings`

- [ ] **Step 1: Add `pydantic-settings` to `requirements.txt` (if not already there)**

```bash
grep "pydantic-settings" requirements.txt
```

If missing, append:
```
pydantic-settings>=2.2.0,<3
```

Install:
```bash
.venv/bin/pip install pydantic-settings
```

- [ ] **Step 2: Create `backend/config.py`**

```python
"""Central env var configuration for the backend.

pydantic-settings reads .env and OS env at import time. Every module that
needs config imports `settings` from here. Avoids the scattered
os.getenv(...) pattern that Stage 1 left in place.

Stage 2 scope: OpenAI + LLM behavior + budgets + viz output path. Stage 3
will add the routing/orchestration settings (CORS, dev-server port range,
github publish flags) and complete the migration off os.getenv.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # OpenAI
    openai_api_key: str = ""
    openai_text_model: str = "gpt-4o-mini"
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 4096
    llm_client_timeout: float = 600.0

    # Per-task model overrides (optional; defaults live in backend.llm.tasks)
    model_agent_a: str | None = None
    model_agent_b: str | None = None
    model_viz_classify: str | None = None
    model_viz_draft: str | None = None
    model_viz_fix: str | None = None
    model_viz_runtime: str | None = None
    model_viz_polish: str | None = None

    # Budgets
    token_budget_per_job: int = 300_000

    # Viz generator (multi-provider)
    llm_provider: Literal["openai", "gemini"] = "openai"
    model_name: str | None = None  # if set, overrides per-provider default in viz_generator/llm.py


settings = Settings()
```

- [ ] **Step 3: Use `settings` in `backend/llm/tracker.py`**

Change the module-level default:
```python
# Before:
_DEFAULT_BUDGET = int(os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))

# After:
from backend.config import settings
_DEFAULT_BUDGET = settings.token_budget_per_job
```

Remove the now-unused `import os` if it's only used for this.

- [ ] **Step 4: Use `settings` in `backend/llm/client.py`**

Replace:
```python
REASONING_EFFORT = (os.getenv("REASONING_EFFORT", "low") or "low").lower()
if REASONING_EFFORT not in ("low", "medium", "high"):
    REASONING_EFFORT = "low"

MAX_OUTPUT_TOKENS_DEFAULT = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))
```

with:
```python
from backend.config import settings
REASONING_EFFORT = settings.reasoning_effort
MAX_OUTPUT_TOKENS_DEFAULT = settings.max_output_tokens
```

Inside `get_client()`, replace the `os.getenv("OPENAI_API_KEY", "")` and `os.getenv("LLM_CLIENT_TIMEOUT", "600")` with `settings.openai_api_key` and `settings.llm_client_timeout`.

Inside the `APITimeoutError` handler, replace the local `os.getenv("LLM_CLIENT_TIMEOUT", "600")` with `settings.llm_client_timeout`.

- [ ] **Step 5: Replace `os.getenv` in `main.py` (scoped to OpenAI/token-related calls only)**

```bash
grep -n "os.getenv" main.py
```

For each one that overlaps with `Settings`'s fields, replace with `settings.<field>`. Leave the routing/CORS/Github/dev-server env vars alone for now — they'll move in Stage 3.

- [ ] **Step 6: Write tests**

Create `tests/unit/config/test_settings.py`:
```python
"""Unit tests for backend.config.Settings."""
from __future__ import annotations

import pytest


def test_defaults_load(monkeypatch):
    # Strip envs that would override defaults
    for name in ("OPENAI_API_KEY", "OPENAI_TEXT_MODEL", "REASONING_EFFORT",
                 "MAX_OUTPUT_TOKENS", "TOKEN_BUDGET_PER_JOB", "LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.openai_text_model == "gpt-4o-mini"
    assert s.reasoning_effort == "low"
    assert s.max_output_tokens == 4096
    assert s.token_budget_per_job == 300_000
    assert s.llm_provider == "openai"


def test_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-5")
    monkeypatch.setenv("TOKEN_BUDGET_PER_JOB", "100000")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    from backend.config import Settings
    s = Settings()
    assert s.openai_text_model == "gpt-5"
    assert s.token_budget_per_job == 100000
    assert s.llm_provider == "gemini"


def test_invalid_reasoning_effort_raises(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "extreme")
    from backend.config import Settings
    with pytest.raises(Exception):
        Settings()


def test_per_task_model_override_optional(monkeypatch):
    monkeypatch.delenv("MODEL_VIZ_DRAFT", raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.model_viz_draft is None

    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    s2 = Settings()
    assert s2.model_viz_draft == "gpt-5"
```

- [ ] **Step 7: Run all tests + commit**

```bash
.venv/bin/pytest tests/ -q
git add backend/config.py tests/unit/config/ backend/llm/tracker.py backend/llm/client.py main.py requirements.txt
git commit -m "feat(config): introduce backend/config.py as single source of truth

pydantic-settings replaces scattered os.getenv() in backend/llm/* and
in main.py (OpenAI + budget settings only — routing/Github settings
migrate in Stage 3).

Was listed in the Stage 1 file structure but skipped; landing here
unblocks Stage 3's import-linter contract."
```

---

## Task 15 — Final verification + tag

- [ ] **Step 1: Run the full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: ~70 tests passing (45 prior + 3 viz CLI contract + 4 parsing + 3 files + 3 select + 5 LLMTask routing + 3 postprocess + 4 config = roughly 70 total, depending on tightenings made along the way).

- [ ] **Step 2: Smoke-test the FastAPI app boots**

```bash
OPENAI_API_KEY=sk-test .venv/bin/uvicorn main:app --port 18002 &
sleep 3
curl -s http://127.0.0.1:18002/healthz
kill %1
```

Expected: a JSON object with `"ok": true`.

- [ ] **Step 3: Smoke-test the subprocess argv contract is intact**

```bash
.venv/bin/python fixed_main_v6.py --help | head -20
.venv/bin/python -m backend.viz_generator.cli --help | head -20
```

Expected: identical argparse help from both paths.

- [ ] **Step 4: Confirm fixed_main_v6.py is the 2-line stub**

```bash
wc -l fixed_main_v6.py
cat fixed_main_v6.py
```

Expected: under 10 lines (allowing for the module docstring).

- [ ] **Step 5: Confirm import-linter readiness (for Stage 3)**

```bash
.venv/bin/python -c "
from backend.viz_generator.cli import main, build_parser
from backend.viz_generator.llm import llm_call
from backend.viz_generator.phases.draft import generate_draft_code
from backend.viz_generator.phases.build_loop import build_error_loop
from backend.viz_generator.phases.runtime_loop import runtime_fix_loop
from backend.viz_generator.phases.polish import design_polish_pass
from backend.viz_generator.postprocess import _patch_vite_config_base
from backend.config import settings
print('All Stage 2 modules import cleanly')
"
```

- [ ] **Step 6: Tag**

```bash
git tag -a modularization-stage-2 -m "Stage 2 of the modularization project — viz_generator/ package

Decomposed fixed_main_v6.py (2,541 lines) into a backend/viz_generator/
package of single-responsibility modules: cli, llm, topic, parsing,
files, select, npm, screenshot, postprocess, phases/{draft,build_loop,
runtime_loop,polish}.

fixed_main_v6.py is now a 2-line stub preserving the subprocess argv
contract. agents.py and main.py unchanged at the call-site level.

Stage 2 also wires LLMTask.VIZ_* at the five viz call sites for
per-task cost routing (defaults: VIZ_DRAFT → gpt-4o; all others →
gpt-4o-mini). Operators can tune via MODEL_VIZ_* env overrides.

backend/config.py introduced as single source of truth for OpenAI +
budget settings (was listed in Stage 1 file structure but skipped).

Tests: ~70 passing (Stage 1's 45 baseline + Stage 2's additions)."
```

---

## Self-review (run before sending the plan)

**Spec coverage:**

| Spec requirement | Implementing task |
|---|---|
| `backend/viz_generator/` package (Section 1) | Tasks 1–12 |
| `viz_generator/cli.py` preserving argv contract (Section 1, Section 4 Stage 2) | Task 12 |
| `viz_generator/{topic,parsing,files,select,npm,screenshot}.py` | Tasks 3–6 |
| `viz_generator/phases/{draft,build_loop,runtime_loop,polish}.py` | Tasks 7–10 |
| `viz_generator/postprocess.py` (from main.py) | Task 11 |
| `LLMTask.VIZ_*` plumbed at viz call sites (Section 2 cost win) | Task 13 |
| `backend/config.py` introduction (Section 1, deferred from Stage 1) | Task 14 |
| Subprocess CLI contract preserved (Section 4 Stage 2) | Tasks 1, 12 |
| `fixed_main_v6.py` stub at repo root (Section 4) | Task 12 |

**Deferred (with task / plan owner identified):**

- Performance optimizations from Section 2 (template cache, deterministic file-selection, parallelism, dev-server reuse): Stage-2b plan, written after Stage 2 merges.
- LLM client unification (orchestrator's `backend/llm` + viz_generator's multi-provider client): Stage-3+ decision.
- `import-linter` contract enforcement: Stage 3 (when `api/` and `services/` exist).

**Placeholder scan:** No TBDs. Status-code assertions in `test_viz_cli.py` are intentionally tightened during Task 1's Step 2 (the implementer reads the actual argparse setup and replaces the permissive assertion with the exact flag list). This is correct, not lazy.

**Type consistency:** `LLMTask` import path is `from backend.llm import LLMTask` everywhere. `task=LLMTask.VIZ_*` kwarg is consistent across draft/build_loop/runtime_loop/polish/topic/select. `Settings` field names match env var names via the auto-derived casing (e.g., `openai_text_model` ↔ `OPENAI_TEXT_MODEL`).

**File-size sanity:** No single new file is expected to exceed ~300 lines. The biggest is `viz_generator/llm.py` (multi-provider client, ~400 lines absorbed from fixed_main_v6.py) — at the upper boundary but still a focused single responsibility.
