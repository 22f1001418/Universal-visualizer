"""On-disk file helpers for the vanilla viz generator.

extract_html strips optional ```html``` code fences from the LLM response.
pre_validate_html runs cheap structural checks before we pay for Chromium.
write_html_to_disk writes the single index.html for a viz.
print_error_block formats error blocks for the viz_agent logger.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger("viz_agent")

ERROR_DISPLAY_MAX_LINES: int = 40

_HTML_FENCE_RE = re.compile(
    r"^\s*```(?:html|HTML)?\s*\n(?P<body>.*?)\n```\s*$",
    re.DOTALL,
)

# Heuristic for "this looks like HTML at all" — used by extract_html so we
# fail loudly when the LLM apologizes or returns Markdown instead of HTML.
_LOOKS_HTML_RE = re.compile(r"<(?:!doctype|html|body)\b", re.IGNORECASE)

# Window at the end of the document we scan for a closing tag — truncated
# generations stop mid-token and leave neither </body> nor </html>.
_TRUNCATION_TAIL_LEN: int = 400

# Catches `<script>` followed only by whitespace before `</script>`. An
# empty inline script is almost always a sign the LLM ran out of tokens.
_EMPTY_SCRIPT_RE = re.compile(r"<script[^>]*>\s*</script>", re.IGNORECASE)


def extract_html(raw: str) -> str:
    """Return the HTML body of the LLM's response.

    Strips a single surrounding ```html ... ``` (or plain ``` ... ```) fence
    if present, then trims whitespace. Raises ValueError if the result does
    not look like an HTML document.
    """
    s = raw.strip()
    m = _HTML_FENCE_RE.match(s)
    if m:
        s = m.group("body").strip()
    if not _LOOKS_HTML_RE.search(s):
        raise ValueError("no html detected in LLM output")
    return s


def pre_validate_html(html: str) -> list[str]:
    """Fast structural checks; returns problem descriptions (empty = OK).

    Cheap pre-flight before the validator launches Chromium. Catches the
    common LLM failure modes (truncation, missing required tags, empty
    inline scripts) so we don't pay browser-startup cost on a generation
    that can't possibly work.
    """
    problems: list[str] = []
    s = html.strip()
    low = s.lower()
    if "<html" not in low:
        problems.append("missing <html> tag")
    if "<body" not in low:
        problems.append("missing <body> tag")
    tail = low[-_TRUNCATION_TAIL_LEN:]
    if "</body>" not in tail and "</html>" not in tail:
        problems.append("appears truncated (no closing </body> or </html> near end)")
    if _EMPTY_SCRIPT_RE.search(s):
        problems.append("empty <script> tag (likely truncated mid-generation)")
    return problems


def write_html_to_disk(project_dir: Path, html: str) -> None:
    """Write `html` as `index.html` under `project_dir`. Refuses empty input."""
    if not html or not html.strip():
        raise ValueError("refusing to write empty html")
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "index.html").write_text(html, encoding="utf-8")


def print_error_block(
    label: str,
    text: str,
    max_lines: int = ERROR_DISPLAY_MAX_LINES,
) -> None:
    """Render an error/output block to the viz_agent logger."""
    text = (text or "").strip()
    if not text:
        log.info("  %s: (no output captured)", label)
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
