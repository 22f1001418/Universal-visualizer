"""On-disk file operations for the viz generator.

Validates filepaths (no traversal), writes the parsed multi-file project to
the output directory, pins dependency versions in package.json, and filters
bogus filenames the LLM occasionally hallucinates.

print_error_block lives here too — it's a small utility for nicely
formatting error blocks in subprocess stdout (used heavily by the fix
loops).
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger("viz_agent")

# ---------------------------------------------------------------------------
# Module-level constants (moved from fixed_main_v6.py)
# ---------------------------------------------------------------------------

ERROR_DISPLAY_MAX_LINES: int = 40

# Allowed file extensions that the LLM may produce
ALLOWED_FILE_EXTENSIONS: set[str] = {
    ".tsx", ".ts", ".jsx", ".js", ".cjs", ".mjs",
    ".css", ".html", ".json",
}

# D10 fix: root files that must NOT end up inside src/
_ROOT_ONLY_FILES = frozenset({
    "index.html", "vite.config.ts", "vite.config.js",
    "tsconfig.json", "tsconfig.node.json",
    "tailwind.config.js", "postcss.config.js", "package.json",
    "package-lock.json", ".eslintrc.cjs", ".eslintrc.js",
})


def _filter_bogus_files(files: dict[str, str]) -> dict[str, str]:
    """Remove obviously-fake filenames produced by LLMs splitting composites."""
    from backend.viz_generator.parsing import _BOGUS_STANDALONE
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


def _validate_filepath(project_dir: Path, filename: str) -> Path:
    """
    Validate that the filename resolves inside project_dir,
    and only has an allowed extension.
    Raises ValueError on path traversal or disallowed extension.
    """
    resolved = (project_dir / filename).resolve()
    project_resolved = project_dir.resolve()
    if not str(resolved).startswith(str(project_resolved) + os.sep) and resolved != project_resolved:
        raise ValueError(
            f"Path traversal blocked — '{filename}' resolves outside project dir"
        )
    ext = resolved.suffix.lower()
    if ext and ext not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError(
            f"Disallowed file extension '{ext}' for '{filename}'. "
            f"Allowed: {sorted(ALLOWED_FILE_EXTENSIONS)}"
        )
    return resolved


def write_to_disk(project_dir: Path, files: dict[str, str]) -> None:
    (project_dir / "src").mkdir(exist_ok=True, parents=True)
    for filename, content in files.items():
        # D10: redirect misplaced root files (e.g. LLM outputs "src/index.html")
        basename = Path(filename).name
        if basename in _ROOT_ONLY_FILES and filename != basename:
            log.warning(
                "  D10: LLM placed '%s' inside a subdirectory — "
                "redirecting to project root as '%s'.", filename, basename
            )
            filename = basename
        try:
            fp = _validate_filepath(project_dir, filename)
        except ValueError as e:
            log.warning("  Skipping file '%s': %s", filename, e)
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")


def enforce_pinned_deps(files: dict[str, str]) -> dict[str, str]:
    """Strip version range prefixes from package.json dependencies.

    LOGICAL-8: Handles ^, ~, >=, >, <=, <, *, x, workspace: ranges in addition
    to the previously handled ^ and ~ cases.
    """
    if "package.json" not in files:
        return files
    try:
        pkg = json.loads(files["package.json"])
    except json.JSONDecodeError:
        return files

    _range_prefix = re.compile(
        r"^(workspace:[~^]?|[><=~^]+\s*)"
    )

    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps = pkg.get(section)
        if isinstance(deps, dict):
            pinned: dict[str, Any] = {}
            for k, v in deps.items():
                if not isinstance(v, str):
                    pinned[k] = v
                    continue
                stripped = _range_prefix.sub("", v).strip()
                # Treat bare "*" or "x" as unpinnable — leave as-is and warn
                if stripped in ("*", "x", ""):
                    log.warning(
                        "  ⚠️  Cannot pin '%s': '%s' has no concrete version.", k, v
                    )
                    pinned[k] = v
                else:
                    pinned[k] = stripped
            pkg[section] = pinned

    files["package.json"] = json.dumps(pkg, indent=2)
    return files


def print_error_block(label: str, text: str, max_lines: int = ERROR_DISPLAY_MAX_LINES) -> None:
    """Print an error/output block to the terminal in a visible format."""
    text = (text or "").strip()
    if not text:
        log.info("  ⚠️  %s: (no output captured)", label)
        return
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    shown = lines[-max_lines:] if truncated else lines
    log.info("\n  ━━━ %s ━━━", label)
    if truncated:
        log.info("  (showing last %d of %d lines)", max_lines, len(lines))
    for line in shown:
        log.info("  | %s", line)
    log.info("  ━━━ end %s ━━━\n", label)
