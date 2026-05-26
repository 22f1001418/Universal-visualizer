"""Locks the vanilla viz prompt's hard constraints. The Stage-2 React/Tailwind
prompt should never be reintroduced; the regression guard below catches that.
"""
from backend.viz_generator.prompts import UNIVERSAL_SYSTEM_PROMPT, POLISH_RUBRIC


def test_universal_prompt_is_nonempty_and_under_target_length():
    p = UNIVERSAL_SYSTEM_PROMPT
    assert p.strip()
    # Target: ~80 lines, well under the previous 313-line React prompt.
    assert p.count("\n") < 150, f"prompt grew to {p.count(chr(10))} lines"


def test_universal_prompt_contains_hard_constraints():
    p = UNIVERSAL_SYSTEM_PROMPT.lower()
    # Must state the single-file rule and the file name.
    assert "single file" in p or "one file" in p
    assert "index.html" in p
    # Must ban external URLs (no CDN scripts/stylesheets).
    assert "no external" in p or "no cdn" in p or "fully offline" in p


def test_universal_prompt_has_no_react_or_tailwind_keywords():
    # Regression guard: these would mean the React prompt got restored.
    p = UNIVERSAL_SYSTEM_PROMPT.lower()
    for banned in ("react", "tailwind", "vite", "framer-motion",
                   "zustand", "tsx", "createroot"):
        assert banned not in p, f"banned keyword present: {banned!r}"


def test_polish_rubric_is_nonempty():
    assert POLISH_RUBRIC.strip()
    # Must talk about *visual* refinement, not algorithm logic.
    low = POLISH_RUBRIC.lower()
    assert "typography" in low or "spacing" in low or "motion" in low
