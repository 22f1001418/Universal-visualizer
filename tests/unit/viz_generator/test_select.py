"""Unit tests for backend.viz_generator.select."""
from __future__ import annotations

from backend.viz_generator.select import merge_patches, format_files_compact


def test_merge_patches_overrides_existing_file():
    original = {"src/App.jsx": "old", "src/main.tsx": "unchanged"}
    patches = {"src/App.jsx": "new"}
    merged = merge_patches(original, patches)
    assert merged["src/App.jsx"] == "new"
    assert merged["src/main.tsx"] == "unchanged"


def test_merge_patches_adds_new_file():
    original = {"src/App.jsx": "x"}
    patches = {"src/utils.ts": "export const a = 1;"}
    merged = merge_patches(original, patches)
    assert "src/utils.ts" in merged
    assert "src/App.jsx" in merged


def test_format_files_compact_lists_paths():
    files = {"src/App.jsx": "// code", "package.json": "{}"}
    out = format_files_compact(files)
    assert "src/App.jsx" in out
    assert "package.json" in out
