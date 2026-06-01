"""program_repos map: parsing from env JSON + resolution by track."""
from __future__ import annotations

import json


def test_program_repos_parsed_from_env_json(monkeypatch):
    payload = {"AIML": {"repo": "viz-aiml", "vercel_base": "https://viz-aiml.vercel.app"}}
    monkeypatch.setenv("PROGRAM_REPOS", json.dumps(payload))
    from backend.config import Settings

    s = Settings()
    pr = s.program_repos.get("AIML")
    assert pr is not None
    assert pr.repo == "viz-aiml"
    assert pr.vercel_base == "https://viz-aiml.vercel.app"


def test_program_repos_missing_track_returns_none(monkeypatch):
    monkeypatch.setenv("PROGRAM_REPOS", "{}")
    from backend.config import Settings

    s = Settings()
    assert s.program_repos.get("Academy DSA") is None
