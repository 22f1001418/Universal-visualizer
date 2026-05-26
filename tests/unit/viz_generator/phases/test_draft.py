"""Unit tests for phases.draft.run_draft_phase.

The LLM and validator are mocked; we verify orchestration: how many calls
fire, how the fix prompt is built, and what gets returned on success vs.
two-failure terminal state.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.viz_generator.phases.draft import run_draft_phase, DraftResult
from backend.viz_generator.validator import ValidationResult


VALID_HTML = "<!doctype html><html><body>x</body></html>"
VALID_HTML_FIX = "<!doctype html><html><body>fixed</body></html>"


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "viz"


def test_draft_succeeds_on_first_attempt(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ) as mock_draft, patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        return_value=ValidationResult(success=True, screenshot_path=str(project_dir / "screenshot.png")),
    ) as mock_validate:
        result = run_draft_phase("binary search", "brief", project_dir)

    assert isinstance(result, DraftResult)
    assert result.success is True
    assert result.html == VALID_HTML
    assert result.attempts == 1
    mock_draft.assert_called_once()
    mock_fix.assert_not_called()
    mock_validate.assert_called_once()


def test_draft_runs_one_fix_iteration_and_succeeds(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ), patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
        return_value=VALID_HTML_FIX,
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        side_effect=[
            ValidationResult(success=False, error_log="boom"),
            ValidationResult(success=True, screenshot_path="x"),
        ],
    ) as mock_validate:
        result = run_draft_phase("topic", "brief", project_dir)

    assert result.success is True
    assert result.html == VALID_HTML_FIX
    assert result.attempts == 2
    assert mock_fix.call_count == 1
    assert mock_validate.call_count == 2
    # Fix call must include the failure log so the model can act on it.
    fix_kwargs = mock_fix.call_args
    assert "boom" in str(fix_kwargs)


def test_draft_returns_failure_after_one_fix_iteration_max(project_dir: Path):
    """If fix attempt also fails, we stop — no third LLM call."""
    with patch(
        "backend.viz_generator.phases.draft._llm_call_draft",
        return_value=VALID_HTML,
    ), patch(
        "backend.viz_generator.phases.draft._llm_call_fix",
        return_value=VALID_HTML_FIX,
    ) as mock_fix, patch(
        "backend.viz_generator.phases.draft.validate",
        side_effect=[
            ValidationResult(success=False, error_log="first"),
            ValidationResult(success=False, error_log="second"),
        ],
    ) as mock_validate:
        result = run_draft_phase("topic", "brief", project_dir)

    assert result.success is False
    assert result.attempts == 2
    assert mock_fix.call_count == 1
    assert mock_validate.call_count == 2
    assert "second" in result.error_log
