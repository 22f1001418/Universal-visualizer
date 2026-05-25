"""Token usage tracker — process-lifetime totals and per-job buckets.

Behaviour preserved from llm_client.TokenUsageTracker (the original):
  - Thread-safe via a single Lock.
  - Records input/output/reasoning tokens per call, accumulates per job_id.
  - Raises RuntimeError when a job exceeds budget_per_job.
  - Emits a [Tokens] log line on every record() call (same format as before).

Differences from the original:
  - Pricing pulled from backend.llm.pricing (no duplicate table).
  - budget_per_job is a constructor arg (was a module global). The module-level
    `token_tracker` singleton reads its budget from env at import time.
"""
from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Optional

from backend.llm.pricing import cost_usd

logger = logging.getLogger("hackmd-orch.llm")

_DEFAULT_BUDGET = int(os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))


class TokenUsageTracker:
    def __init__(self, budget_per_job: int = _DEFAULT_BUDGET) -> None:
        self.budget_per_job = budget_per_job
        self.total_input = 0
        self.total_output = 0
        self.total_calls = 0
        self._per_job: dict[str, dict] = {}
        self._lock = Lock()

    def record(
        self,
        step_label: str,
        input_tokens: int,
        output_tokens: int,
        job_id: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_tokens: int = 0,
    ) -> None:
        cost = cost_usd(input_tokens, output_tokens, model)

        with self._lock:
            self.total_input += input_tokens
            self.total_output += output_tokens
            self.total_calls += 1
            job_total = 0
            if job_id is not None:
                bucket = self._per_job.setdefault(
                    job_id, {"input": 0, "output": 0, "calls": 0, "reasoning": 0, "cost": 0.0}
                )
                bucket["input"] += input_tokens
                bucket["output"] += output_tokens
                bucket["calls"] += 1
                bucket["reasoning"] += reasoning_tokens
                # Accumulate cost per call (each call may use a different model under LLMTask routing).
                bucket["cost"] = bucket.get("cost", 0.0) + cost
                job_total = bucket["input"] + bucket["output"]

        model_tag = (model or "?")[:18]

        if job_id is not None:
            pct = (job_total / self.budget_per_job) * 100 if self.budget_per_job else 0
            logger.info(
                "[Tokens] %-30s [%s] in=%-6d out=%-5d cost=$%.4f  job=%d/%d (%.0f%%)",
                step_label, model_tag, input_tokens, output_tokens, cost,
                job_total, self.budget_per_job, pct,
            )
            if reasoning_tokens > 0 and output_tokens > 0:
                visible = max(output_tokens - reasoning_tokens, 0)
                logger.info(
                    "  [Reasoning] hidden=%d  visible=%d  ratio=%.0f%%",
                    reasoning_tokens, visible,
                    (reasoning_tokens / output_tokens) * 100,
                )
            if job_total > self.budget_per_job:
                raise RuntimeError(
                    f"Job {job_id} exceeded token budget: "
                    f"{job_total} > {self.budget_per_job}. Aborting."
                )
        else:
            logger.info(
                "[Tokens] %-30s [%s] in=%-6d out=%-5d cost=$%.4f",
                step_label, model_tag, input_tokens, output_tokens, cost,
            )

    def job_summary(self, job_id: str) -> dict:
        with self._lock:
            # Copy the bucket inside the lock so concurrent record() calls
            # can't mutate it between us reading and returning.
            bucket = dict(self._per_job.get(
                job_id, {"input": 0, "output": 0, "calls": 0, "reasoning": 0, "cost": 0.0}
            ))
        return {
            "calls": bucket["calls"],
            "input_tokens": bucket["input"],
            "output_tokens": bucket["output"],
            "reasoning_tokens": bucket.get("reasoning", 0),
            "total_tokens": bucket["input"] + bucket["output"],
            "estimated_cost_usd": round(bucket.get("cost", 0.0), 4),
        }


# Module-level singleton — same as the old llm_client.token_tracker.
token_tracker = TokenUsageTracker()
