"""Per-task model selection.

Today's code uses one global OPENAI_TEXT_MODEL for every LLM call. That means
cheap classification calls pay heavy-model prices. LLMTask + resolve_model
let each call site declare its task; the cheap tasks default to gpt-4o-mini
while VIZ_DRAFT (the expensive one) keeps gpt-4o.

Env overrides per task:
  AGENT_A_EXTRACT     → MODEL_AGENT_A
  AGENT_B_SUGGEST     → MODEL_AGENT_B
  VIZ_TOPIC_CLASSIFY  → MODEL_VIZ_CLASSIFY
  VIZ_DRAFT           → MODEL_VIZ_DRAFT
  VIZ_BUILD_FIX       → MODEL_VIZ_FIX
  VIZ_RUNTIME_FIX     → MODEL_VIZ_RUNTIME
  VIZ_POLISH          → MODEL_VIZ_POLISH

Callers that pass task=None fall back to OPENAI_TEXT_MODEL (current behaviour).
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class LLMTask(str, Enum):
    AGENT_A_EXTRACT = "agent_a_extract"
    AGENT_B_SUGGEST = "agent_b_suggest"
    VIZ_TOPIC_CLASSIFY = "viz_topic_classify"
    VIZ_DRAFT = "viz_draft"
    VIZ_BUILD_FIX = "viz_build_fix"
    VIZ_RUNTIME_FIX = "viz_runtime_fix"
    VIZ_POLISH = "viz_polish"


_DEFAULTS: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "gpt-4o-mini",
    LLMTask.AGENT_B_SUGGEST:    "gpt-4o-mini",
    LLMTask.VIZ_TOPIC_CLASSIFY: "gpt-4o-mini",
    LLMTask.VIZ_DRAFT:          "gpt-4o",
    LLMTask.VIZ_BUILD_FIX:      "gpt-4o-mini",
    LLMTask.VIZ_RUNTIME_FIX:    "gpt-4o-mini",
    LLMTask.VIZ_POLISH:         "gpt-4o-mini",
}

_ENV_VAR: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "MODEL_AGENT_A",
    LLMTask.AGENT_B_SUGGEST:    "MODEL_AGENT_B",
    LLMTask.VIZ_TOPIC_CLASSIFY: "MODEL_VIZ_CLASSIFY",
    LLMTask.VIZ_DRAFT:          "MODEL_VIZ_DRAFT",
    LLMTask.VIZ_BUILD_FIX:      "MODEL_VIZ_FIX",
    LLMTask.VIZ_RUNTIME_FIX:    "MODEL_VIZ_RUNTIME",
    LLMTask.VIZ_POLISH:         "MODEL_VIZ_POLISH",
}


def resolve_model(task: Optional[LLMTask]) -> str:
    """Return the model name to use for this task.

    Resolution order:
      1. If task is set: env override (MODEL_<TASK>), else _DEFAULTS[task].
      2. If task is None: env OPENAI_TEXT_MODEL, else "gpt-4o-mini".
    """
    if task is None:
        return os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
    override = os.getenv(_ENV_VAR[task])
    if override:
        return override
    return _DEFAULTS[task]
