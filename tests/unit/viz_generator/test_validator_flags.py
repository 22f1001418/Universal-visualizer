"""Fast (mocked) guard that the Chromium launch keeps its low-memory flags.

The real-browser validator tests live in test_validator.py (slow lane). This
one mocks Playwright so it runs in the fast lane and exists purely to stop the
container-stability flags from being dropped accidentally — they are what keeps
the screenshot step alive on a 512MB instance (e.g. Render free).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.viz_generator.validator import validate

REQUIRED_FLAGS = {"--no-sandbox", "--disable-dev-shm-usage", "--single-process"}


def test_validator_launches_chromium_with_memory_flags(tmp_path: Path):
    captured: dict = {}

    fake_browser = MagicMock()
    fake_page = fake_browser.new_context.return_value.new_page.return_value
    # body_html (non-empty) then body_text — so validation reaches screenshot.
    fake_page.evaluate.side_effect = ["<div>x</div>", "x"]

    fake_p = MagicMock()

    def _launch(**kwargs):
        captured.update(kwargs)
        return fake_browser

    fake_p.chromium.launch.side_effect = _launch

    with patch("playwright.sync_api.sync_playwright") as m_sp:
        m_sp.return_value.__enter__.return_value = fake_p
        result = validate("<!doctype html><body><div>x</div></body>", tmp_path)

    assert result.success is True
    args = set(captured.get("args", []))
    missing = REQUIRED_FLAGS - args
    assert not missing, f"Chromium launch missing low-memory flags: {missing} (got {args})"
    assert captured.get("headless") is True
