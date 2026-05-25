"""Unit tests for backend.llm.client.

These tests exercise the public surface (`get_client`, `llm_call`) without
hitting OpenAI. The OpenAI client is monkey-patched to a fake.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_openai_client(monkeypatch):
    """Replace get_client()'s OpenAI() construction with a MagicMock."""
    fake_client = MagicMock()
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(content='{"hello":"world"}', reasoning_content=None),
        finish_reason="stop",
    )
    fake_response = SimpleNamespace(
        choices=[fake_choice],
        usage=SimpleNamespace(
            prompt_tokens=42,
            completion_tokens=17,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )
    fake_client.chat.completions.create.return_value = fake_response

    import backend.llm.client as client_mod
    monkeypatch.setattr(client_mod, "_client", fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return fake_client


def test_llm_call_happy_path(fake_openai_client):
    from backend.llm.client import llm_call
    from backend.llm.tasks import LLMTask

    out = llm_call(
        system_prompt="you are a test",
        user_prompt="say hi",
        step_label="unit_test",
        task=LLMTask.AGENT_A_EXTRACT,
    )
    assert out == '{"hello":"world"}'
    # Verify the SDK was called with the resolved model
    args, kwargs = fake_openai_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"  # AGENT_A_EXTRACT default
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"


def test_llm_call_reasoning_model_uses_completion_tokens(fake_openai_client, monkeypatch):
    from backend.llm.client import llm_call
    from backend.llm.tasks import LLMTask

    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    llm_call("sys", "usr", "x", task=LLMTask.VIZ_DRAFT)
    _, kwargs = fake_openai_client.chat.completions.create.call_args
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert kwargs["reasoning_effort"] in {"low", "medium", "high"}


def test_llm_call_records_tokens(fake_openai_client):
    from backend.llm.client import llm_call
    from backend.llm.tracker import token_tracker

    llm_call("sys", "usr", "step_X", job_id="job_42")
    s = token_tracker.job_summary("job_42")
    assert s["input_tokens"] >= 42
    assert s["output_tokens"] >= 17
