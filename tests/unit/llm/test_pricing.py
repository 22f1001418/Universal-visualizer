"""Unit tests for backend.llm.pricing."""
from __future__ import annotations

import pytest

from backend.llm.pricing import cost_usd, PRICE_PER_1K


def test_cost_usd_gpt_4o_mini():
    # 1000 input + 1000 output @ gpt-4o-mini = 0.00015 + 0.0006 = 0.00075
    assert cost_usd(1000, 1000, "gpt-4o-mini") == pytest.approx(0.00075)


def test_cost_usd_gpt_4o():
    # 1000 input + 1000 output @ gpt-4o = 0.0025 + 0.010 = 0.0125
    assert cost_usd(1000, 1000, "gpt-4o") == pytest.approx(0.0125)


def test_cost_usd_unknown_model_returns_zero():
    assert cost_usd(1000, 1000, "fictional-model-9000") == 0.0


def test_cost_usd_zero_tokens():
    assert cost_usd(0, 0, "gpt-4o-mini") == 0.0


def test_price_table_has_every_documented_model():
    # If a model is added to the table without a test, this list goes stale.
    expected = {
        "gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4-turbo",
        "gpt-5", "gpt-5-mini", "gpt-5-nano",
        "o1", "o1-mini", "o3", "o3-mini", "o4-mini",
    }
    assert expected <= set(PRICE_PER_1K.keys())
