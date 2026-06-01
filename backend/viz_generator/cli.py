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

from backend.config import settings
from backend.llm.client import get_client
from backend.llm.tracker import token_tracker
from backend.viz_generator.files import write_html_to_disk
from backend.viz_generator.phases.draft import run_draft_phase
from backend.viz_generator.phases.polish import run_polish_phase
from backend.viz_generator.topic import classify_topic

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viz_agent")


def status(stage: str, detail: str = "") -> None:
    """Print a clearly-marked status line to stdout.

    The orchestrator's stdout parser keys on these [STATUS] markers for SPA
    progress-bar updates, so the exact output format must not change.
    """
    bar = "─" * 4
    if detail:
        log.info("\n%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        log.info("\n%s [STATUS] %s %s", bar, stage, bar)


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

    get_client()  # lazily initialise the OpenAI client + log readiness

    log.info("=" * 60)
    log.info("  Vanilla Viz Agent — '%s'", args.topic)
    log.info("=" * 60)

    status("STEP 1", "CLASSIFY TOPIC")
    pattern_name, _pattern = classify_topic(args.topic)

    slug = _slug_for_topic(args.topic)
    project_dir = Path.cwd() / slug

    log.info(
        "[Config] Token budget: %d  Default model: %s  (per-task routing via LLMTask)",
        settings.token_budget_per_job, settings.openai_text_model,
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
