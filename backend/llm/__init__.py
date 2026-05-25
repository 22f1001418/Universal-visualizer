"""Public surface of backend.llm — the shared LLM core.

Every caller should import from `backend.llm`, not from sub-modules. The
sub-module layout is an implementation detail and may shuffle.
"""
from backend.llm.client import get_client, llm_call
from backend.llm.errors import (
    PERMANENT_STATUS,
    RETRYABLE_STATUS,
    extract_openai_error,
    is_permanent_status,
    is_retryable_status,
)
from backend.llm.pricing import PRICE_PER_1K, cost_usd
from backend.llm.reasoning import REASONING_PREFIXES, is_reasoning_model
from backend.llm.tasks import LLMTask, resolve_model
from backend.llm.tracker import TokenUsageTracker, token_tracker

__all__ = [
    "get_client", "llm_call",
    "LLMTask", "resolve_model",
    "TokenUsageTracker", "token_tracker",
    "cost_usd", "PRICE_PER_1K",
    "is_reasoning_model", "REASONING_PREFIXES",
    "extract_openai_error", "is_retryable_status", "is_permanent_status",
    "PERMANENT_STATUS", "RETRYABLE_STATUS",
]
