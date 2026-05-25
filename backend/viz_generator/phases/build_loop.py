"""Step 2 of the viz pipeline: build the project with Vite and iteratively
fix build errors using the LLM until `npm run build` succeeds (or the loop
exhausts its budget).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import LLM_FIX_MAX_TOKENS, llm_call
from backend.viz_generator.npm import _run_npm_install, _run_npm_build
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import write_to_disk, print_error_block, enforce_pinned_deps
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT
from backend.viz_generator.llm import status

log = logging.getLogger("viz_agent")

# Maximum number of LLM-assisted build-fix iterations before giving up.
BUILD_RETRIES: int = 7


def build_error_loop(
    topic: str,
    pattern: dict[str, Any],
    project_dir: Path,
    files: dict[str, str],
) -> tuple[dict[str, str], bool]:
    """Run npm install then iteratively fix build errors using TARGETED PATCHES.

    Token-efficiency strategy:
      - On each retry, send only the files most likely to contain the bug
        (selected by error-keyword regex matching), not the full codebase.
      - Ask the LLM to output ONLY the changed files, not all files.
      - Memory of previous attempts is maintained via a running summary
        of "what was tried and failed" so the LLM doesn't repeat itself.
    """
    status("STEP 2 / 4", "BUILD ERROR LOOP")
    log.info("[Step 2] npm install...")

    if not _run_npm_install(project_dir):
        log.error("  ❌ npm install failed — skipping build loop.")
        return files, False

    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    # Memory: a short summary of past failed attempts so the LLM doesn't repeat itself
    attempt_history: list[str] = []

    for attempt in range(1, BUILD_RETRIES + 1):
        log.info("  [Build %d/%d]...", attempt, BUILD_RETRIES)
        r = _run_npm_build(project_dir)
        if r is None:
            return files, False
        if r.returncode == 0:
            log.info("  ✅ Build OK")
            return files, True

        error_log = (r.stderr or r.stdout).strip()
        log.info("  ❌ Build failed.")
        print_error_block(f"Build attempt {attempt} error", error_log)

        # ── Select only the files relevant to THIS error ──
        relevant = select_relevant_files(files, error_log, max_files=5)
        log.info("  → Sending %d relevant file(s) to LLM (out of %d total): %s",
                 len(relevant), len(files), ", ".join(relevant.keys()))

        # ── Build prompt with attempt memory but no full codebase ──
        history_block = ""
        if attempt_history:
            history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat these — they did not fix the issue):\n" + \
                            "\n".join(f"  - Attempt {i+1}: {h}" for i, h in enumerate(attempt_history))

        fix_prompt = f"""Vite build failed for "{topic}".

<current_build_error>
{error_log[:3000]}
</current_build_error>
{history_block}

You are seeing only the files most likely to contain the bug.
Apply the MINIMUM change needed to fix the error.
Do NOT rewrite working code. Do NOT touch files you do not change.

Output ONLY the files you actually modify, in this format:
==== FILE: path/to/changed.ts ====
[full new content of THAT FILE ONLY]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed and why>

<relevant_files>
{format_files_compact(relevant)}
</relevant_files>
"""
        raw = llm_call(
            [
                {"role": "system", "content": system},
                {"role": "user",   "content": fix_prompt},
            ],
            temperature=1,
            max_tokens=LLM_FIX_MAX_TOKENS,
            step_label=f"step2_build_fix:attempt_{attempt}",
        )

        # Capture the LLM's own one-line summary for the next attempt's memory
        summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
        if summary_match:
            attempt_history.append(summary_match.group(1).strip())
        else:
            # Synthesize a short summary from the error itself
            attempt_history.append(error_log.splitlines()[0][:140] if error_log else "no summary")

        patched = parse_files(raw)
        if not patched:
            log.info("  ⚠️  LLM returned no parseable file blocks.")
            break

        # ── Merge and write only the patched files ──
        patched = enforce_pinned_deps(patched)
        files = merge_patches(files, patched)
        write_to_disk(project_dir, patched)

        if "package.json" in patched:
            log.info("  package.json changed — re-running npm install...")
            if not _run_npm_install(project_dir):
                log.error("  ❌ npm install failed after package.json update.")
                return files, False

    log.info("  ⚠️  Build loop exhausted.")
    return files, False
