"""Import-linter contract enforcement runs in the test suite.

Fails the build if any forbidden cross-package import appears.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Repo root is two levels up from this file (tests/contract/)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_lint_imports_contract_passes():
    r = subprocess.run(
        [sys.executable, "-c",
         "from importlinter.cli import lint_imports_command; import sys; sys.exit(lint_imports_command())"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    if r.returncode != 0:
        print("lint-imports stdout:\n" + r.stdout)
        print("lint-imports stderr:\n" + r.stderr)
    assert r.returncode == 0, "import-linter contract violation"
