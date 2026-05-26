"""Unit tests for phases.polish.run_polish_phase.

LLM + validator mocked. Verifies two fallback paths:
  (1) validation regresses → ship pre-polish HTML
  (2) structural integrity regresses (buttons/inputs stripped) → ship
      pre-polish HTML even when validation passes
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from backend.viz_generator.phases.polish import run_polish_phase, PolishResult
from backend.viz_generator.validator import ValidationResult


PRE_HTML = (
    "<!doctype html><html><body>"
    "<button>Play</button><button>Step</button><button>Reset</button>"
    "<input type='range' />"
    "<div id='viz'>pre</div>"
    "</body></html>"
)
POST_HTML_KEEPS_CONTROLS = (
    "<!doctype html><html><body>"
    "<button>Play</button><button>Step</button><button>Reset</button>"
    "<input type='range' />"
    "<div id='viz'>polished</div>"
    "</body></html>"
)
POST_HTML_DROPS_CONTROLS = (
    "<!doctype html><html><body>"
    "<div id='viz'>shiny but stripped</div>"
    "</body></html>"
)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    return tmp_path / "viz"


def test_polish_succeeds_and_returns_polished_html(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.polish._llm_call_polish",
        return_value=POST_HTML_KEEPS_CONTROLS,
    ), patch(
        "backend.viz_generator.phases.polish.validate",
        return_value=ValidationResult(success=True, screenshot_path="x"),
    ) as mock_validate:
        result = run_polish_phase("topic", PRE_HTML, project_dir)

    assert isinstance(result, PolishResult)
    assert result.html == POST_HTML_KEEPS_CONTROLS
    assert result.polished is True
    assert result.fallback_used is False
    mock_validate.assert_called_once()


def test_polish_falls_back_to_pre_polish_when_validation_regresses(project_dir: Path):
    with patch(
        "backend.viz_generator.phases.polish._llm_call_polish",
        return_value=POST_HTML_KEEPS_CONTROLS,
    ), patch(
        "backend.viz_generator.phases.polish.validate",
        return_value=ValidationResult(success=False, error_log="polish broke it"),
    ):
        result = run_polish_phase("topic", PRE_HTML, project_dir)

    assert result.html == PRE_HTML
    assert result.polished is False
    assert result.fallback_used is True
    assert "polish broke it" in result.error_log


def test_polish_falls_back_when_structural_integrity_regresses(project_dir: Path):
    """Even if validation passes, dropping >40% of buttons/inputs is a regress."""
    with patch(
        "backend.viz_generator.phases.polish._llm_call_polish",
        return_value=POST_HTML_DROPS_CONTROLS,
    ), patch(
        "backend.viz_generator.phases.polish.validate",
        return_value=ValidationResult(success=True, screenshot_path="x"),
    ):
        result = run_polish_phase("topic", PRE_HTML, project_dir)

    assert result.html == PRE_HTML
    assert result.polished is False
    assert result.fallback_used is True
    assert "structural" in result.error_log.lower() or "buttons" in result.error_log.lower()
