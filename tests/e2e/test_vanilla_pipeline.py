"""End-to-end smoke for the vanilla viz CLI.

Hits real OpenAI; gated behind `pytest -m slow`. Run locally with
  pytest -m slow tests/e2e/test_vanilla_pipeline.py
Requires OPENAI_API_KEY in the environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow


def test_cli_produces_index_and_screenshot(tmp_path: Path):
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    r = subprocess.run(
        [sys.executable, "-m", "backend.viz_generator.cli",
         "--topic", "binary search", "--polish"],
        cwd=str(tmp_path),
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"stdout:\n{r.stdout}\n---stderr:\n{r.stderr}"

    # The CLI writes <slug>-viz/{index.html,screenshot.png} inside cwd.
    projects = [p for p in tmp_path.iterdir() if p.is_dir() and p.name.endswith("-viz")]
    assert len(projects) == 1, f"expected one viz dir, got: {projects}"
    viz = projects[0]
    assert (viz / "index.html").stat().st_size > 0
    assert (viz / "screenshot.png").stat().st_size > 0

    html = (viz / "index.html").read_text(encoding="utf-8")
    assert html.lower().startswith("<!doctype") or html.lower().startswith("<html")
