"""Locks the vanilla-viz phase detection in backend.orchestrator."""
import pytest

from backend.orchestrator import detect_phase_from_line


@pytest.mark.parametrize("line,phase", [
    ("──── [STATUS] STEP 1 — CLASSIFY TOPIC ────", "draft"),
    ("──── [STATUS] STEP 2 — DRAFT + VALIDATE ────", "draft"),
    ("[draft] generating initial HTML for 'x'...", "draft"),
    ("[validate] first attempt...", "validate"),
    ("──── [STATUS] STEP 3 — POLISH ────", "polish"),
    ("[polish] refining design for 'x'...", "polish"),
    ("  DONE!", "done"),
])
def test_detect_phase_from_line_recognises_vanilla_markers(line, phase):
    assert detect_phase_from_line(line) == phase


def test_detect_phase_from_line_returns_none_for_unrelated_lines():
    assert detect_phase_from_line("random log noise") is None
