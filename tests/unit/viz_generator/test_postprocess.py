"""Unit tests for backend.viz_generator.postprocess.

Real bug history in this code path — see commits 0099c84 (base:'./' must
apply to .js config too) and 68ddd9c (ErrorBoundary injection). These tests
are regression guards for both fixes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.viz_generator.postprocess import (
    _patch_vite_config_base,
    _inject_error_boundary,
)


def _noop_log(msg: str) -> None:
    pass


def test_patch_vite_config_base_for_ts(tmp_path):
    cfg = tmp_path / "vite.config.ts"
    cfg.write_text("""import { defineConfig } from 'vite';
export default defineConfig({
  plugins: [react()],
});
""")
    _patch_vite_config_base(tmp_path, _noop_log)
    out = cfg.read_text()
    assert "base:" in out
    assert "'./'" in out or '"./"' in out


def test_patch_vite_config_base_for_js(tmp_path):
    cfg = tmp_path / "vite.config.js"
    cfg.write_text("""import { defineConfig } from 'vite';
export default defineConfig({
  plugins: [react()],
});
""")
    _patch_vite_config_base(tmp_path, _noop_log)
    out = cfg.read_text()
    assert "base:" in out
    assert "'./'" in out or '"./"' in out


def test_patch_vite_config_base_idempotent(tmp_path):
    """If base is already './', the file must not be modified."""
    original = """import { defineConfig } from 'vite';
export default defineConfig({
  base: './',
  plugins: [react()],
});
"""
    cfg = tmp_path / "vite.config.ts"
    cfg.write_text(original)
    _patch_vite_config_base(tmp_path, _noop_log)
    assert cfg.read_text() == original


def test_patch_vite_config_base_rewrites_wrong_base(tmp_path):
    """If base is set to something other than './', it should be rewritten."""
    cfg = tmp_path / "vite.config.ts"
    cfg.write_text("""import { defineConfig } from 'vite';
export default defineConfig({
  base: '/app/',
  plugins: [react()],
});
""")
    _patch_vite_config_base(tmp_path, _noop_log)
    out = cfg.read_text()
    assert "base: './'" in out
    assert "/app/" not in out


def test_patch_vite_config_base_no_config_file(tmp_path):
    """Should not raise if no vite config file exists."""
    logs: list[str] = []
    _patch_vite_config_base(tmp_path, logs.append)
    assert any("skipping patch" in m for m in logs)


def test_inject_error_boundary_creates_boundary_file(tmp_path):
    """ErrorBoundary.tsx must be created under src/."""
    src = tmp_path / "src"
    src.mkdir()
    main_tsx = src / "main.tsx"
    main_tsx.write_text("""import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
""")
    (src / "App.tsx").write_text("export default function App() { return null; }")
    _inject_error_boundary(tmp_path, _noop_log)

    boundary_file = src / "ErrorBoundary.tsx"
    assert boundary_file.exists(), "ErrorBoundary.tsx must be written to src/"
    assert "ErrorBoundary" in boundary_file.read_text()


def test_inject_error_boundary_modifies_main_tsx(tmp_path):
    """main.tsx must import ErrorBoundary and wrap <App />."""
    src = tmp_path / "src"
    src.mkdir()
    main_tsx = src / "main.tsx"
    main_tsx.write_text("""import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
""")
    (src / "App.tsx").write_text("export default function App() { return null; }")
    _inject_error_boundary(tmp_path, _noop_log)

    after = main_tsx.read_text()
    assert "ErrorBoundary" in after, "main.tsx must reference ErrorBoundary after injection"
    assert "<ErrorBoundary>" in after, "main.tsx must wrap <App /> in <ErrorBoundary>"


def test_inject_error_boundary_idempotent(tmp_path):
    """Running inject twice must not double-wrap or raise."""
    src = tmp_path / "src"
    src.mkdir()
    main_tsx = src / "main.tsx"
    main_tsx.write_text("""import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
""")
    (src / "App.tsx").write_text("export default function App() { return null; }")

    _inject_error_boundary(tmp_path, _noop_log)
    text_after_first = main_tsx.read_text()
    _inject_error_boundary(tmp_path, _noop_log)
    text_after_second = main_tsx.read_text()

    assert text_after_first == text_after_second, "Second inject must be a no-op"


def test_inject_error_boundary_no_src_dir(tmp_path):
    """Should not raise when src/ directory does not exist."""
    logs: list[str] = []
    _inject_error_boundary(tmp_path, logs.append)
    assert any("skipping" in m for m in logs)
