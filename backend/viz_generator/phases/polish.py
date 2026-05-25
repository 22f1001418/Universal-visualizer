"""Step 4 of the viz pipeline (optional, --polish flag): a final LLM pass
that improves visual design — typography, spacing, color, motion — on the
already-working viz.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.llm import LLMTask
from backend.viz_generator.llm import llm_call
from backend.viz_generator.parsing import parse_files, format_files_for_prompt
from backend.viz_generator.files import write_to_disk
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT

log = logging.getLogger("viz_agent")

POLISH_SYSTEM_PROMPT = """You are a senior UI designer who refines React visualisations.
You must NOT change any algorithm logic, state machine, or step generation code.
Only improve: colors, typography, spacing, transitions, layout, labels, and aesthetic polish.
"""


def design_polish_pass(
    topic: str,
    pattern_name: str,
    project_dir: Path,
    files: dict[str, str],
) -> dict[str, str]:
    log.info("\n[Step 4] Design polish pass...")

    polish_prompt = f"""Polish the visual design of this "{topic}" visualisation ({pattern_name} pattern).

Design direction:
- Dark background: #0a0e1a. Card surfaces: #111827.
- Primary accent: #6366f1 (indigo). Active/highlight: #f59e0b (amber). Done/sorted: #10b981 (emerald).
- Monospace font for values/numbers, clean sans-serif for labels.
- Smooth CSS transitions (150-200ms) on all color and size changes.
- Pill-shaped control buttons with hover states (scale 1.02, brightness 1.1).
- Responsive layout: bars/nodes sized with % or SVG viewBox, not fixed px.
- Step message area with a subtle border and monospace font.

DO NOT modify: steps[] generation, stepIdx logic, any algorithm/math code, data structures.
Only touch: className attributes, inline styles, SVG colors/sizes, layout wrapper divs, transitions.

<codebase>
{format_files_for_prompt(files)}
</codebase>

Output FULL content of every changed file.
"""

    raw = llm_call(
        [
            {"role": "system", "content": POLISH_SYSTEM_PROMPT},
            {"role": "user",   "content": polish_prompt},
        ],
        temperature=1,
        step_label="step4_polish",
        task=LLMTask.VIZ_POLISH,
    )

    fixed = parse_files(raw)
    if fixed:
        files.update(fixed)
        write_to_disk(project_dir, fixed)
        log.info("  ✅ Polish applied (%d file(s)).", len(fixed))
    else:
        log.info("  ⚠️  No files returned.")
    return files
