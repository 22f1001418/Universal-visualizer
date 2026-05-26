"""Draft phase: one LLM call → validate → optional one-shot fix.

Public surface: run_draft_phase(topic, brief, project_dir) -> DraftResult.
Fix-loop policy (one iteration max) lives here, NOT in validator.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from backend.llm import LLMTask
from backend.viz_generator.events import log_event
from backend.viz_generator.files import (
    extract_html, pre_validate_html, print_error_block,
)
from backend.viz_generator.llm import llm_call
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT
from backend.viz_generator.validator import ValidationResult, validate

log = logging.getLogger("viz_agent")

# Per-call output budget. A single-file vanilla viz typically lands at 2-5k
# output tokens; 6k is comfortable headroom. Truncation at this limit means
# we should fail fast rather than ship a half-file. Override via env.
MODEL_VIZ_DRAFT_MAX_TOKENS: int = int(os.getenv("MODEL_VIZ_DRAFT_MAX_TOKENS", "6000"))
MODEL_VIZ_FIX_MAX_TOKENS: int = int(os.getenv("MODEL_VIZ_FIX_MAX_TOKENS", "3000"))


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


def _validate_with_preflight(html: str, project_dir: Path) -> ValidationResult:
    """Run cheap pre-flight checks; only launch Chromium if they pass.

    Saves ~2-3s of browser startup on obviously-broken generations
    (truncation, missing tags, empty <script>).
    """
    problems = pre_validate_html(html)
    if problems:
        msg = "pre-validate: " + "; ".join(problems)
        log_event("validate", "preflight_fail", problems=problems)
        return ValidationResult(success=False, error_log=msg)
    return validate(html, project_dir)


def run_draft_phase(topic: str, brief: str, project_dir: Path) -> DraftResult:
    """Generate + validate the initial viz. One fix-loop iteration on failure."""
    log.info("[draft] generating initial HTML for '%s'...", topic)
    log_event("draft", "start", topic=topic)
    html = _llm_call_draft(topic, brief)

    log.info("[validate] first attempt...")
    result = _validate_with_preflight(html, project_dir)
    if result.success:
        log.info("  ✅ draft validated on first attempt.")
        log_event("draft", "success", attempts=1)
        return DraftResult(
            success=True, html=html, attempts=1,
            screenshot_path=result.screenshot_path,
        )

    print_error_block("Draft validation errors", result.error_log)
    log_event("draft", "fix_iteration", error=result.error_log[:200])
    log.info("[draft] running one fix iteration...")
    html_fixed = _llm_call_fix(topic, html, result.error_log)

    log.info("[validate] post-fix attempt...")
    result2 = _validate_with_preflight(html_fixed, project_dir)
    if result2.success:
        log.info("  ✅ draft validated after one fix iteration.")
        log_event("draft", "success", attempts=2)
        return DraftResult(
            success=True, html=html_fixed, attempts=2,
            screenshot_path=result2.screenshot_path,
        )

    log.info("  ❌ draft failed after one fix iteration.")
    print_error_block("Post-fix validation errors", result2.error_log)
    log_event("draft", "failed", attempts=2, error=result2.error_log[:200])
    return DraftResult(
        success=False, html=html_fixed, attempts=2,
        error_log=result2.error_log,
    )
