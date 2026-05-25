"""Unit tests for backend.config.Settings."""
from __future__ import annotations

import pytest


def test_defaults_load(monkeypatch):
    # Strip envs that would override defaults
    for name in ("OPENAI_API_KEY", "OPENAI_TEXT_MODEL", "REASONING_EFFORT",
                 "MAX_OUTPUT_TOKENS", "TOKEN_BUDGET_PER_JOB",
                 "LLM_CLIENT_TIMEOUT"):
        monkeypatch.delenv(name, raising=False)
    # Construct a FRESH Settings — module-level singleton was bound at import
    from backend.config import Settings
    s = Settings()
    assert s.openai_text_model == "gpt-4o-mini"
    assert s.reasoning_effort == "low"
    assert s.max_output_tokens == 4096
    assert s.token_budget_per_job == 300_000
    assert s.llm_client_timeout == 600.0


def test_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-5")
    monkeypatch.setenv("TOKEN_BUDGET_PER_JOB", "100000")
    from backend.config import Settings
    s = Settings()
    assert s.openai_text_model == "gpt-5"
    assert s.token_budget_per_job == 100_000


def test_invalid_reasoning_effort_raises(monkeypatch):
    monkeypatch.setenv("REASONING_EFFORT", "extreme")
    from backend.config import Settings
    with pytest.raises(Exception):
        Settings()


def test_per_task_model_override_optional(monkeypatch):
    monkeypatch.delenv("MODEL_VIZ_DRAFT", raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.model_viz_draft is None

    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    s2 = Settings()
    assert s2.model_viz_draft == "gpt-5"


def test_server_defaults(monkeypatch):
    for name in ("PORT", "HOST", "ALLOWED_ORIGINS"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.port == 8001
    assert s.host == "0.0.0.0"
    assert s.allowed_origins == [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ]


def test_allowed_origins_csv_parsing(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS",
        "https://a.example.com,https://b.example.com",
    )
    from backend.config import Settings
    s = Settings()
    assert s.allowed_origins == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_github_defaults(monkeypatch):
    for name in ("GITHUB_TOKEN", "GITHUB_OWNER",
                 "PUBLISH_TO_GITHUB", "GITHUB_INCLUDE_DIST",
                 "GITHUB_REPOS_PRIVATE"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.github_token is None
    assert s.github_owner is None
    assert s.publish_to_github is True
    assert s.github_include_dist is True
    assert s.github_repos_private is False


def test_github_repos_private_truthy(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOS_PRIVATE", "true")
    from backend.config import Settings
    s = Settings()
    assert s.github_repos_private is True


def test_dev_server_defaults(monkeypatch):
    for name in ("DEV_SERVER_PORT_START", "DEV_SERVER_PORT_END",
                 "PREVIEW_BOOT_WAIT", "NPM_INSTALL_TIMEOUT",
                 "AUDIT_FIX_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    from backend.config import Settings
    s = Settings()
    assert s.dev_server_port_start == 5180
    assert s.dev_server_port_end == 5230
    assert s.preview_boot_wait == 45
    assert s.npm_install_timeout == 300
    assert s.audit_fix_enabled is False


def test_build_timeout_override(monkeypatch):
    monkeypatch.setenv("BUILD_TIMEOUT_SECONDS", "600")
    from backend.config import Settings
    s = Settings()
    assert s.build_timeout_seconds == 600
