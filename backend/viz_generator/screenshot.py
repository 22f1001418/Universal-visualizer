"""Playwright-driven runtime validation + semantic checks for built vizes.

After a successful npm build + dev server, this module navigates the
running app and runs assertion DSL checks (semantic checks) against
the live DOM and JS state to confirm the viz actually works.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from backend.viz_generator.npm import (
    _pick_free_port,
    _wait_for_server,
    PREVIEW_STARTUP_WAIT,
)

log = logging.getLogger("viz_agent")

# ─────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────

PREVIEW_PAGE_TIMEOUT: int = 12_000     # ms — Playwright page.goto timeout
PREVIEW_IDLE_TIMEOUT: int = 10_000     # ms — Playwright networkidle timeout
INTERACTION_SETTLE: float = 1.2        # seconds — pause after click for re-render

# ─────────────────────────────────────────────────────────────
# UNIVERSAL TESTS (static test suite shared across all patterns)
# ─────────────────────────────────────────────────────────────

UNIVERSAL_TESTS: list[dict[str, str]] = [
    {
        "check": "document.body.innerText.trim().length > 20",
        "description": "Page renders visible content",
        "fix_hint": "The page is blank. Check createRoot mounts App correctly.",
    },
    {
        "check": """(() => {
            // D7 fix: also check aria-label and title for icon-only buttons
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /play|start|resume|begin|replay/.test(text);
            });
        })()""",
        "description": "Play/Start button present",
        "fix_hint": "Add a Play or Start button. If icon-only, add aria-label='Play' or title='Play' so tests can detect it.",
    },
    {
        "check": """(() => {
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /step|next|forward|advance/.test(text);
            });
        })()""",
        "description": "Step Forward button present",
        "fix_hint": "Add a Step or Next button. If icon-only, add aria-label='Step Forward' so tests can detect it.",
    },
    {
        "check": """(() => {
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /reset|restart|new|clear/.test(text);
            });
        })()""",
        "description": "Reset button present",
        "fix_hint": "Add a Reset button. If icon-only, add aria-label='Reset' so tests can detect it.",
    },
    {
        "check": """(() => {
            const text = document.body.innerText.toLowerCase();
            const isAtStart = /step[:\\s]*0|0\\s*\\/|epoch[:\\s]*0/i.test(text);
            if (!isAtStart) return true;
            const isDone = /complete|finished|converged|all done/.test(text);
            return !isDone;
        })()""",
        "description": "No 'complete/done' message on initial state",
        "fix_hint": (
            "Completion message appears at step 0. "
            "Only show completion when the user reaches the last step (stepIdx === steps.length - 1)."
        ),
    },
]

# D5 fix: validate Tailwind custom tokens resolve correctly
TAILWIND_TOKEN_TEST: dict[str, str] = {
    "check": (
        "(() => {"
        "  const el = document.querySelector('.glass-panel');"
        "  if (!el) return true;"
        "  const bg = window.getComputedStyle(el).background;"
        "  return bg !== '' && bg !== 'none' && bg !== 'rgba(0, 0, 0, 0)';"
        "})()"
    ),
    "description": "glass-panel CSS class resolves to a visible background",
    "fix_hint": (
        "glass-panel background is transparent or missing. "
        "Ensure src/index.css defines --panel-bg and .glass-panel uses it. "
        "Verify tailwind.config.js uses ESM export default with correct content paths."
    ),
}

UNIVERSAL_INTERACTION_TESTS: list[dict[str, str]] = [
    {
        "description": "Step Forward changes visible content",
        "fix_hint": (
            "Clicking Step Forward does not update the display. "
            "Ensure the render reads from steps[stepIdx] and stepIdx state is updated."
        ),
        "js_before": "document.body.innerText",
        "action": """(() => {
            // D7 fix: find by textContent OR aria-label/title for icon-only buttons
            const btn = [...document.querySelectorAll('button')].find(b => {
                const text = b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '');
                return /step|next|forward|advance/i.test(text);
            });
            if (btn) btn.click();
        })()""",
        "js_after": "document.body.innerText",
        "assert": "before !== after",
    },
]


def _playwright_available() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True, text=True,
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def run_semantic_checks(
    project_dir: Path,
    pattern_tests: list[dict[str, str]],
    topic_slug: str = "viz",
) -> list[dict[str, Any]]:
    """
    Runs universal + pattern-specific tests.
    Returns list of failure dicts: [{ description, fix_hint }]

    CRITICAL-2: port is randomised per call.
    CRITICAL-3: Popen stdout/stderr use DEVNULL to prevent pipe buffer deadlock.
    LOGICAL-10: screenshot named {topic_slug}_screenshot.png.
    """
    if not _playwright_available():
        log.info("  Playwright not found — skipping runtime checks.")
        log.info("  Install: pip install playwright && playwright install chromium")
        return [{
            "description": "Playwright not available",
            "fix_hint": "Install Playwright: pip install playwright && playwright install chromium",
            "infrastructure": True,
        }]

    # CRITICAL-2: randomise port
    try:
        port = _pick_free_port()
    except RuntimeError as e:
        return [{"description": str(e), "fix_hint": "Free a port in range 5100-5900.", "infrastructure": True}]

    log.info("  Starting vite preview on port %d...", port)

    # CRITICAL-3: DEVNULL prevents pipe buffer deadlock from accumulated vite output
    proc = subprocess.Popen(
        ["npm", "run", "preview", "--", "--port", str(port)],
        cwd=project_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_server(port, timeout=PREVIEW_STARTUP_WAIT):
        if proc.poll() is not None:
            log.info("  ❌ Vite preview exited with code %d during startup.", proc.returncode)
        else:
            proc.kill()
            proc.wait()
            log.info("  ❌ Vite preview did not respond within %ds.", PREVIEW_STARTUP_WAIT)
        return [{
            "description": "Preview server failed to start",
            "fix_hint": "Vite preview process did not respond in time. Check the build output.",
            "infrastructure": True,
        }]

    failures: list[dict[str, Any]] = []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1024, "height": 768})

            console_errors: list[str] = []
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)

            page.goto(f"http://localhost:{port}", timeout=PREVIEW_PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PREVIEW_IDLE_TIMEOUT)
            time.sleep(1)

            load_console_errors = list(console_errors)

            if load_console_errors:
                failures.append({
                    "description": "JS console errors on load",
                    "fix_hint": "Console errors:\n" +
                                "\n".join(f"  - {e}" for e in load_console_errors[:15]),
                })

            all_tests = UNIVERSAL_TESTS + [TAILWIND_TOKEN_TEST] + pattern_tests
            for test in all_tests:
                ok: bool
                try:
                    ok = page.evaluate(test["check"])
                except Exception as exc:
                    ok = False
                    # CRITICAL-4: use a separate dict; do NOT rebind `test`
                    augmented = dict(test)
                    augmented["fix_hint"] = augmented["fix_hint"] + f"\n(Eval error: {exc})"
                    log.debug("  Test eval error for '%s': %s", test["description"], exc)
                    failures.append({
                        "description": augmented["description"],
                        "fix_hint": augmented["fix_hint"],
                    })
                    continue
                if not ok:
                    failures.append({
                        "description": test["description"],
                        "fix_hint": test["fix_hint"],
                    })

            for itest in UNIVERSAL_INTERACTION_TESTS:
                try:
                    before = page.evaluate(itest["js_before"])
                    page.evaluate(itest["action"])
                    # MINOR-5: raised to 1.2s for slow framer-motion animations
                    time.sleep(INTERACTION_SETTLE)
                    after = page.evaluate(itest["js_after"])
                    ok = _evaluate_assertion(itest["assert"], before, after)
                except Exception as exc:
                    ok = False
                    log.debug("  Interaction test error for '%s': %s", itest["description"], exc)
                if not ok:
                    failures.append({
                        "description": itest["description"],
                        "fix_hint": itest["fix_hint"],
                    })

            # LOGICAL-10: topic-specific screenshot name prevents overwriting
            shot_path = project_dir / f"{topic_slug}_screenshot.png"
            page.screenshot(path=str(shot_path))
            log.info("  📸 Screenshot: %s", shot_path)

            browser.close()

    except Exception as e:
        failures.append({
            "description": "Playwright error",
            "fix_hint": str(e),
            "infrastructure": True,
        })
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    return failures


def _evaluate_assertion(assertion: str, before: Any, after: Any) -> bool:
    """Safe assertion lookup — no eval()."""
    _SAFE_ASSERTIONS: dict[str, Any] = {
        "before !== after": lambda b, a: b != a,
        "before != after":  lambda b, a: b != a,
        "before === after": lambda b, a: b == a,
        "before == after":  lambda b, a: b == a,
    }
    handler = _SAFE_ASSERTIONS.get(assertion.strip())
    if handler is None:
        log.warning(
            "  Unknown assertion '%s' — treating as failure. "
            "Add it to _SAFE_ASSERTIONS if it's intentional.",
            assertion,
        )
        return False
    return handler(before, after)
