"""Post-build patches applied to the generated viz project before it's
published or previewed.

_patch_vite_config_base: ensures vite.config.{ts,js} has `base: './'` so
the production build works when served from a sub-path (e.g., GitHub
Pages preview path). Real-bug history: commit 0099c84.

_inject_error_boundary: wraps the app entry in a React ErrorBoundary so
runtime errors render a friendly fallback instead of a blank white screen.
Real-bug history: commit 68ddd9c.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable, Optional


_VITE_BASE_RE = re.compile(r"base\s*:\s*['\"]([^'\"]*)['\"]")


def _patch_vite_config_base(project_dir: Path, on_log) -> None:
    """Ensure the generated vite.config.{ts,js} sets `base: './'`.

    The viz generator emits a vite.config.ts without a `base` field, which makes
    `vite build` produce a dist/index.html with absolute asset paths (`/assets/…`).
    That works on a root-domain deploy but breaks on any subpath deploy. Setting
    `base: './'` makes the paths relative and works in both cases.

    Idempotent: if `base` is already set to './' or '', leaves the file alone.
    If `base` is set to something else, rewrites it. Tries .ts first, then .js.
    """
    for name in ("vite.config.ts", "vite.config.js", "vite.config.mts"):
        cfg = project_dir / name
        if not cfg.exists():
            continue
        src = cfg.read_text(encoding="utf-8")

        existing = _VITE_BASE_RE.search(src)
        if existing:
            if existing.group(1) in ("./", ""):
                on_log(f"[vite-config] {name} already has base='{existing.group(1)}' — skipping")
                return
            new_src = _VITE_BASE_RE.sub("base: './'", src, count=1)
        else:
            # Inject `base: './'` as the first key inside `defineConfig({ … })`.
            # The regex is forgiving about whitespace and call style.
            new_src, n = re.subn(
                r"defineConfig\s*\(\s*\{",
                "defineConfig({\n  base: './',",
                src,
                count=1,
            )
            if n == 0:
                on_log(f"[vite-config] {name}: defineConfig({{...}}) not found, skipping")
                return

        cfg.write_text(new_src, encoding="utf-8")
        on_log(f"[vite-config] patched {name} → base: './'")
        return

    on_log("[vite-config] no vite.config.{ts,js,mts} found — skipping patch")


# ── ErrorBoundary injection ───────────────────────────────────────────────────
# If the LLM-generated App throws during the first render, React 18 unmounts the
# whole tree by default → a blank/black page on the deployed host with no clue
# about what went wrong. We wrap <App/> in a class-based ErrorBoundary so the
# actual error message + stack is visible on the page itself. Uses inline styles
# so it works even if Tailwind / index.css failed to load.

_ERROR_BOUNDARY_SOURCE = '''import { Component, ErrorInfo, ReactNode } from 'react';

interface State {
  error: Error | null;
  info: ErrorInfo | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null, info: null };

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Surfaces in the deployed page's DevTools console too.
    // eslint-disable-next-line no-console
    console.error('[ErrorBoundary]', error, info);
    this.setState({ info });
  }

  render(): ReactNode {
    if (!this.state.error) return this.props.children;
    return (
      <div style={{
        minHeight: '100vh',
        padding: '32px',
        background: '#0f1117',
        color: '#fca5a5',
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        fontSize: '14px',
        lineHeight: 1.55,
        overflow: 'auto',
      }}>
        <div style={{ maxWidth: 880, margin: '0 auto' }}>
          <h1 style={{ color: '#f87171', fontSize: '20px', marginBottom: 12 }}>
            Visualization failed to render
          </h1>
          <p style={{ color: '#cbd5e1', marginBottom: 18 }}>
            A JavaScript error was thrown during the first render. The viz
            cannot mount until this is fixed. Details below.
          </p>
          <div style={{
            background: '#1a1d28',
            border: '1px solid #3f3f46',
            borderRadius: 6,
            padding: '14px 16px',
            marginBottom: 14,
          }}>
            <div style={{ color: '#fca5a5', fontWeight: 600, marginBottom: 6 }}>
              {this.state.error.name}: {this.state.error.message}
            </div>
            {this.state.error.stack && (
              <pre style={{ whiteSpace: 'pre-wrap', color: '#94a3b8', margin: 0 }}>
                {this.state.error.stack}
              </pre>
            )}
          </div>
          {this.state.info?.componentStack && (
            <details>
              <summary style={{ cursor: 'pointer', color: '#cbd5e1' }}>
                React component stack
              </summary>
              <pre style={{ whiteSpace: 'pre-wrap', color: '#94a3b8', marginTop: 10 }}>
                {this.state.info.componentStack}
              </pre>
            </details>
          )}
        </div>
      </div>
    );
  }
}
'''


def _inject_error_boundary(project_dir: Path, on_log) -> None:
    """Wrap <App/> in src/main.tsx with an ErrorBoundary so first-render
    crashes show a visible diagnostic on the deployed page instead of a
    blank screen. Idempotent — re-running on a patched project is a no-op.
    """
    src_dir = project_dir / "src"
    if not src_dir.is_dir():
        on_log("[error-boundary] no src/ — skipping")
        return

    boundary_path = src_dir / "ErrorBoundary.tsx"
    if not boundary_path.exists():
        boundary_path.write_text(_ERROR_BOUNDARY_SOURCE, encoding="utf-8")
        on_log("[error-boundary] wrote src/ErrorBoundary.tsx")

    # Find the project's entry file. main.tsx is the Vite default; fall back to
    # index.tsx if the LLM picked that name instead.
    entry: Optional[Path] = None
    for candidate in ("main.tsx", "index.tsx", "main.jsx", "index.jsx"):
        p = src_dir / candidate
        if p.exists():
            entry = p
            break
    if entry is None:
        on_log("[error-boundary] no src/main.tsx or index.tsx — skipping wrap")
        return

    text = entry.read_text(encoding="utf-8")
    if "ErrorBoundary" in text:
        on_log(f"[error-boundary] {entry.name} already wraps in ErrorBoundary — skipping")
        return

    # 1. Add the import after the existing imports.
    import_line = "import { ErrorBoundary } from './ErrorBoundary';"
    if "from 'react-dom/client'" in text:
        new_text = text.replace(
            "from 'react-dom/client';",
            f"from 'react-dom/client';\n{import_line}",
            1,
        )
    else:
        new_text = import_line + "\n" + text

    # 2. Wrap the first <App /> usage. The generator emits one of these shapes;
    #    we cover both. JSX self-closing vs explicit pair.
    replacements = [
        ("<App />",       "<ErrorBoundary><App /></ErrorBoundary>"),
        ("<App/>",        "<ErrorBoundary><App/></ErrorBoundary>"),
        ("<App></App>",   "<ErrorBoundary><App></App></ErrorBoundary>"),
    ]
    wrapped = False
    for old, new in replacements:
        if old in new_text and "ErrorBoundary" not in old:
            new_text = new_text.replace(old, new, 1)
            wrapped = True
            break

    if not wrapped:
        on_log(f"[error-boundary] couldn't locate <App/> in {entry.name} — skipping wrap")
        return

    entry.write_text(new_text, encoding="utf-8")
    on_log(f"[error-boundary] wrapped <App/> in {entry.name}")
