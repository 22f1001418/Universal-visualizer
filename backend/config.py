"""Central env var configuration for the backend.

pydantic-settings reads .env and OS env at import time. Every module that
needs config should import `settings` from here. Avoids the scattered
os.getenv(...) pattern that Stage 1 left in place.

Stage 2 scope: OpenAI + LLM behavior + budgets. Stage 3 will add the
routing/orchestration settings (CORS, dev-server port range, github
publish flags) and complete the migration off os.getenv.

Note on test-time mutation: pydantic-settings reads env at construction.
The `settings` singleton at module scope is bound once. Tests that need
to override should construct a fresh Settings() inside the test (after
monkeypatching the env), not mutate `settings` directly.

Note: LLM_PROVIDER and MODEL_NAME (used by backend/viz_generator/llm.py)
are intentionally omitted here. The viz generator reads those at module
import via os.getenv and migrating it requires deeper restructuring of the
viz_generator package. That migration is deferred to a later stage.
"""
from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # OpenAI
    openai_api_key: str = ""
    openai_text_model: str = "gpt-4o-mini"
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 4096
    llm_client_timeout: float = 600.0

    # Per-task model overrides (optional; defaults live in backend.llm.tasks)
    model_agent_a: str | None = None
    model_agent_b: str | None = None
    model_viz_classify: str | None = None
    model_viz_draft: str | None = None
    model_viz_fix: str | None = None
    model_viz_runtime: str | None = None
    model_viz_polish: str | None = None

    # Budgets
    token_budget_per_job: int = 300_000


settings = Settings()
