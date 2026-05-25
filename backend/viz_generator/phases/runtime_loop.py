"""Step 3 of the viz pipeline: launch the built dev server, run Playwright
semantic checks against the live app, and iteratively fix runtime errors
using the LLM until checks pass (or the loop exhausts its budget).
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

from backend.llm import LLMTask
from backend.viz_generator.llm import LLM_FIX_MAX_TOKENS, llm_call, status
from backend.viz_generator.npm import _run_npm_build, _run_npm_install
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import write_to_disk, print_error_block, enforce_pinned_deps
from backend.viz_generator.screenshot import run_semantic_checks
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT

log = logging.getLogger("viz_agent")

# Maximum number of LLM-assisted runtime-fix iterations before giving up.
RUNTIME_RETRIES: int = 3


def runtime_fix_loop(
    topic: str,
    pattern_name: str,
    pattern: dict[str, Any],
    project_dir: Path,
    files: dict[str, str],
    topic_slug: str,
) -> dict[str, str]:
    """Semantic runtime validation loop with TARGETED PATCHES.

    Same token-efficiency strategy as build_error_loop:
      - On rebuild failure: send only files relevant to the build error.
      - On semantic test failure: send only files relevant to the failing tests.
      - Maintain attempt history so the LLM doesn't repeat itself.
    """
    status("STEP 3 / 4", "SEMANTIC RUNTIME VALIDATION")
    log.info("[Step 3] Semantic runtime validation...")
    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    attempt_history: list[str] = []

    for attempt in range(1, RUNTIME_RETRIES + 1):
        r = _run_npm_build(project_dir)
        if r is None:
            log.info("  ⚠️  Build timed out.")
            break

        # ── Rebuild failure path ──
        if r.returncode != 0:
            log.info("  ⚠️  Rebuild failed (attempt %d) — sending error to LLM.", attempt)
            build_error_log = (r.stderr or r.stdout).strip()
            print_error_block(f"Rebuild attempt {attempt} error", build_error_log)

            relevant = select_relevant_files(files, build_error_log, max_files=5)
            log.info("  → Sending %d relevant file(s): %s",
                     len(relevant), ", ".join(relevant.keys()))

            history_block = ""
            if attempt_history:
                history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat):\n" + \
                    "\n".join(f"  - {h}" for h in attempt_history)

            build_fix_prompt = f"""The "{topic}" visualisation failed to rebuild during runtime validation.

<current_build_error>
{build_error_log[:3000]}
</current_build_error>
{history_block}

Apply the MINIMUM change needed. Do NOT rewrite working code.

Output ONLY changed files in this format:
==== FILE: path ====
[full new content of that file only]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed>

<relevant_files>
{format_files_compact(relevant)}
</relevant_files>
"""
            raw = llm_call(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": build_fix_prompt},
                ],
                temperature=1,
                max_tokens=LLM_FIX_MAX_TOKENS,
                step_label=f"step3_rebuild_fix:attempt_{attempt}",
                task=LLMTask.VIZ_RUNTIME_FIX,
            )

            summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
            attempt_history.append(
                summary_match.group(1).strip() if summary_match
                else (build_error_log.splitlines()[0][:140] if build_error_log else "no summary")
            )

            patched = parse_files(raw)
            if not patched:
                log.info("  ⚠️  LLM returned no files for build fix.")
                break
            patched = enforce_pinned_deps(patched)
            files = merge_patches(files, patched)
            write_to_disk(project_dir, patched)
            if "package.json" in patched:
                log.info("  package.json changed — re-running npm install...")
                if not _run_npm_install(project_dir):
                    break
            continue

        # ── Semantic test path ──
        failures = run_semantic_checks(project_dir, pattern["tests"], topic_slug=topic_slug)

        if not failures:
            log.info("  ✅ All checks passed (attempt %d).", attempt)
            return files

        infra_failures = [f for f in failures if f.get("infrastructure")]
        if infra_failures:
            log.info("\n  ⚠️  Infrastructure error — cannot validate runtime:")
            for f in infra_failures:
                log.info("    - %s: %s", f["description"], f["fix_hint"].splitlines()[0])
            log.info("  Skipping LLM fix loop (cannot fix infra failures from prompts).")
            log.info("  Most likely fix: run  %s -m playwright install chromium", sys.executable)
            return files

        failure_report = "\n".join(
            f"FAIL [{i+1}]: {f['description']}\n  -> {f['fix_hint']}"
            for i, f in enumerate(failures)
        )
        log.info("\n  ❌ %d failure(s):\n%s", len(failures), failure_report)

        # ── Pick relevant files based on the failure descriptions ──
        # Concatenate descriptions + hints so select_relevant_files can match keywords
        failure_text = "\n".join(f["description"] + " " + f["fix_hint"] for f in failures)

        # For semantic failures, App.tsx is almost always involved
        relevant = select_relevant_files(files, failure_text, max_files=5)
        if "src/App.tsx" not in relevant and "src/App.tsx" in files:
            relevant["src/App.tsx"] = files["src/App.tsx"]
        log.info("  → Sending %d relevant file(s): %s",
                 len(relevant), ", ".join(relevant.keys()))

        history_block = ""
        if attempt_history:
            history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat):\n" + \
                "\n".join(f"  - {h}" for h in attempt_history)

        fix_prompt = f"""The "{topic}" visualisation (pattern: {pattern_name}) has runtime failures:

{failure_report}

These are SEMANTIC/LOGIC failures, not syntax errors.

Diagnosis guide:
- "complete at step 0": done state initialised wrong — never true at start.
- "Step Forward no change": render doesn't read from steps[stepIdx].
- "0 or 1 total steps": step-generation loop is broken.
- "no circles/nodes": SVG structure missing.
- "console errors": unhandled exception — check the message.
- "glass-panel": tailwind config tokens not resolving.
{history_block}

Fix ONLY what the failure report lists. Apply the MINIMUM change needed.

Output ONLY changed files:
==== FILE: path ====
[full new content of that file only]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed>

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
            step_label=f"step3_semantic_fix:attempt_{attempt}",
            task=LLMTask.VIZ_RUNTIME_FIX,
        )

        summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
        attempt_history.append(
            summary_match.group(1).strip() if summary_match
            else (failures[0]["description"][:140] if failures else "no summary")
        )

        patched = parse_files(raw)
        if not patched:
            log.info("  ⚠️  LLM returned no files.")
            break
        patched = enforce_pinned_deps(patched)
        files = merge_patches(files, patched)
        write_to_disk(project_dir, patched)
        log.info("  → Patched %d file(s).", len(patched))

        if "package.json" in patched:
            log.info("  package.json changed — re-running npm install...")
            if not _run_npm_install(project_dir):
                break

    log.info("  ⚠️  Runtime loop exhausted.")
    return files
