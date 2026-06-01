"""Single-launch Playwright validator + screenshot for a vanilla HTML viz.

Severity model:
  • pageerror (uncaught exception) → FATAL: the viz crashed.
  • console.error → WARNING by default: many libs / devtools log non-fatal
    noise to console.error. Surface it in `warnings` for diagnostics but
    don't fail. Set env VALIDATOR_STRICT_CONSOLE=1 to make it fatal.
  • Empty / minimal-content body → FATAL: catches blank pages.

We check DOM presence + non-empty innerText rather than bounding_box —
bounding_box can race init JS and time out spuriously on slow renders.
The fix-loop policy (one iteration max) lives in `phases/draft.py`.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("viz_agent")

PAGE_GOTO_TIMEOUT_MS: int = 10_000
VIEWPORT = {"width": 1280, "height": 800}

# Minimum body innerHTML length to consider the page non-empty. Catches
# bodies that contain only whitespace, comments, or a stray <noscript>.
MIN_BODY_HTML_LEN: int = 30


@dataclass(frozen=True)
class ValidationResult:
    success: bool
    error_log: str = ""
    screenshot_path: str = ""
    warnings: str = ""   # non-fatal diagnostics (e.g. console.error in default mode)


def _strict_console() -> bool:
    return os.getenv("VALIDATOR_STRICT_CONSOLE", "").strip() in ("1", "true", "yes")


def validate(html: str, project_dir: Path) -> ValidationResult:
    """Load `html` in Chromium, capture errors, screenshot on success."""
    project_dir.mkdir(parents=True, exist_ok=True)
    html_path = project_dir / "index.html"
    html_path.write_text(html, encoding="utf-8")

    from playwright.sync_api import sync_playwright

    errors: list[str] = []
    warnings: list[str] = []
    strict = _strict_console()
    screenshot_path = project_dir / "screenshot.png"

    with sync_playwright() as p:
        # Memory/stability flags for constrained containers (e.g. Render free,
        # 512MB). --disable-dev-shm-usage avoids the tiny /dev/shm that crashes
        # Chromium in Docker; --single-process is the biggest RAM reduction
        # (drop it first if screenshots glitch); --no-sandbox is required when
        # running as a non-root container user.
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ],
        )
        try:
            context = browser.new_context(viewport=VIEWPORT)
            page = context.new_page()

            # pageerror → fatal. The viz crashed at runtime.
            page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))

            def _on_console(msg):
                if msg.type == "error":
                    line = f"console.error: {msg.text}"
                    (errors if strict else warnings).append(line)
                elif msg.type == "warning":
                    warnings.append(f"console.warning: {msg.text}")

            page.on("console", _on_console)

            try:
                page.goto(
                    f"file://{html_path.resolve()}",
                    wait_until="networkidle",
                    timeout=PAGE_GOTO_TIMEOUT_MS,
                )
            except Exception as exc:
                errors.append(f"navigation: {exc}")

            # DOM presence + text check. Cheaper and more robust than
            # bounding_box (which races init JS and can time out).
            try:
                body_html = page.evaluate(
                    "document.body ? document.body.innerHTML.trim() : ''"
                )
                body_text = page.evaluate(
                    "document.body ? document.body.innerText.trim() : ''"
                )
                if not body_html:
                    errors.append("empty body: <body> has no children")
                elif len(body_html) < MIN_BODY_HTML_LEN and not body_text:
                    errors.append(
                        f"empty body: minimal content "
                        f"(html={len(body_html)} chars, text={len(body_text)} chars)"
                    )
            except Exception as exc:
                errors.append(f"DOM read error: {exc}")

            if errors:
                return ValidationResult(
                    success=False,
                    error_log="\n".join(errors),
                    warnings="\n".join(warnings),
                )

            page.screenshot(path=str(screenshot_path), full_page=False)
        finally:
            browser.close()

    return ValidationResult(
        success=True,
        error_log="",
        screenshot_path=str(screenshot_path),
        warnings="\n".join(warnings),
    )
