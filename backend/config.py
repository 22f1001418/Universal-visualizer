"""Central env var configuration for the backend.

pydantic-settings reads .env and OS env at import time. Every module that
needs config should import `settings` from here. Stage 3 completes the
migration off os.getenv for FastAPI-side env vars.

Note on test-time mutation: pydantic-settings reads env at construction.
The `settings` singleton at module scope is bound once. Tests that need
to override should construct a fresh Settings() inside the test (after
monkeypatching the env), not mutate `settings` directly.

Note: LLM_PROVIDER and MODEL_NAME (used by backend/viz_generator/llm.py)
remain on os.getenv. Migrating them requires deeper restructuring of the
viz_generator package and is not part of Stage 3.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict
from pydantic_settings.sources.providers.dotenv import DotEnvSettingsSource

# CSV_FIELDS is the set of list[str] fields that accept comma-separated values
# (not JSON arrays) when supplied via environment variables.
_CSV_FIELDS: frozenset[str] = frozenset({"allowed_origins"})


def _parse_csv(raw: str | list[str]) -> list[str]:
    """Parse comma-separated strings into a list; pass-through if already a list."""
    if isinstance(raw, list):
        return raw
    return [x.strip() for x in raw.split(",") if x.strip()]


class _CsvDecodeMixin:
    """Mixin that CSV-splits fields listed in _CSV_FIELDS.

    pydantic-settings by default tries to JSON-decode all complex-typed fields
    (including list[str]). For CSV_FIELDS we intercept the decode step and split
    on commas instead. Applied to both env-var and dotenv sources so the
    behavior is identical whether the value comes from os.environ or .env.
    """

    def decode_complex_value(self, field_name: str, field: FieldInfo, value: Any) -> Any:
        if field_name in _CSV_FIELDS and isinstance(value, str):
            return _parse_csv(value)
        return super().decode_complex_value(field_name, field, value)


class _CsvEnvSource(_CsvDecodeMixin, EnvSettingsSource):
    pass


class _CsvDotEnvSource(_CsvDecodeMixin, DotEnvSettingsSource):
    pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── OpenAI (Stage 2) ─────────────────────────────────────
    openai_api_key: str = ""
    openai_text_model: str = "gpt-4o-mini"
    reasoning_effort: Literal["low", "medium", "high"] = "low"
    max_output_tokens: int = 4096
    llm_client_timeout: float = 600.0

    # Per-task model overrides (Stage 2)
    model_agent_a: str | None = None
    model_agent_b: str | None = None
    model_viz_classify: str | None = None
    model_viz_draft: str | None = None
    model_viz_fix: str | None = None
    model_viz_runtime: str | None = None
    model_viz_polish: str | None = None

    # Budgets (Stage 2)
    token_budget_per_job: int = 500_000

    # ── Server (Stage 3) ─────────────────────────────────────
    port: int = 8001
    host: str = "0.0.0.0"
    allowed_origins: list[str] = [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]

    # ── GitHub publish (Stage 3) ─────────────────────────────
    github_token: str | None = None
    github_owner: str | None = None
    publish_to_github: bool = True
    github_include_dist: bool = True
    github_repos_private: bool = False

    # ── Build orchestration (Stage 3) ────────────────────────
    build_timeout_seconds: int = 1200
    viz_output_dir: str = "viz_outputs"
    fixed_main_path: str = "fixed_main_v6.py"

    # ── Dev server (Stage 3) ─────────────────────────────────
    dev_server_port_start: int = 5180
    dev_server_port_end: int = 5230
    preview_boot_wait: int = 45
    npm_install_timeout: int = 300
    audit_fix_enabled: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        return (
            init_settings,
            _CsvEnvSource(settings_cls),
            _CsvDotEnvSource(settings_cls),
            file_secret_settings,
        )


settings = Settings()
