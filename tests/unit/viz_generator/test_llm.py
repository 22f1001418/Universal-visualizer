"""Smoke test that the new `task` parameter on viz_generator.llm.llm_call
routes through backend.llm.resolve_model() for the OpenAI branch.

Design note: LLM_PROVIDER is a module-level constant in backend.viz_generator.llm
(set at import time from os.getenv). We therefore patch the *module attribute*
directly rather than relying on monkeypatch.setenv (which would only affect
future os.getenv calls, not the already-bound constant).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from backend.llm import LLMTask


def _make_fake_response(content: str = '{"ok": true}') -> MagicMock:
    fake_response = MagicMock()
    fake_choice = MagicMock()
    fake_choice.message.content = content
    fake_choice.finish_reason = "stop"
    fake_response.choices = [fake_choice]
    usage = MagicMock()
    usage.prompt_tokens = 10
    usage.completion_tokens = 5
    details = MagicMock()
    details.reasoning_tokens = 0
    usage.completion_tokens_details = details
    fake_response.usage = usage
    return fake_response


def test_task_overrides_model_for_openai():
    """When LLM_PROVIDER=openai and task=VIZ_DRAFT, the model passed to the
    OpenAI client must match resolve_model(LLMTask.VIZ_DRAFT)."""
    import backend.viz_generator.llm as viz_llm

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _make_fake_response()

    # Patch the module-level constants so the function sees "openai" provider
    # and our sentinel model for VIZ_DRAFT.
    with (
        patch.object(viz_llm, "LLM_PROVIDER", "openai"),
        patch.object(viz_llm, "MODEL_NAME", "gpt-original"),
        patch("backend.llm.tasks._ENV_VAR", {
            **{t: v for t, v in __import__("backend.llm.tasks", fromlist=["_ENV_VAR"])._ENV_VAR.items()},
        }),
        patch("backend.viz_generator.llm._get_client", return_value=fake_client),
        patch("backend.viz_generator.llm.resolve_model", return_value="gpt-sentinel") as mock_resolve,
    ):
        viz_llm.llm_call(
            messages=[{"role": "user", "content": "hi"}],
            step_label="test",
            task=LLMTask.VIZ_DRAFT,
        )

    # resolve_model should have been called with VIZ_DRAFT
    mock_resolve.assert_called_once_with(LLMTask.VIZ_DRAFT)

    # The sentinel model name should have reached the OpenAI client
    call_kwargs = fake_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("model") == "gpt-sentinel", (
        f"Expected model='gpt-sentinel', got model={call_kwargs.get('model')!r}"
    )


def test_task_none_uses_model_name():
    """When task=None, the existing MODEL_NAME fallback is preserved."""
    import backend.viz_generator.llm as viz_llm

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _make_fake_response()

    with (
        patch.object(viz_llm, "LLM_PROVIDER", "openai"),
        patch.object(viz_llm, "MODEL_NAME", "gpt-original"),
        patch("backend.viz_generator.llm._get_client", return_value=fake_client),
        patch("backend.viz_generator.llm.resolve_model") as mock_resolve,
    ):
        viz_llm.llm_call(
            messages=[{"role": "user", "content": "hi"}],
            step_label="test",
            task=None,
        )

    # resolve_model must NOT be called when task is None
    mock_resolve.assert_not_called()

    call_kwargs = fake_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("model") == "gpt-original", (
        f"Expected model='gpt-original', got model={call_kwargs.get('model')!r}"
    )


def test_gemini_provider_ignores_task():
    """For LLM_PROVIDER=gemini, task is ignored and MODEL_NAME is used as-is."""
    import backend.viz_generator.llm as viz_llm

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = _make_fake_response()

    with (
        patch.object(viz_llm, "LLM_PROVIDER", "gemini"),
        patch.object(viz_llm, "MODEL_NAME", "gemini-2.5-flash"),
        patch("backend.viz_generator.llm._get_client", return_value=fake_client),
        patch("backend.viz_generator.llm.resolve_model") as mock_resolve,
    ):
        viz_llm.llm_call(
            messages=[{"role": "user", "content": "hi"}],
            step_label="test",
            task=LLMTask.VIZ_DRAFT,
        )

    # resolve_model must NOT be called for Gemini even when task is set
    mock_resolve.assert_not_called()

    call_kwargs = fake_client.chat.completions.create.call_args[1]
    assert call_kwargs.get("model") == "gemini-2.5-flash", (
        f"Expected model='gemini-2.5-flash', got model={call_kwargs.get('model')!r}"
    )
