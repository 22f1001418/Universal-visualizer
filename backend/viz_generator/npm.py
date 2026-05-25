"""npm + port management for the viz generator.

Runs `npm install` and `npm run build` in a project directory, picks a
free port for the preview dev server, and waits for the server to become
reachable.
"""
from __future__ import annotations

import logging
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

from backend.viz_generator.files import print_error_block

log = logging.getLogger("viz_agent")

# ─────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────

SUBPROCESS_TIMEOUT: int = 180           # seconds — npm install/build hard cap
PREVIEW_STARTUP_WAIT: float = 25.0     # seconds — max wait for vite preview port


def _run_npm_install(project_dir: Path) -> bool:
    """Run npm install with timeout. Returns True on success."""
    try:
        install_r = subprocess.run(
            ["npm", "install"], cwd=project_dir,
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("  ❌ npm install timed out after %ds.", SUBPROCESS_TIMEOUT)
        return False

    if install_r.returncode != 0:
        log.info("  ❌ npm install failed.")
        print_error_block("npm install error", install_r.stderr or install_r.stdout)
        return False

    warn_text = (install_r.stderr or "").strip()
    if warn_text and ("warn" in warn_text.lower() or "vulnerab" in warn_text.lower()):
        log.info("  ⚠️  npm install completed with warnings (first lines):")
        for line in warn_text.splitlines()[:5]:
            log.info("    %s", line)
    return True


def _run_npm_build(project_dir: Path) -> subprocess.CompletedProcess[str] | None:
    """Run npm build with timeout. Returns CompletedProcess or None on timeout."""
    try:
        return subprocess.run(
            ["npm", "run", "build"], cwd=project_dir,
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("  ❌ npm run build timed out after %ds.", SUBPROCESS_TIMEOUT)
        return None


def _pick_free_port(start: int = 5100, end: int = 5900) -> int:
    """CRITICAL-2: Pick a random free port to avoid EADDRINUSE conflicts."""
    for _ in range(20):
        port = random.randint(start, end)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("Could not find a free port in range %d-%d" % (start, end))


def _wait_for_server(port: int, timeout: float = PREVIEW_STARTUP_WAIT) -> bool:
    """Poll until the preview server responds, up to `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False
