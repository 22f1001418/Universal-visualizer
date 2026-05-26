from __future__ import annotations

import pytest

from backend.llm.tasks import LLMTask, resolve_model


def test_llmtask_enum_values():
    assert LLMTask.AGENT_A_EXTRACT.value == "agent_a_extract"
    assert LLMTask.AGENT_B_SUGGEST.value == "agent_b_suggest"
    assert LLMTask.VIZ_TOPIC_CLASSIFY.value == "viz_topic_classify"
    assert LLMTask.VIZ_DRAFT.value == "viz_draft"
    assert LLMTask.VIZ_RUNTIME_FIX.value == "viz_runtime_fix"
    assert LLMTask.VIZ_POLISH.value == "viz_polish"


def test_resolve_model_defaults(monkeypatch):
    # Strip every override
    for name in ("MODEL_AGENT_A", "MODEL_AGENT_B", "MODEL_VIZ_CLASSIFY",
                 "MODEL_VIZ_DRAFT", "MODEL_VIZ_RUNTIME",
                 "MODEL_VIZ_POLISH", "OPENAI_TEXT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert resolve_model(LLMTask.AGENT_A_EXTRACT) == "gpt-4o-mini"
    assert resolve_model(LLMTask.AGENT_B_SUGGEST) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_TOPIC_CLASSIFY) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-5"
    assert resolve_model(LLMTask.VIZ_RUNTIME_FIX) == "gpt-5"
    assert resolve_model(LLMTask.VIZ_POLISH) == "gpt-5"


def test_resolve_model_env_override(monkeypatch):
    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-5"


def test_resolve_model_falls_back_to_global_when_task_is_none(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-4.1")
    assert resolve_model(None) == "gpt-4.1"


def test_resolve_model_uses_global_default_when_nothing_set(monkeypatch):
    for name in ("OPENAI_TEXT_MODEL",):
        monkeypatch.delenv(name, raising=False)
    assert resolve_model(None) == "gpt-4o-mini"


def test_viz_build_fix_is_removed():
    names = {t.name for t in LLMTask}
    assert "VIZ_BUILD_FIX" not in names


def test_remaining_viz_tasks_present():
    names = {t.name for t in LLMTask}
    for required in (
        "AGENT_A_EXTRACT", "AGENT_B_SUGGEST",
        "VIZ_TOPIC_CLASSIFY", "VIZ_DRAFT", "VIZ_RUNTIME_FIX", "VIZ_POLISH",
    ):
        assert required in names, f"{required} missing"


def test_resolve_model_for_viz_draft_defaults_to_gpt5(monkeypatch):
    monkeypatch.delenv("MODEL_VIZ_DRAFT", raising=False)
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-5"
