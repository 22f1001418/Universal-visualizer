from __future__ import annotations

import pytest

from backend.llm.tracker import TokenUsageTracker


def test_record_accumulates_per_job():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 50, job_id="j1", model="gpt-4o-mini")
    t.record("step_b", 200, 75, job_id="j1", model="gpt-4o-mini")
    s = t.job_summary("j1")
    assert s["calls"] == 2
    assert s["input_tokens"] == 300
    assert s["output_tokens"] == 125
    assert s["total_tokens"] == 425
    assert s["estimated_cost_usd"] > 0


def test_record_without_job_id_does_not_raise():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 50, job_id=None, model="gpt-4o-mini")
    # Empty job_summary for unknown job
    s = t.job_summary("nonexistent")
    assert s["calls"] == 0
    assert s["total_tokens"] == 0


def test_budget_overrun_raises():
    t = TokenUsageTracker(budget_per_job=500)
    t.record("step_a", 200, 200, job_id="j1", model="gpt-4o-mini")  # 400 — OK
    with pytest.raises(RuntimeError, match="exceeded token budget"):
        t.record("step_b", 100, 100, job_id="j1", model="gpt-4o-mini")  # 600 total — over


def test_reasoning_tokens_recorded():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 200, job_id="j1", model="gpt-5", reasoning_tokens=150)
    s = t.job_summary("j1")
    assert s["reasoning_tokens"] == 150


def test_tracker_singleton_importable():
    from backend.llm.tracker import token_tracker
    assert token_tracker is not None
