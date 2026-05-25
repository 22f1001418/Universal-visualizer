"""Step 1 of the viz pipeline: generate the initial multi-file React+Vite
project from the topic + brief.

The most expensive LLM call in the viz pipeline — generates ~10-30 files
of React/TypeScript in one shot. Stage 2 Task 13 will add
task=LLMTask.VIZ_DRAFT routing so this defaults to gpt-4o.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from backend.viz_generator.llm import LLM_DEFAULT_MAX_TOKENS, llm_call
from backend.viz_generator.parsing import parse_files
from backend.viz_generator.files import enforce_pinned_deps
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT

log = logging.getLogger("viz_agent")


def generate_draft_code(
    topic: str,
    pattern_name: str,
    pattern: dict[str, Any],
    project_dir: Path,
) -> dict[str, str]:
    """Generate initial multi-file React+Vite project from topic + pattern.

    Uses the most expensive LLM call in the viz pipeline to generate ~10-30 files
    of React/TypeScript in one shot.

    Args:
        topic: The visualization topic (e.g., "binary search tree insertion").
        pattern_name: The visualization pattern name (e.g., "ArrayGrid").
        pattern: Dict containing pattern config, including "prompt_extra" for pattern-specific guidelines.
        project_dir: Path where the generated project will be written to disk.

    Returns:
        Dict mapping filename -> file contents. All dependencies pinned to exact semver.

    Raises:
        SystemExit: If LLM returns unparseable output (logs raw output to /tmp fallback).
    """
    log.info("\n[Step 1] Generating '%s' as pattern '%s'...", topic, pattern_name)

    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    user_prompt = f"""Generate a complete, working Vite + React visualisation for:

TOPIC: "{topic}"

Requirements:
- Follow the Universal State-Machine Contract from the system prompt.
- Follow the '{pattern_name}' visualization pattern guidelines.
- The visualization must be educational: a student should understand the concept
  by watching it animate step by step.
- Default to a non-trivial example (e.g. 8-12 elements for algorithms,
  3-4 layers for neural nets, 5-6 nodes for graphs).
- Include a brief text explanation of what is happening at the current step.

Required files to produce (ALL must be present):
  src/App.tsx  src/main.tsx  src/index.css
  index.html  (at project root — NOT src/index.html)
  vite.config.ts  tsconfig.json  tsconfig.node.json
  tailwind.config.js  postcss.config.js  package.json
  src/hooks/useAnimation.ts  src/store/useStore.ts

Also produce these component files (src/components/):
  Sidebar.tsx  PageShell.tsx  Panel.tsx  AnimControls.tsx  StepExplainer.tsx
"""

    raw = llm_call(
        [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=1,
        max_tokens=LLM_DEFAULT_MAX_TOKENS,
        step_label="step1_generate",
    )

    files = parse_files(raw)
    if not files:
        # MINOR-2: dump to project_dir (which exists) with /tmp fallback
        dump_path: Path
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            dump_path = project_dir / "last_llm_output.txt"
        except OSError:
            dump_path = Path("/tmp/last_llm_output.txt")
        dump_path.write_text(raw, encoding="utf-8")
        log.error("[ERROR] No parseable files from LLM.")
        log.error("  Raw output saved to: %s", dump_path)
        log.error("  Output length: %d chars", len(raw))
        log.error("  First 500 chars of response:")
        log.error("  %s", raw[:500].replace("\n", "\n  "))
        sys.exit(1)

    return enforce_pinned_deps(files)
