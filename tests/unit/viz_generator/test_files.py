"""Unit tests for backend.viz_generator.files."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.viz_generator.files import (
    ALLOWED_FILE_EXTENSIONS,
    ERROR_DISPLAY_MAX_LINES,
    _validate_filepath,
    enforce_pinned_deps,
    write_to_disk,
)


# ---------------------------------------------------------------------------
# _validate_filepath
# ---------------------------------------------------------------------------

def test_validate_filepath_rejects_traversal(tmp_path):
    with pytest.raises(ValueError, match="Path traversal blocked"):
        _validate_filepath(tmp_path, "../../../etc/passwd")


def test_validate_filepath_accepts_normal_path(tmp_path):
    p = _validate_filepath(tmp_path, "src/App.jsx")
    # _validate_filepath calls .resolve() internally so the returned path
    # is also resolved (on macOS /tmp → /private/tmp)
    assert p == (tmp_path / "src" / "App.jsx").resolve()


def test_validate_filepath_accepts_root_level_file(tmp_path):
    p = _validate_filepath(tmp_path, "index.html")
    assert p == (tmp_path / "index.html").resolve()


def test_validate_filepath_rejects_disallowed_extension(tmp_path):
    with pytest.raises(ValueError, match="Disallowed file extension"):
        _validate_filepath(tmp_path, "src/script.sh")


def test_validate_filepath_allowed_extensions_cover_expected_types(tmp_path):
    for ext in (".tsx", ".ts", ".jsx", ".js", ".css", ".html", ".json"):
        p = _validate_filepath(tmp_path, f"src/file{ext}")
        assert p.suffix == ext


# ---------------------------------------------------------------------------
# enforce_pinned_deps
# ---------------------------------------------------------------------------

def test_enforce_pinned_deps_strips_caret(tmp_path):
    files = {
        "package.json": json.dumps({"dependencies": {"react": "^18.0.0"}}),
    }
    out = enforce_pinned_deps(files)
    pkg = json.loads(out["package.json"])
    assert pkg["dependencies"]["react"] == "18.0.0"


def test_enforce_pinned_deps_strips_tilde(tmp_path):
    files = {
        "package.json": json.dumps({"dependencies": {"vite": "~4.3.0"}}),
    }
    out = enforce_pinned_deps(files)
    pkg = json.loads(out["package.json"])
    assert pkg["dependencies"]["vite"] == "4.3.0"


def test_enforce_pinned_deps_strips_gte_range():
    files = {
        "package.json": json.dumps({"dependencies": {"lodash": ">=4.0.0"}}),
    }
    out = enforce_pinned_deps(files)
    pkg = json.loads(out["package.json"])
    assert pkg["dependencies"]["lodash"] == "4.0.0"


def test_enforce_pinned_deps_no_package_json_unchanged():
    files = {"src/App.jsx": "// app"}
    out = enforce_pinned_deps(files)
    assert out == files


def test_enforce_pinned_deps_invalid_json_unchanged():
    files = {"package.json": "NOT JSON"}
    out = enforce_pinned_deps(files)
    assert out == files


def test_enforce_pinned_deps_preserves_non_package_files():
    files = {
        "package.json": json.dumps({"dependencies": {"react": "^18.0.0"}}),
        "src/App.jsx": "// app",
    }
    out = enforce_pinned_deps(files)
    assert out["src/App.jsx"] == "// app"


def test_enforce_pinned_deps_handles_dev_dependencies():
    files = {
        "package.json": json.dumps({
            "dependencies": {"react": "^18.0.0"},
            "devDependencies": {"typescript": "^5.0.0"},
        }),
    }
    out = enforce_pinned_deps(files)
    pkg = json.loads(out["package.json"])
    assert pkg["devDependencies"]["typescript"] == "5.0.0"


# ---------------------------------------------------------------------------
# write_to_disk
# ---------------------------------------------------------------------------

def test_write_to_disk_creates_files(tmp_path):
    files = {
        "src/App.jsx": "export default function App() {}",
        "index.html": "<!DOCTYPE html>",
    }
    write_to_disk(tmp_path, files)
    assert (tmp_path / "src" / "App.jsx").exists()
    assert (tmp_path / "index.html").exists()


def test_write_to_disk_creates_src_directory(tmp_path):
    files = {"src/App.jsx": "// app"}
    write_to_disk(tmp_path, files)
    assert (tmp_path / "src").is_dir()


def test_write_to_disk_redirects_misplaced_root_file(tmp_path):
    # D10: if LLM outputs "src/index.html", it should land at root/index.html
    files = {"src/index.html": "<!DOCTYPE html>"}
    write_to_disk(tmp_path, files)
    assert (tmp_path / "index.html").exists()
    assert not (tmp_path / "src" / "index.html").exists()


def test_write_to_disk_skips_traversal_filenames(tmp_path):
    files = {"../../../etc/passwd": "evil"}
    write_to_disk(tmp_path, files)
    # No file should be created outside tmp_path
    assert not Path("/etc/passwd").exists() or Path("/etc/passwd").read_text() != "evil"


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------

def test_error_display_max_lines_is_positive():
    assert ERROR_DISPLAY_MAX_LINES > 0


def test_allowed_file_extensions_contains_jsx():
    assert ".jsx" in ALLOWED_FILE_EXTENSIONS
    assert ".tsx" in ALLOWED_FILE_EXTENSIONS
    assert ".json" in ALLOWED_FILE_EXTENSIONS
