"""Compatibility shim — re-exports from backend.llm.

Originally hosted the LLM client implementation; that has moved into the
backend.llm package as part of Stage 1 of the modularization project.
This file is kept for backwards compatibility with existing imports
(`from llm_client import llm_call`) and is scheduled for removal at the
end of Stage 3, when all callers will have been migrated to import from
backend.llm directly.

See docs/superpowers/specs/2026-05-25-modularization-design.md for context.
"""
from backend.llm import (  # noqa: F401
    PERMANENT_STATUS,
    PRICE_PER_1K,
    REASONING_PREFIXES,
    RETRYABLE_STATUS,
    LLMTask,
    TokenUsageTracker,
    cost_usd,
    extract_openai_error,
    get_client,
    is_permanent_status,
    is_reasoning_model,
    is_retryable_status,
    llm_call,
    resolve_model,
    token_tracker,
)

# Module-level constants the original file exposed. Kept here so legacy
# `from llm_client import OPENAI_API_KEY` etc. doesn't break.
import os as _os
OPENAI_API_KEY = _os.getenv("OPENAI_API_KEY", "")
TEXT_MODEL = _os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
REASONING_EFFORT = _os.getenv("REASONING_EFFORT", "low")
MAX_OUTPUT_TOKENS = int(_os.getenv("MAX_OUTPUT_TOKENS", "4096"))
TOKEN_BUDGET_PER_JOB = int(_os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))

# Legacy helper name kept for back-compat
_extract_openai_error = extract_openai_error
