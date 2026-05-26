"""Subprocess orchestrator — runs fixed_main_v6.py for a single topic build.

Why subprocess and not direct import?
  - fixed_main_v6.py is a CLI tool with its own logging, sys.exit() calls,
    and global state (token tracker, Playwright browser). Importing it would
    let multiple concurrent builds stomp on each other.
  - Subprocess gives us process isolation, clean log capture, and a simple
    way to surface real-time progress to the frontend.
  - It also lets the operator tweak FIXED_MAIN_PATH or run a different
    version of the viz generator without restarting the orchestrator.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys

from backend.config import settings
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("hackmd-orch.runner")


# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# Path to the viz-generator CLI. Override via FIXED_MAIN_PATH in .env.
FIXED_MAIN_PATH = Path(settings.fixed_main_path).resolve()

# Where the generated Vite projects should be created.
# fixed_main_v6.py creates them in cwd, so we cd into VIZ_OUTPUT_DIR before running.
VIZ_OUTPUT_DIR = Path(settings.viz_output_dir).resolve()
VIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Max seconds the build is allowed to run end-to-end.
BUILD_TIMEOUT_SECONDS = settings.build_timeout_seconds


# ──────────────────────────────────────────────
# Phase detection — map fixed_main output lines to BuildPhase
# ──────────────────────────────────────────────

_PHASE_PATTERNS: list[tuple[str, str]] = [
    (r"\[STATUS\]\s*STEP\s*1",         "draft"),    # classify is part of draft phase from SPA POV
    (r"\[STATUS\]\s*STEP\s*2",         "draft"),
    (r"\[draft\]",                      "draft"),
    (r"\[validate\]",                   "validate"),
    (r"\[STATUS\]\s*STEP\s*3",         "polish"),
    (r"\[polish\]",                     "polish"),
    (r"\bDONE!",                        "done"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), phase) for p, phase in _PHASE_PATTERNS]


def detect_phase_from_line(line: str) -> Optional[str]:
    for pat, phase in _COMPILED:
        if pat.search(line):
            return phase
    return None


# ──────────────────────────────────────────────
# Project-dir detection from the slug
# ──────────────────────────────────────────────

# ──────────────────────────────────────────────
# Project-dir detection
# ──────────────────────────────────────────────

def _slug_for_topic(topic: str, max_len: int = 60) -> str:
    """Mirrors the slug logic at the bottom of fixed_main_v6.py main(),
    PLUS a hard length cap so we never produce a directory name longer
    than the filesystem can hold. Keep this in sync with what
    fixed_main_v6.py does — but the orchestrator no longer relies on
    this matching exactly (see _snapshot_dirs / _find_new_dir below)."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return f"{slug}-viz"


def _snapshot_dirs(parent: Path) -> set[str]:
    """Return the set of directory NAMES currently inside parent.
    Files are ignored. Used for before/after diffing."""
    if not parent.exists():
        return set()
    return {p.name for p in parent.iterdir() if p.is_dir()}


def _find_new_dir(
    parent: Path,
    before: set[str],
    expected_slug_hint: str | None = None,
) -> Path | None:
    """After a build, find the directory the subprocess created.

    Strategy:
      1. Diff against `before` snapshot — pick whatever is new.
      2. If multiple new dirs (rare — concurrent builds), prefer one that
         starts with the slug-hint, else newest by mtime.
      3. If zero new dirs, fall back to the dir with the most recent mtime
         that starts with the slug-hint.
    """
    if not parent.exists():
        return None

    after = {p.name: p for p in parent.iterdir() if p.is_dir()}
    new_names = set(after.keys()) - before

    if len(new_names) == 1:
        return after[next(iter(new_names))]

    if len(new_names) > 1:
        candidates = [after[n] for n in new_names]
        if expected_slug_hint:
            preferred = [c for c in candidates if c.name.startswith(expected_slug_hint[:30])]
            if preferred:
                preferred.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                return preferred[0]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return candidates[0]

    # No new dirs — last-resort match by hint + mtime (covers the case where
    # the dir already existed from a previous run and was reused).
    if expected_slug_hint:
        hint = expected_slug_hint[:30]
        candidates = [p for n, p in after.items() if n.startswith(hint)]
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return candidates[0]

    return None


# ──────────────────────────────────────────────
# Run a build
# ──────────────────────────────────────────────

class BuildResult:
    def __init__(self) -> None:
        self.success: bool = False
        self.project_dir: str = ""
        self.screenshot_path: str = ""
        self.exit_code: int = -1
        self.last_phase: str = "queued"
        self.error: str = ""
        self.stdout_tail: list[str] = []        # last 200 lines, for debugging
        self.completed_at: Optional[datetime] = None


