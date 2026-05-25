"""Regression guard: each viz phase declares the right LLMTask in its
llm_call invocations. Catches accidental removal of task= during future
refactors.
"""
from __future__ import annotations

from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_BASE = _PROJECT_ROOT / "backend" / "viz_generator"


def test_each_phase_uses_expected_task():
    expected = {
        "phases/draft.py":        "LLMTask.VIZ_DRAFT",
        "phases/build_loop.py":   "LLMTask.VIZ_BUILD_FIX",
        "phases/runtime_loop.py": "LLMTask.VIZ_RUNTIME_FIX",
        "phases/polish.py":       "LLMTask.VIZ_POLISH",
        "topic.py":               "LLMTask.VIZ_TOPIC_CLASSIFY",
    }
    for rel, expected_token in expected.items():
        text = (_BASE / rel).read_text()
        assert expected_token in text, f"{rel} is missing {expected_token}"
