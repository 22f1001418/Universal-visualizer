"""Unit tests for the single-launch Playwright validator.

These tests require Playwright to be installed with the chromium browser
(the project already depends on `playwright>=1.40` and the Dockerfile runs
`playwright install chromium --with-deps`). They run in CI's slow lane.
"""
from __future__ import annotations

import pytest
from pathlib import Path

from backend.viz_generator.validator import validate, ValidationResult


pytestmark = pytest.mark.slow


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "viz"
    d.mkdir()
    return d


def test_validator_returns_success_for_valid_html(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>t</title></head>
    <body><div>hi</div></body></html>"""
    result = validate(html, project_dir)
    assert isinstance(result, ValidationResult)
    assert result.success is True
    assert result.error_log == ""
    assert result.screenshot_path == str(project_dir / "screenshot.png")
    assert (project_dir / "screenshot.png").exists()
    assert (project_dir / "screenshot.png").stat().st_size > 0


def test_validator_captures_pageerror(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><div>boot</div>
    <script>throw new Error("boom-runtime-error")</script>
    </body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "boom-runtime-error" in result.error_log
    # No screenshot on failure
    assert result.screenshot_path == ""


def test_validator_logs_console_error_but_passes_by_default(project_dir: Path):
    """console.error is noisy (many libs / devtools log non-fatal warnings here).
    It is captured for diagnostics but does NOT fail validation in default mode.
    Use VALIDATOR_STRICT_CONSOLE=1 to make it fatal."""
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><div>boot</div>
    <script>console.error("logged-noise")</script>
    </body></html>"""
    result = validate(html, project_dir)
    assert result.success is True
    # Warning surfaced on the result for diagnostics
    assert "logged-noise" in result.warnings


def test_validator_fails_on_console_error_in_strict_mode(project_dir: Path, monkeypatch):
    monkeypatch.setenv("VALIDATOR_STRICT_CONSOLE", "1")
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><div>x</div><script>console.error("strict-fail")</script></body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "strict-fail" in result.error_log


def test_validator_rejects_empty_body(project_dir: Path):
    """Catch blank pages via DOM presence + innerText, not bounding_box.
    bounding_box races init JS and times out spuriously."""
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body></body></html>"""
    result = validate(html, project_dir)
    assert result.success is False
    assert "empty" in result.error_log.lower() or "blank" in result.error_log.lower()


def test_validator_writes_html_to_disk_before_launch(project_dir: Path):
    html = """<!doctype html><html lang="en"><head><meta charset="UTF-8"></head>
    <body><p>x</p></body></html>"""
    validate(html, project_dir)
    written = (project_dir / "index.html").read_text(encoding="utf-8")
    assert written == html
