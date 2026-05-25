"""File-format parsing helpers for the viz generator.

The LLM outputs a multi-file React+Vite project in one of two formats —
the marker format (custom delimiters) or the fenced-codeblock format.
This module owns the parsers and the shared helpers (filename sniff,
content cleaning).
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("viz_agent")

# ---------------------------------------------------------------------------
# Module-level constants (moved from fixed_main_v6.py)
# ---------------------------------------------------------------------------

PROMPT_SIZE_WARN_CHARS: int = 200_000

_FILENAME_HINT = re.compile(
    r"(?:[\w\-./]+/)?[\w\-]+\.(?:tsx?|jsx?|css|html|json|js|cjs|mjs)\b"
)

# Filenames that are almost always the result of an LLM splitting a composite
# filename (e.g. "tsconfig.node.json" gets split into "tsconfig.json" + "node.json").
# When seen as standalone filenames they're fake and must be discarded.
_BOGUS_STANDALONE = frozenset({
    "node.json",          # split from tsconfig.node.json
    "config.js",          # split from vite.config.js / tailwind.config.js
    "config.ts",          # split from vite.config.ts
    "vite.ts",            # split from vite.config.ts
    "tailwind.js",        # split from tailwind.config.js
    "postcss.js",         # split from postcss.config.js
})


def _filter_bogus_files(files: dict[str, str]) -> dict[str, str]:
    """Remove obviously-fake filenames produced by LLMs splitting composites."""
    cleaned: dict[str, str] = {}
    for name, body in files.items():
        if Path(name).name in _BOGUS_STANDALONE:
            log.warning(
                "  ⚠️  Rejecting suspicious filename '%s' — likely an LLM split "
                "of a composite name. Skipping.", name
            )
            continue
        cleaned[name] = body
    return cleaned


def parse_files(text: str) -> dict[str, str]:
    """
    Parse files from LLM output. Tolerates several formats:
      1. ==== FILE: name ====  ...  ==== END FILE ====   (preferred)
      2. ```lang src/foo.tsx \n ...  ```                  (markdown code block w/ filename)
      3. **File: name** \n ```lang \n ... \n ```          (bold header + code block)
      4. ### name \n ```lang \n ... \n ```                (markdown heading + code block)
      5. // File: name \n ...                             (inline comment header)
    Returns {} if nothing parseable found.
    """
    files = _parse_marker_format(text)
    if files:
        return _filter_bogus_files(files)
    return _filter_bogus_files(_parse_codeblock_format(text))


def _parse_marker_format(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    lines = text.split("\n")
    current_file: str | None = None
    current_content: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("==== FILE:"):
            if current_file:
                files[current_file] = _clean_file_content(current_content)
            current_file = stripped.replace("==== FILE:", "").replace("====", "").strip()
            current_content = []
        elif stripped.startswith("==== END FILE"):
            if current_file:
                files[current_file] = _clean_file_content(current_content)
            current_file = None
            current_content = []
        else:
            if current_file is not None:
                if line.startswith("```") and not current_content:
                    continue
                current_content.append(line)
    if current_file and current_content:
        files[current_file] = _clean_file_content(current_content)
    return files


def _clean_file_content(lines: list[str]) -> str:
    """Join content lines and strip only leading/trailing backtick fences and whitespace."""
    content = "\n".join(lines).strip()
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _parse_codeblock_format(text: str) -> dict[str, str]:
    """
    Look for fenced code blocks where the filename appears either:
      - in the fence info string:   ```tsx src/App.tsx
      - in the line just above the fence (heading, bold, or comment style)
    """
    files: dict[str, str] = {}
    lines = text.split("\n")
    i = 0
    pending_filename: str | None = None
    pending_line_idx: int = -1
    while i < len(lines):
        line = lines[i]
        fence_match = re.match(r"^\s*```([\w+\-]*)\s*(.*)$", line)
        if fence_match:
            rest = fence_match.group(2).strip()
            fn_in_fence = _extract_filename(rest)
            filename = fn_in_fence or pending_filename
            pending_filename = None
            content_lines: list[str] = []
            i += 1
            # MINOR-4: closing fence accepts optional trailing language tag or whitespace
            while i < len(lines) and not re.match(r"^\s*```\w*\s*$", lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            if filename:
                files[filename] = "\n".join(content_lines).rstrip()
            continue

        fn_hint = _extract_filename(line)
        if fn_hint:
            pending_filename = fn_hint
            pending_line_idx = i
        elif line.strip():
            if pending_filename and (i - pending_line_idx) > 2:
                pending_filename = None
        i += 1
    return files


def _extract_filename(s: str) -> str | None:
    """Pull a path-looking token out of a line (or None if none looks plausible)."""
    s = s.strip().strip("*#<>:").strip()
    s = re.sub(r"^(file|filename|path)\s*[:=]\s*", "", s, flags=re.IGNORECASE)
    s = s.rstrip(":")
    m = _FILENAME_HINT.search(s)
    if not m:
        return None
    candidate = m.group(0)
    if len(candidate) > 80 or candidate.count(" ") > 0:
        return None
    return candidate


def format_files_for_prompt(files: dict[str, str]) -> str:
    """Serialize files dict into the ==== FILE ==== format for LLM prompts.

    MINOR-3: Warns when the serialized codebase is large enough to risk
    overflowing smaller model context windows.
    """
    result = "\n\n".join(
        f"==== FILE: {n} ====\n{c}\n==== END FILE ===="
        for n, c in files.items()
    )
    if len(result) > PROMPT_SIZE_WARN_CHARS:
        log.warning(
            "  ⚠️  Codebase prompt is %d chars — may approach context window limits "
            "on smaller models. Consider reducing file count.",
            len(result),
        )
    return result
