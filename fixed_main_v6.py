"""
Universal Visualization Generator — v4.3 (Topic-Agnostic)
========================================================

Works for ANY topic: DSA, ML, CS concepts, math, networks, etc.
Examples:
  python main.py --topic "binary search tree insertion"
  python main.py --topic "gradient descent"
  python main.py --topic "dijkstra shortest path"
  python main.py --topic "neural network forward pass"
  python main.py --topic "fourier transform"
  python main.py --topic "convolutional neural network"
  python main.py --topic "PageRank algorithm"
  python main.py --topic "TCP handshake"

Key design:
  - Topic classifier decides the VISUALIZATION PATTERN best suited to the topic.
  - Each pattern has its own system prompt additions and semantic test suite.
  - All patterns share a universal state-machine contract (steps[], stepIdx).
  - Playwright tests are assembled dynamically from universal + pattern-specific checks.

Prerequisites:
    pip install openai python-dotenv playwright
    playwright install chromium

Fixes applied (v4.1):
  CRITICAL-1  : _run_npm_install return value now checked; fatal on failure.
  CRITICAL-2  : Preview port randomised per-run to avoid EADDRINUSE conflicts.
  CRITICAL-3  : Popen stdout/stderr redirected to DEVNULL (pipe buffer deadlock fix).
  CRITICAL-4  : test variable shadow fixed; failed_test appended explicitly.
  LOGICAL-5   : build_error_loop propagates timeout/failure flag; main skips runtime.
  LOGICAL-6   : runtime_fix_loop feeds build errors back to LLM instead of breaking.
  LOGICAL-7   : classify_topic strips quotes/punctuation/prose from LLM response.
  LOGICAL-8   : enforce_pinned_deps handles >=, >, <=, *, workspace: ranges.
  LOGICAL-9   : Preview startup wait raised to 25 s; configurable constant added.
  LOGICAL-10  : Screenshot named {topic_slug}_screenshot.png to prevent overwrite.
  MINOR-1     : llm_call has a 120 s HTTP timeout.
  MINOR-2     : LLM output dump written to project_dir (or /tmp fallback).
  MINOR-3     : format_files_for_prompt warns when prompt may exceed context.
  MINOR-4     : Closing fence regex accepts optional trailing language tag.
  MINOR-5     : INTERACTION_SETTLE raised to 1.2 s for slow framer-motion.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing: pip install python-dotenv")
    sys.exit(1)

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        OpenAI,
        RateLimitError,
    )
except ImportError:
    print("Missing: pip install openai python-dotenv playwright")
    sys.exit(1)

# (LangSmith tracing setup moved to backend/viz_generator/llm.py in Stage 2 Task 2)


# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viz_agent")


# ─────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────

# ── LLM machinery moved to backend/viz_generator/llm.py (Task 2, Stage 2) ──
# LLM_PROVIDER, DEFAULT_MODELS, MODEL_NAME, TOKEN_BUDGET,
# LLM_DEFAULT_TEMPERATURE, LLM_DEFAULT_MAX_TOKENS, LLM_FIX_MAX_TOKENS,
# PRICE_PER_1K_TOKENS, _GEMINI_PRICES, REASONING_EFFORT,
# _is_reasoning_model, _REASONING_MODEL_PREFIXES,
# TokenUsageTracker, token_tracker, status,
# _init_client, _get_client, llm_call
# are all re-imported below for back-compat while Stage 2 progresses.
from backend.llm import LLMTask  # noqa: F401 — for Stage 2 call sites
from backend.llm import is_reasoning_model  # noqa: F401 — used indirectly via _is_reasoning_model
from backend.viz_generator.llm import (
    LLM_PROVIDER,
    DEFAULT_MODELS,
    MODEL_NAME,
    TOKEN_BUDGET,
    LLM_DEFAULT_TEMPERATURE,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_FIX_MAX_TOKENS,
    PRICE_PER_1K_TOKENS,
    _GEMINI_PRICES,
    _REASONING_MODEL_PREFIXES,
    REASONING_EFFORT,
    _is_reasoning_model,
    TokenUsageTracker,
    token_tracker,
    status,
    _init_client,
    _get_client,
    llm_call,
)

# RUNTIME_RETRIES moved to backend/viz_generator/phases/runtime_loop.py (Task 9, Stage 2)

# These constants now live in the dedicated sub-modules; re-imported here
# for backward compatibility while Stage 2 modularization progresses.
from backend.viz_generator.files import (
    ERROR_DISPLAY_MAX_LINES,
    ALLOWED_FILE_EXTENSIONS,
    _filter_bogus_files,
    _validate_filepath,
    write_to_disk,
    enforce_pinned_deps,
    print_error_block,
)
from backend.viz_generator.parsing import (
    PROMPT_SIZE_WARN_CHARS,
    _BOGUS_STANDALONE,
    _FILENAME_HINT,
    parse_files,
    _parse_marker_format,
    _clean_file_content,
    _parse_codeblock_format,
    _extract_filename,
    format_files_for_prompt,
)
from backend.viz_generator.topic import classify_topic
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


# ─────────────────────────────────────────────────────────────
# TOPIC TAXONOMY
# ─────────────────────────────────────────────────────────────



# ─────────────────────────────────────────────────────────────
# UNIVERSAL RUNTIME TESTS (now live in backend/viz_generator/screenshot.py)
# Re-imported above via run_semantic_checks.
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# POLISH SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

# Moved to backend/viz_generator/phases/polish.py (Task 10, Stage 2)


# ─────────────────────────────────────────────────────────────
# SETUP — deferred until main() to avoid import-time side effects
# _client, TokenUsageTracker, token_tracker, status, _init_client,
# _get_client are all imported from backend.viz_generator.llm above.
# ─────────────────────────────────────────────────────────────




# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────
# parse_files, _parse_marker_format, _clean_file_content,
# _parse_codeblock_format, _extract_filename, format_files_for_prompt,
# _FILENAME_HINT, _BOGUS_STANDALONE, PROMPT_SIZE_WARN_CHARS
#   → now live in backend/viz_generator/parsing.py (re-imported above)
#
# _filter_bogus_files, _validate_filepath, write_to_disk,
# enforce_pinned_deps, print_error_block, ERROR_DISPLAY_MAX_LINES,
# ALLOWED_FILE_EXTENSIONS
#   → now live in backend/viz_generator/files.py (re-imported above)

# llm_call is imported from backend.viz_generator.llm above.


# ─────────────────────────────────────────────────────────────
# TARGETED-PATCH HELPERS  —  minimise tokens on retry loops
# ─────────────────────────────────────────────────────────────
#
# The original loops sent the FULL codebase back to the LLM every retry.
# For a 9-file project this is 30-60K input tokens × N retries — runaway cost.
#
# These helpers select ONLY the files relevant to the current error and ask
# the LLM to return MINIMAL surgical patches, not the entire codebase.

# File-selection helpers moved to backend/viz_generator/select.py (Stage 2, Task 4)
from backend.viz_generator.select import (
    select_relevant_files,
    format_files_compact,
    merge_patches,
)


# ─────────────────────────────────────────────────────────────
# STEP 1 — GENERATION
# ─────────────────────────────────────────────────────────────
# generate_draft_code moved to backend/viz_generator/phases/draft.py (Task 7, Stage 2)

from backend.viz_generator.phases.draft import generate_draft_code
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT


# ─────────────────────────────────────────────────────────────
# STEP 2 — BUILD ERROR LOOP
# build_error_loop moved to backend/viz_generator/phases/build_loop.py (Task 8, Stage 2)
# ─────────────────────────────────────────────────────────────

from backend.viz_generator.phases.build_loop import build_error_loop


# ─────────────────────────────────────────────────────────────
# STEP 3 — SEMANTIC RUNTIME VALIDATION
# runtime_fix_loop moved to backend/viz_generator/phases/runtime_loop.py (Task 9, Stage 2)
# Functions moved to backend/viz_generator/screenshot.py:
# _playwright_available, run_semantic_checks, _evaluate_assertion
# Re-imported above for back-compat.
# ─────────────────────────────────────────────────────────────

from backend.viz_generator.phases.runtime_loop import runtime_fix_loop


# ─────────────────────────────────────────────────────────────
# STEP 4 — OPTIONAL DESIGN POLISH
# design_polish_pass moved to backend/viz_generator/phases/polish.py (Task 10, Stage 2)
# ─────────────────────────────────────────────────────────────

from backend.viz_generator.phases.polish import design_polish_pass


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal Visualization Builder (v4.2) — works for any CS/ML/math topic"
    )
    parser.add_argument("--topic", required=True,
                        help="Topic to visualise (e.g. 'gradient descent', 'AVL tree', 'TCP handshake')")
    parser.add_argument("--polish", action="store_true",
                        help="Run design polish pass after validation")
    args = parser.parse_args()

    _init_client()

    log.info("=" * 60)
    log.info("  Universal Viz Agent v4.3 — '%s'", args.topic)
    log.info("=" * 60)

    log.info("\n[Classify] Identifying visualization pattern...")
    pattern_name, pattern = classify_topic(args.topic)

    safe_name = re.sub(r"[^a-z0-9]+", "-", args.topic.lower()).strip("-") + "-viz"
    project_dir = Path.cwd() / safe_name
    topic_slug = safe_name

    log.info("[Config] Token budget: %d  Model: %s  Provider: %s",
             TOKEN_BUDGET, MODEL_NAME, LLM_PROVIDER)

    # 1. Generate
    status("STEP 1 / 4", "GENERATE DRAFT CODE")
    files = generate_draft_code(args.topic, pattern_name, pattern, project_dir)
    write_to_disk(project_dir, files)

    # 2. Build loop
    files, build_ok = build_error_loop(args.topic, pattern, project_dir, files)

    # 3. Semantic runtime loop
    if build_ok:
        files = runtime_fix_loop(
            args.topic, pattern_name, pattern, project_dir, files, topic_slug=topic_slug
        )
    else:
        log.info("\n[Step 3] Skipping runtime validation — build did not succeed.")

    # 4. Polish (optional)
    if args.polish:
        if not build_ok:
            log.info("\n[Step 4] Skipping polish — build did not succeed.")
        else:
            status("STEP 4 / 4", "DESIGN POLISH")
            files = design_polish_pass(args.topic, pattern_name, project_dir, files)
            polish_r = _run_npm_build(project_dir)
            if polish_r is None:
                log.info("  ❌ Post-polish rebuild timed out.")
            elif polish_r.returncode != 0:
                log.info("  ❌ Post-polish rebuild failed.")
                print_error_block("Post-polish build error", polish_r.stderr or polish_r.stdout)
            else:
                log.info("  ✅ Post-polish build OK")

    # Final summary
    log.info("\n" + "=" * 60)
    log.info("  DONE!")
    log.info("  Topic:   %s", args.topic)
    log.info("  Pattern: %s", pattern_name)
    log.info("  Project: %s", project_dir)
    log.info("\n  Run:  cd %s && npm run dev", safe_name)
    shot = project_dir / f"{topic_slug}_screenshot.png"
    if shot.exists():
        log.info("  Screenshot: %s", shot)
    log.info("=" * 60)

    # ── Token usage / cost summary ──
    token_tracker.print_summary()


if __name__ == "__main__":
    main()
