"""Unit tests for backend.api.deps."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.deps import get_settings, get_job


def test_get_settings_returns_singleton():
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2


def test_get_job_raises_404_for_unknown(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        get_job("does-not-exist")
    assert exc_info.value.status_code == 404
    assert "not found" in exc_info.value.detail.lower()
