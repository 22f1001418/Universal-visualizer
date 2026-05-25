"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture
def fake_llm(monkeypatch):
    """Pre-recorded LLM responses keyed by step_label.

    Tests set `fake_llm.responses[step_label] = '...'` and any call into
    `backend.llm.client.llm_call` returns that string. Calls are also recorded
    into the real token tracker so budget tests behave realistically.
    """
    responses: dict[str, str] = {}
    calls: list[dict] = []

    def llm_call_stub(
        system_prompt: str,
        user_prompt: str,
        step_label: str,
        job_id=None,
        task=None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        calls.append({
            "step_label": step_label,
            "job_id": job_id,
            "task": task,
            "in_chars": len(system_prompt) + len(user_prompt),
        })
        try:
            from backend.llm.tracker import token_tracker
        except ImportError:
            from llm_client import token_tracker
        token_tracker.record(
            step_label=step_label,
            input_tokens=100,
            output_tokens=50,
            job_id=job_id,
            model="gpt-4o-mini",
            reasoning_tokens=0,
        )
        return responses.get(step_label, '{"topics": []}')

    # Patch backend.llm.client.llm_call only if the module exists (Tasks 3-8 create it).
    try:
        import backend.llm.client as _backend_llm_client  # noqa: PLC0415
        monkeypatch.setattr(_backend_llm_client, "llm_call", llm_call_stub)
    except ImportError:
        pass  # backend.llm.client not yet created — skip this patch for now
    # Always patch the legacy shim so current `from llm_client import llm_call` calls work.
    monkeypatch.setattr("llm_client.llm_call", llm_call_stub, raising=False)
    # Also patch agents.llm_call — agents.py uses `from llm_client import llm_call`
    # which binds the name at import time; patching the source module alone is not enough.
    monkeypatch.setattr("agents.llm_call", llm_call_stub, raising=False)

    class FakeLLM:
        def __init__(self) -> None:
            self.responses = responses
            self.calls = calls

    return FakeLLM()
