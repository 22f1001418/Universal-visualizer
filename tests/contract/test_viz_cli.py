"""Subprocess argv contract — locks the CLI interface of fixed_main_v6.py
as it exists at the start of Stage 2. Migration into backend.viz_generator.cli
must preserve every flag, dest, type, and default.
"""
from __future__ import annotations

import subprocess
import sys


def test_help_exits_zero_and_mentions_topic():
    """Help screen must mention --topic at minimum."""
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py", "--help"],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode == 0, r.stderr
    assert "--topic" in r.stdout


def test_topic_is_required():
    """Invoking with no args should fail."""
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py"],
        capture_output=True, text=True, timeout=20,
    )
    assert r.returncode != 0


def test_known_flags_present_in_help():
    """Every flag the orchestrator relies on must show in --help."""
    r = subprocess.run(
        [sys.executable, "fixed_main_v6.py", "--help"],
        capture_output=True, text=True, timeout=20,
    )
    help_text = r.stdout
    # Locked flags as of Stage 2 start:
    assert "--topic" in help_text
    assert "--polish" in help_text


def test_build_parser_accepts_topic_and_polish():
    """Programmatic argparse — doesn't require subprocess. Locks the flag
    structure as it exists at Stage 2 cutover.
    """
    from backend.viz_generator.cli import build_parser
    parser = build_parser()
    args = parser.parse_args(["--topic", "binary search"])
    assert args.topic == "binary search"
    assert args.polish is False

    args2 = parser.parse_args(["--topic", "x", "--polish"])
    assert args2.polish is True


def test_build_parser_requires_topic():
    """build_parser() must reject empty argv."""
    import pytest
    from backend.viz_generator.cli import build_parser
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([])
