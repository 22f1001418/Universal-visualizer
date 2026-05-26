"""Polish phase: one LLM call refines the working HTML's visual design.

Two fallback paths after the polished HTML comes back:
  1. Re-validate. If validation fails, ship pre-polish HTML.
  2. Structural integrity check (button/input counts must not drop by
     more than INTEGRITY_DROP_LIMIT). Catches the case where polish
     "passes validation" but quietly strips functionality.

Limitation: neither check detects pure aesthetic regression (worse colors,
worse layout that still works). Real fix requires a vision LLM judge or
manual review. Manual review is built into the plan (Task 22). A vision
judge is a follow-up.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from backend.llm import LLMTask
from backend.viz_generator.events import log_event
from backend.viz_generator.files import extract_html, write_html_to_disk
from backend.viz_generator.llm import llm_call
from backend.viz_generator.prompts import POLISH_RUBRIC, UNIVERSAL_SYSTEM_PROMPT
from backend.viz_generator.validator import validate

log = logging.getLogger("viz_agent")

MODEL_VIZ_POLISH_MAX_TOKENS: int = int(os.getenv("MODEL_VIZ_POLISH_MAX_TOKENS", "6000"))

# Max fraction of buttons/inputs polish is allowed to drop without
# triggering a fallback. 0.4 = if post has < 60% of pre's controls, revert.
INTEGRITY_DROP_LIMIT: float = 0.4


@dataclass(frozen=True)
class PolishResult:
    html: str            # final HTML actually written to disk
    polished: bool       # True if the polished HTML cleared validation + integrity
    fallback_used: bool  # True if we reverted to pre_html
    error_log: str = ""


def _count_tag(tag: str, html: str) -> int:
    return len(re.findall(rf"<{tag}\b", html, re.IGNORECASE))


def _structural_integrity_problem(pre_html: str, post_html: str) -> str:
    """Return a problem description if polish stripped too much; else ''."""
    for tag in ("button", "input"):
        pre = _count_tag(tag, pre_html)
        if pre == 0:
            continue
        post = _count_tag(tag, post_html)
        if post / pre < (1 - INTEGRITY_DROP_LIMIT):
            return f"structural regression: {tag}s dropped {pre} -> {post}"
    return ""


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
    """Refine pre_html visually; fall back to pre_html on validation OR structural regress."""
    log.info("[polish] refining design for '%s'...", topic)
    log_event("polish", "start", topic=topic)
    polished_html = _llm_call_polish(topic, pre_html)

    log.info("[polish] re-validating polished HTML...")
    result = validate(polished_html, project_dir)

    if not result.success:
        reason = f"validation: {result.error_log}"
        log.info("  ⚠️  polish regressed validation — reverting to pre-polish HTML.")
        return _fallback_to_pre(pre_html, project_dir, reason)

    integrity_problem = _structural_integrity_problem(pre_html, polished_html)
    if integrity_problem:
        log.info("  ⚠️  polish %s — reverting to pre-polish HTML.", integrity_problem)
        return _fallback_to_pre(pre_html, project_dir, integrity_problem)

    log.info("  ✅ polish applied + validated + structurally intact.")
    log_event("polish", "success")
    return PolishResult(html=polished_html, polished=True, fallback_used=False)


def _fallback_to_pre(pre_html: str, project_dir: Path, reason: str) -> PolishResult:
    """Rewrite pre_html to disk + re-screenshot so the shipped state is consistent."""
    log_event("polish", "fallback", reason=reason[:200])
    write_html_to_disk(project_dir, pre_html)
    validate(pre_html, project_dir)
    return PolishResult(
        html=pre_html, polished=False, fallback_used=True,
        error_log=reason,
    )