def run_viz_build(
    topic_brief: str,
    on_log: Callable[[str], None] | None = None,
    on_phase_change: Callable[[str], None] | None = None,
    extra_env: Optional[dict[str, str]] = None,
) -> BuildResult:
    """Run fixed_main_v6.py for a single topic. Returns when the process exits.

    Args:
      topic_brief: the rich brief assembled from the suggestion + custom notes.
      on_log: callback invoked once per stdout line. Use it to push progress to UI.
      on_phase_change: callback invoked when we detect a phase transition.
      extra_env: extra env vars to merge on top of os.environ for the subprocess.

    Returns a BuildResult.
    """
    result = BuildResult()

    if not FIXED_MAIN_PATH.exists():
        result.error = f"fixed_main_v6.py not found at: {FIXED_MAIN_PATH}"
        result.last_phase = "failed"
        logger.error("[Builder] %s", result.error)
        return result

    # Snapshot existing project dirs BEFORE the subprocess runs. We diff
    # against this snapshot afterward to find whatever fixed_main_v6.py
    # actually created — without having to predict its slug logic.
    dirs_before = _snapshot_dirs(VIZ_OUTPUT_DIR)

    # Hint string for tiebreaking when multiple new dirs appear (rare).
    expected_slug_hint = _slug_for_topic(topic_brief)

    cmd = [sys.executable, str(FIXED_MAIN_PATH), "--topic", topic_brief, "--polish"]
    logger.info("[Builder] cwd=%s", VIZ_OUTPUT_DIR)
    logger.info("[Builder] cmd=%s", " ".join(cmd[:3] + [repr(topic_brief[:80])]))

    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    # Force unbuffered stdout from the child so we can stream progress live.
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(VIZ_OUTPUT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,   # merge so we don't lose order
            text=True,
            bufsize=1,
            env=env,
        )
    except FileNotFoundError as e:
        result.error = f"Could not exec viz generator: {e}"
        result.last_phase = "failed"
        logger.exception(result.error)
        return result

    last_phase = "queued"
    tail: list[str] = []
    start = datetime.utcnow()

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line:
                continue
            tail.append(line)
            if len(tail) > 200:
                tail = tail[-200:]
            if on_log is not None:
                try:
                    on_log(line)
                except Exception:   # ignore broken UI callback
                    pass

            phase = detect_phase_from_line(line)
            if phase and phase != last_phase:
                last_phase = phase
                if on_phase_change is not None:
                    try:
                        on_phase_change(phase)
                    except Exception:
                        pass

            if (datetime.utcnow() - start).total_seconds() > BUILD_TIMEOUT_SECONDS:
                logger.error("[Builder] timeout after %ds — killing process", BUILD_TIMEOUT_SECONDS)
                proc.kill()
                result.error = f"Build timed out after {BUILD_TIMEOUT_SECONDS}s"
                result.last_phase = "failed"
                break
    except Exception as e:
        logger.exception("[Builder] error tailing stdout: %s", e)
        result.error = f"stdout tail error: {e}"

    proc.wait()
    result.exit_code = proc.returncode
    result.last_phase = last_phase
    result.stdout_tail = tail
    result.completed_at = datetime.utcnow()

    # ── Discover what the subprocess actually produced (diff snapshot) ──
    project_dir_path = _find_new_dir(
        VIZ_OUTPUT_DIR,
        dirs_before,
        expected_slug_hint=expected_slug_hint,
    )

    if project_dir_path is not None and project_dir_path.exists():
        result.project_dir = str(project_dir_path)
        # fixed_main_v6.py screenshot file = "<slug>_screenshot.png" inside
        # the project dir. We don't know the exact slug it picked, so just
        # glob for "*_screenshot.png" and take the most recent.
        shots = sorted(
            project_dir_path.glob("*_screenshot.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        result.screenshot_path = str(shots[0]) if shots else ""
    else:
        result.project_dir = ""
        result.screenshot_path = ""
        logger.warning(
            "[Builder] No project directory found in %s after subprocess "
            "exited with code %d. Hint='%s', dirs_before=%d, dirs_after=%d.",
            VIZ_OUTPUT_DIR, proc.returncode,
            expected_slug_hint, len(dirs_before),
            len(_snapshot_dirs(VIZ_OUTPUT_DIR)),
        )

    # Heuristic: success if exit code 0 AND we found a project directory
    # AND the build reached at least the validate phase.
    if (
        proc.returncode == 0
        and result.project_dir
        and last_phase in ("done", "polish", "validate")
    ):
        result.success = True
    else:
        if not result.error:
            if not result.project_dir:
                result.error = (
                    f"Exit code {proc.returncode}, last phase {last_phase}, "
                    f"AND no new project directory was found in "
                    f"{VIZ_OUTPUT_DIR} (subprocess may have crashed before "
                    f"creating it). Tail: {tail[-3:] if tail else '(no output)'}"
                )
            else:
                result.error = (
                    f"Exit code {proc.returncode}, last phase {last_phase}. "
                    f"Tail: {tail[-3:] if tail else '(no output)'}"
                )

    return result
