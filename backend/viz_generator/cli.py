"""CLI entry point for the viz generator subprocess.

The argv contract (--topic, --polish) is unchanged from the original
fixed_main_v6.py main(). The orchestrator (backend/orchestrator.py)
spawns this via the FIXED_MAIN_PATH env var which still points at
fixed_main_v6.py — that file is now a 2-line stub that calls back here.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path

from backend.viz_generator.llm import (
    LLM_PROVIDER,
    MODEL_NAME,
    TOKEN_BUDGET,
    token_tracker,
    status,
    _init_client,
)
from backend.viz_generator.files import (
    write_to_disk,
    print_error_block,
)
from backend.viz_generator.npm import _run_npm_build
from backend.viz_generator.topic import classify_topic
from backend.viz_generator.phases.draft import generate_draft_code
from backend.viz_generator.phases.build_loop import build_error_loop
from backend.viz_generator.phases.runtime_loop import runtime_fix_loop
from backend.viz_generator.phases.polish import design_polish_pass


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
# ARGPARSE
# ─────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse parser used by main().

    Exposed so contract tests can assert the flag set without invoking the
    subprocess. The orchestrator calls this via subprocess + argv anyway.
    """
    p = argparse.ArgumentParser(
        description="Universal Visualization Builder (v4.2) — works for any CS/ML/math topic"
    )
    p.add_argument(
        "--topic",
        required=True,
        help="Topic to visualise (e.g. 'gradient descent', 'AVL tree', 'TCP handshake')",
    )
    p.add_argument(
        "--polish",
        action="store_true",
        help="Run design polish pass after validation",
    )
    return p


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    args = build_parser().parse_args()

    _init_client()

    log.info("=" * 60)
    log.info("  Universal Viz Agent v4.3 — '%s'", args.topic)
    log.info("=" * 60)

    log.info("\n[Classify] Identifying visualization pattern...")
    pattern_name, pattern = classify_topic(args.topic)

    safe_name = re.sub(r"[^a-z0-9]+", "-", args.topic.lower()).strip("-") + "-viz"
    project_dir = Path.cwd() / safe_name
    topic_slug = safe_name

    log.info(
        "[Config] Token budget: %d  Model: %s  Provider: %s",
        TOKEN_BUDGET, MODEL_NAME, LLM_PROVIDER,
    )

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

    # Token usage / cost summary
    token_tracker.print_summary()


if __name__ == "__main__":
    main()
