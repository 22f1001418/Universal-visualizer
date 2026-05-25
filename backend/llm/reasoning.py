"""Reasoning-model detection for OpenAI gpt-5 / o-series.

Reasoning models require:
  - max_completion_tokens (not max_tokens)
  - reasoning_effort kwarg
  - omitted temperature/top_p (they reject these)
This module is the single place that knows the prefix list.
"""
from __future__ import annotations

REASONING_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str | None) -> bool:
    if not model:
        return False
    name = model.lower()
    return any(name.startswith(p) for p in REASONING_PREFIXES)
