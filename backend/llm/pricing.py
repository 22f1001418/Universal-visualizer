"""Per-call USD pricing for OpenAI models.

PRICE_PER_1K mirrors the table previously in llm_client.py — values are USD
per 1,000 tokens. Update when OpenAI changes prices.

cost_usd() is the single function callers should use. Unknown models return
$0 (never raises) so the rest of the system never crashes because of a
missing entry; the [Tokens] log line will show $0 and that's the prompt to
update the table.
"""
from __future__ import annotations

from typing import Optional

PRICE_PER_1K: dict[str, dict[str, float]] = {
    "gpt-4o-mini":      {"input": 0.00015, "output": 0.0006},
    "gpt-4o":           {"input": 0.0025,  "output": 0.010},
    "gpt-4.1":          {"input": 0.0020,  "output": 0.008},
    "gpt-4.1-mini":     {"input": 0.00040, "output": 0.0016},
    "gpt-4-turbo":      {"input": 0.010,   "output": 0.030},
    "gpt-5":            {"input": 0.00125, "output": 0.010},
    "gpt-5-mini":       {"input": 0.00025, "output": 0.0020},
    "gpt-5-nano":       {"input": 0.00005, "output": 0.0004},
    "o1":               {"input": 0.015,   "output": 0.060},
    "o1-mini":          {"input": 0.0011,  "output": 0.0044},
    "o3":               {"input": 0.010,   "output": 0.040},
    "o3-mini":          {"input": 0.0011,  "output": 0.0044},
    "o4-mini":          {"input": 0.0011,  "output": 0.0044},
}


def cost_usd(input_tokens: int, output_tokens: int, model: Optional[str]) -> float:
    """Return USD cost of a call. Unknown models return 0.0."""
    if not model:
        return 0.0
    prices = PRICE_PER_1K.get(model)
    if not prices:
        return 0.0
    return (input_tokens / 1000.0) * prices["input"] + (output_tokens / 1000.0) * prices["output"]
