"""Unit tests for backend.viz_generator.parsing."""
from __future__ import annotations

import pytest

from backend.viz_generator.parsing import (
    _clean_file_content,
    _extract_filename,
    _parse_codeblock_format,
    _parse_marker_format,
    format_files_for_prompt,
    parse_files,
)


# ---------------------------------------------------------------------------
# _extract_filename
# ---------------------------------------------------------------------------

def test_extract_filename_bare_path():
    assert _extract_filename("src/App.jsx") == "src/App.jsx"


def test_extract_filename_from_bold_header():
    # "**File: src/App.jsx**" — leading/trailing * stripped, then label removed
    assert _extract_filename("**File: src/App.jsx**") == "src/App.jsx"


def test_extract_filename_from_markdown_heading():
    assert _extract_filename("### index.html") == "index.html"


def test_extract_filename_from_codeblock_info_string():
    # e.g. the rest of "```javascript src/App.jsx" after the language tag
    assert _extract_filename("javascript src/App.jsx") == "src/App.jsx"


def test_extract_filename_no_match_returns_none():
    assert _extract_filename("no filename here") is None


def test_extract_filename_empty_string_returns_none():
    assert _extract_filename("") is None


# ---------------------------------------------------------------------------
# _clean_file_content
# ---------------------------------------------------------------------------

def test_clean_file_content_strips_opening_fence():
    lines = ["```jsx", "const x = 1;"]
    assert _clean_file_content(lines) == "const x = 1;"


def test_clean_file_content_strips_closing_fence():
    lines = ["const x = 1;", "```"]
    assert _clean_file_content(lines) == "const x = 1;"


def test_clean_file_content_plain_lines():
    lines = ["const x = 1;", "const y = 2;"]
    assert _clean_file_content(lines) == "const x = 1;\nconst y = 2;"


# ---------------------------------------------------------------------------
# _parse_marker_format
# ---------------------------------------------------------------------------

def test_parse_marker_format_basic():
    text = (
        "==== FILE: src/App.jsx ====\n"
        "export default function App() {}\n"
        "==== END FILE ===="
    )
    result = _parse_marker_format(text)
    assert "src/App.jsx" in result
    assert "App" in result["src/App.jsx"]


def test_parse_marker_format_multiple_files():
    text = (
        "==== FILE: src/App.jsx ====\n"
        "// app\n"
        "==== END FILE ====\n"
        "==== FILE: index.html ====\n"
        "<html/>\n"
        "==== END FILE ===="
    )
    result = _parse_marker_format(text)
    assert set(result.keys()) == {"src/App.jsx", "index.html"}


def test_parse_marker_format_no_markers_returns_empty():
    assert _parse_marker_format("just some text") == {}


# ---------------------------------------------------------------------------
# _parse_codeblock_format
# ---------------------------------------------------------------------------

def test_parse_codeblock_format_filename_above_fence():
    text = "src/App.jsx\n```jsx\nconst App = () => null;\n```"
    result = _parse_codeblock_format(text)
    assert "src/App.jsx" in result
    assert "App" in result["src/App.jsx"]


def test_parse_codeblock_format_no_filenames_returns_empty():
    text = "```jsx\nconst x = 1;\n```"
    result = _parse_codeblock_format(text)
    # No filename hint → empty dict
    assert result == {}


# ---------------------------------------------------------------------------
# parse_files  (integration: prefers marker format, falls back to codeblock)
# ---------------------------------------------------------------------------

def test_parse_files_marker_format():
    text = (
        "==== FILE: src/App.jsx ====\n"
        "import React from 'react';\n"
        "export default function App() { return null; }\n"
        "==== END FILE ===="
    )
    files = parse_files(text)
    assert isinstance(files, dict)
    assert "src/App.jsx" in files


def test_parse_files_codeblock_fallback():
    text = "src/App.jsx\n```jsx\nconst App = () => null;\n```"
    files = parse_files(text)
    assert isinstance(files, dict)
    assert "src/App.jsx" in files


def test_parse_files_empty_input_returns_empty_dict():
    files = parse_files("")
    assert files == {}


def test_parse_files_filters_bogus_filenames():
    # "node.json" is in _BOGUS_STANDALONE and must be dropped
    text = (
        "==== FILE: node.json ====\n"
        "{}\n"
        "==== END FILE ===="
    )
    files = parse_files(text)
    assert "node.json" not in files


# ---------------------------------------------------------------------------
# format_files_for_prompt
# ---------------------------------------------------------------------------

def test_format_files_for_prompt_round_trips():
    files = {"src/App.jsx": "export default function App() {}"}
    prompt = format_files_for_prompt(files)
    assert "==== FILE: src/App.jsx ====" in prompt
    assert "==== END FILE ====" in prompt
    assert "export default function App() {}" in prompt


def test_format_files_for_prompt_multiple_files():
    files = {"a.js": "// a", "b.css": "/* b */"}
    prompt = format_files_for_prompt(files)
    assert "==== FILE: a.js ====" in prompt
    assert "==== FILE: b.css ====" in prompt
