from __future__ import annotations

from backend.llm.reasoning import is_reasoning_model, REASONING_PREFIXES


def test_gpt_5_is_reasoning():
    assert is_reasoning_model("gpt-5") is True
    assert is_reasoning_model("gpt-5-mini") is True


def test_o_series_is_reasoning():
    assert is_reasoning_model("o1") is True
    assert is_reasoning_model("o3-mini") is True
    assert is_reasoning_model("o4-mini") is True


def test_gpt_4o_not_reasoning():
    assert is_reasoning_model("gpt-4o") is False
    assert is_reasoning_model("gpt-4o-mini") is False


def test_empty_or_none_is_not_reasoning():
    assert is_reasoning_model("") is False
    assert is_reasoning_model(None) is False  # type: ignore[arg-type]


def test_case_insensitive():
    assert is_reasoning_model("GPT-5") is True
    assert is_reasoning_model("O3") is True


def test_known_prefixes_exposed():
    assert "gpt-5" in REASONING_PREFIXES
    assert "o1" in REASONING_PREFIXES
