"""Contract test fixtures — TestClient against the current FastAPI app."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """A TestClient bound to the live FastAPI app.

    OPENAI_API_KEY is set to a dummy value because get_client() exits on import
    if it's missing. Actual LLM calls in contract tests are stubbed via fake_llm
    from tests/conftest.py.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("VIZ_OUTPUT_DIR", str(tmp_path / "viz_outputs"))
    (tmp_path / "viz_outputs").mkdir(parents=True, exist_ok=True)

    from main import app  # noqa: WPS433 — late import after env setup
    return TestClient(app)
