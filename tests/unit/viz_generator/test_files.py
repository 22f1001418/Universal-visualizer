"""Unit tests for the slim vanilla-viz file helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.viz_generator.files import (
    extract_html,
    pre_validate_html,
    write_html_to_disk,
    print_error_block,
)


# ── extract_html ─────────────────────────────────────────────────────────

def test_extract_html_passes_raw_doctype_through():
    raw = "<!doctype html><html lang=\"en\"><body>x</body></html>"
    assert extract_html(raw) == raw


def test_extract_html_strips_html_codefence():
    raw = "```html\n<!doctype html><html><body>x</body></html>\n```"
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_strips_unlabeled_codefence():
    raw = "```\n<!doctype html><html><body>x</body></html>\n```"
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_strips_leading_and_trailing_whitespace():
    raw = "   \n<!doctype html><html><body>x</body></html>\n   "
    assert extract_html(raw) == "<!doctype html><html><body>x</body></html>"


def test_extract_html_raises_when_no_html_found():
    with pytest.raises(ValueError, match="no html"):
        extract_html("sorry, I cannot help with that")


# ── pre_validate_html ────────────────────────────────────────────────────

def test_pre_validate_passes_for_valid_html():
    html = "<!doctype html><html lang=\"en\"><body><div>x</div></body></html>"
    assert pre_validate_html(html) == []


def test_pre_validate_flags_missing_html_tag():
    problems = pre_validate_html("<!doctype><body>x</body>")
    assert any("html" in p.lower() for p in problems)


def test_pre_validate_flags_missing_body_tag():
    problems = pre_validate_html("<!doctype html><html></html>")
    assert any("body" in p.lower() for p in problems)


def test_pre_validate_flags_truncation():
    # Long HTML that ends mid-script (no </body>/</html>)
    truncated = "<!doctype html><html><body><div>x</div><script>function foo(){ return " + "a" * 500
    problems = pre_validate_html(truncated)
    assert any("truncat" in p.lower() for p in problems)


def test_pre_validate_flags_empty_script():
    html = "<!doctype html><html><body><div>x</div><script></script></body></html>"
    problems = pre_validate_html(html)
    assert any("empty <script>" in p for p in problems)


# ── write_html_to_disk ───────────────────────────────────────────────────

def test_write_html_to_disk_writes_index_html(tmp_path: Path):
    html = "<!doctype html><html><body>x</body></html>"
    write_html_to_disk(tmp_path, html)
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == html


def test_write_html_to_disk_creates_project_dir(tmp_path: Path):
    target = tmp_path / "new-sub"
    write_html_to_disk(target, "<!doctype html><html><body>x</body></html>")
    assert (target / "index.html").exists()


def test_write_html_to_disk_rejects_empty(tmp_path: Path):
    with pytest.raises(ValueError, match="empty"):
        write_html_to_disk(tmp_path, "")
    with pytest.raises(ValueError, match="empty"):
        write_html_to_disk(tmp_path, "   \n\n   ")


# ── print_error_block ────────────────────────────────────────────────────

def test_print_error_block_runs_without_raising(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="viz_agent")
    print_error_block("Demo error", "line one\nline two\nline three")
    # Should have emitted at least one log record with the label
    assert any("Demo error" in rec.message for rec in caplog.records)
