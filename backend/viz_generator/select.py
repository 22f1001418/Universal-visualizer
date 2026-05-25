"""File-selection helpers for the viz generator fix loops.

When a build or runtime error happens, the fix loop asks the LLM which
subset of project files needs to be re-emitted. These helpers ask the LLM
(select_relevant_files), format a compact representation of the project
(format_files_compact), and merge LLM-emitted patches back over the
original files (merge_patches).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger("viz_agent")


# ─────────────────────────────────────────────────────────────
# ERROR-FILE HINTS
# ─────────────────────────────────────────────────────────────

_ERROR_FILE_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"cannot find module ['\"]([^'\"]+)['\"]", re.I), []),
    (re.compile(r"tailwind|postcss|@tailwind", re.I),                    ["tailwind.config.js", "postcss.config.js", "src/index.css"]),
    (re.compile(r"vite|defineConfig|plugin-react", re.I),                ["vite.config.ts"]),
    (re.compile(r"tsconfig|TS\d{4}|TS6310|TS5055", re.I),              ["tsconfig.json", "tsconfig.node.json"]),
    (re.compile(r"package\.json|npm err|peer dep|ERESOLVE", re.I),      ["package.json"]),
    (re.compile(r"index\.html|<script|<head>|module entry", re.I),      ["index.html", "src/main.tsx"]),
    (re.compile(r"createRoot|ReactDOM|StrictMode", re.I),                ["src/main.tsx"]),
    (re.compile(r"useAnimation|setInterval|stepIdx", re.I),              ["src/hooks/useAnimation.ts", "src/App.tsx"]),
    (re.compile(r"useStore|zustand", re.I),                              ["src/store/useStore.ts"]),
    (re.compile(r"\.css|@tailwind|--accent|glass-panel|mesh-bg", re.I), ["src/index.css"]),
]


# ─────────────────────────────────────────────────────────────
# FILE SELECTION
# ─────────────────────────────────────────────────────────────

def select_relevant_files(
    files: dict[str, str],
    error_log: str,
    always_include: tuple[str, ...] = ("package.json",),
    max_files: int = 5,
) -> dict[str, str]:
    """Pick the subset of files most likely to contain the bug for the given error.

    Strategy:
      1. Always include `always_include` (package.json so the LLM sees deps).
      2. Walk error keyword regex table; add any matched file that exists.
      3. Walk explicit "Cannot find module 'X'" mentions; add X if it's in the codebase.
      4. Walk filenames mentioned literally in the error (e.g. "src/App.tsx:42").
      5. Cap at max_files. If nothing matched, fall back to App.tsx + main.tsx + package.json.
    """
    selected: dict[str, str] = {}

    # 1. Always include
    for name in always_include:
        if name in files:
            selected[name] = files[name]

    # 2. Keyword regex hints
    for pattern, candidates in _ERROR_FILE_HINTS:
        if pattern.search(error_log):
            for c in candidates:
                if c in files and c not in selected:
                    selected[c] = files[c]
                    if len(selected) >= max_files:
                        return selected

    # 3. "Cannot find module 'X'" — try to resolve X
    for m in re.finditer(r"cannot find module ['\"]([^'\"]+)['\"]", error_log, re.I):
        modspec = m.group(1).lstrip("./")
        for fname in files:
            if fname.endswith(modspec) or fname.endswith(modspec + ".tsx") or fname.endswith(modspec + ".ts"):
                if fname not in selected:
                    selected[fname] = files[fname]

    # 4. Literal filename mentions in the error log.
    # IMPORTANT: use word-boundary regex anchored to either the path or the basename,
    # NOT a plain substring match — otherwise "node.json" matches inside
    # "tsconfig.node.json" and produces phantom selections.
    for fname in files:
        if fname in selected:
            continue
        # Build a regex that matches the full filename (or just its basename) at a
        # path boundary: start of string, whitespace, slash, quote, or colon.
        basename = Path(fname).name
        boundary = r"(?:^|[\s/\\\"':])"
        # Escape both forms — try full path first, then basename
        full_pat = boundary + re.escape(fname) + r"(?:[\s:,\"']|$)"
        base_pat = boundary + re.escape(basename) + r"(?:[\s:,\"']|$)"
        if re.search(full_pat, error_log) or re.search(base_pat, error_log):
            selected[fname] = files[fname]
            if len(selected) >= max_files:
                break

    # 5. Sensible fallback
    if not selected or "src/App.tsx" not in selected:
        for fallback in ("src/App.tsx", "src/main.tsx", "package.json"):
            if fallback in files and fallback not in selected:
                selected[fallback] = files[fallback]

    # Cap
    if len(selected) > max_files:
        selected = dict(list(selected.items())[:max_files])

    return selected


def format_files_compact(files: dict[str, str]) -> str:
    """Same delimiter format but used for SUBSET file lists in patch prompts."""
    return "\n\n".join(
        f"==== FILE: {n} ====\n{c}\n==== END FILE ===="
        for n, c in files.items()
    )


def merge_patches(original: dict[str, str], patches: dict[str, str]) -> dict[str, str]:
    """Merge LLM-returned partial files back into the full codebase."""
    merged = dict(original)
    for fname, content in patches.items():
        if fname in merged:
            log.info("  [Patch] Updating %s (%d -> %d chars)",
                     fname, len(merged[fname]), len(content))
        else:
            log.info("  [Patch] Adding new file %s (%d chars)", fname, len(content))
        merged[fname] = content
    return merged
