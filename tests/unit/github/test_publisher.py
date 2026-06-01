"""Unit tests for the per-program repo publisher (Vercel-hosted).

We mock requests at the module level and verify: repo creation, vercel.json
seeding on first push, module/viz path, three-file commit, embed_url shape,
and stale-ref retry.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _fake_response(status_code: int, json_body: dict | None = None, text: str = ""):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body or {}
    r.text = text or ""
    return r


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    d = tmp_path / "viz"
    d.mkdir()
    (d / "index.html").write_text("<!doctype html><html><body>x</body></html>", encoding="utf-8")
    (d / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00fake")
    return d


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, "github_token", "ghp_fake", raising=False)
    monkeypatch.setattr(settings, "github_owner", "tester", raising=False)
    yield


def test_publishes_into_program_repo_with_vercel_json(project_dir: Path):
    from backend.github_publisher import publish_viz

    side_effects_get = [
        _fake_response(200, {"type": "User", "login": "tester"}),   # owner lookup
        _fake_response(404),                                        # repo exists?
        _fake_response(404),                                        # vercel.json at root?
        _fake_response(404),                                        # path exists? (collision)
        _fake_response(200, {"object": {"sha": "parent"}}),         # ref
        _fake_response(200, {"sha": "tree-base"}),                  # base tree
    ]
    side_effects_post = [
        _fake_response(201, {"name": "viz-aiml"}),   # create repo
        _fake_response(201, {"sha": "blob1"}),       # blob index.html
        _fake_response(201, {"sha": "blob2"}),       # blob screenshot.png
        _fake_response(201, {"sha": "blob3"}),       # blob vercel.json
        _fake_response(201, {"sha": "tree-new"}),    # new tree
        _fake_response(201, {"sha": "commit-1"}),    # commit
    ]
    side_effects_patch = [_fake_response(200)]       # update ref

    with patch("backend.github_publisher.requests.get", side_effect=side_effects_get), \
         patch("backend.github_publisher.requests.post", side_effect=side_effects_post) as mpost, \
         patch("backend.github_publisher.requests.patch", side_effect=side_effects_patch):
        result = publish_viz(
            project_dir=str(project_dir),
            repo="viz-aiml",
            vercel_base="https://viz-aiml.vercel.app",
            module_slug="conv-nets",
            viz_slug="binary-search",
            description="d",
        )

    assert result.embed_url == "https://viz-aiml.vercel.app/conv-nets/binary-search/"
    assert result.repo_edit_url == "https://github.com/tester/viz-aiml/tree/main/conv-nets/binary-search"
    assert result.repo_name == "viz-aiml"
    assert result.commit_sha == "commit-1"
    assert result.file_count == 3  # index.html + screenshot.png + vercel.json

    # The tree commit must contain all three paths.
    tree_call = [c for c in mpost.call_args_list if "/git/trees" in c.args[0]][0]
    paths = {item["path"] for item in tree_call.kwargs["json"]["tree"]}
    assert paths == {
        "conv-nets/binary-search/index.html",
        "conv-nets/binary-search/screenshot.png",
        "vercel.json",
    }


def test_skips_vercel_json_when_already_present(project_dir: Path):
    from backend.github_publisher import publish_viz

    side_effects_get = [
        _fake_response(200, {"type": "User", "login": "tester"}),   # owner lookup
        _fake_response(200, {"name": "viz-aiml"}),                  # repo exists
        _fake_response(200, {"name": "vercel.json"}),               # vercel.json present
        _fake_response(404),                                        # path exists?
        _fake_response(200, {"object": {"sha": "parent"}}),         # ref
        _fake_response(200, {"sha": "tree-base"}),                  # base tree
    ]
    side_effects_post = [
        _fake_response(201, {"sha": "blob1"}),    # blob index.html
        _fake_response(201, {"sha": "blob2"}),    # blob screenshot.png
        _fake_response(201, {"sha": "tree-new"}), # new tree
        _fake_response(201, {"sha": "commit-2"}), # commit
    ]
    side_effects_patch = [_fake_response(200)]

    with patch("backend.github_publisher.requests.get", side_effect=side_effects_get), \
         patch("backend.github_publisher.requests.post", side_effect=side_effects_post), \
         patch("backend.github_publisher.requests.patch", side_effect=side_effects_patch):
        result = publish_viz(
            project_dir=str(project_dir),
            repo="viz-aiml",
            vercel_base="https://viz-aiml.vercel.app/",  # trailing slash tolerated
            module_slug="conv-nets",
            viz_slug="kmeans",
            description="d",
        )

    assert result.embed_url == "https://viz-aiml.vercel.app/conv-nets/kmeans/"
    assert result.file_count == 2


def test_retries_on_stale_ref(project_dir: Path):
    """A 422 on the ref PATCH (concurrent push) triggers a full retry:
    blobs are re-created, a fresh parent SHA is fetched, and the second
    attempt's commit SHA is returned."""
    from backend.github_publisher import publish_viz

    side_effects_get = [
        _fake_response(200, {"type": "User", "login": "tester"}),   # owner lookup
        _fake_response(200, {"name": "viz-aiml"}),                  # repo exists
        _fake_response(200, {"name": "vercel.json"}),               # vercel.json present
        _fake_response(404),                                        # path exists? (collision)
        # attempt 1
        _fake_response(200, {"object": {"sha": "parent"}}),         # ref
        _fake_response(200, {"sha": "tree-base"}),                  # base tree
        # attempt 2 (after 422 retry)
        _fake_response(200, {"object": {"sha": "parent"}}),         # ref
        _fake_response(200, {"sha": "tree-base"}),                  # base tree
    ]
    side_effects_post = [
        # attempt 1
        _fake_response(201, {"sha": "b1"}),       # blob index.html
        _fake_response(201, {"sha": "b2"}),       # blob screenshot.png
        _fake_response(201, {"sha": "t"}),        # new tree
        _fake_response(201, {"sha": "commit-1"}), # commit
        # attempt 2
        _fake_response(201, {"sha": "b1"}),       # blob index.html
        _fake_response(201, {"sha": "b2"}),       # blob screenshot.png
        _fake_response(201, {"sha": "t"}),        # new tree
        _fake_response(201, {"sha": "commit-2"}), # commit
    ]
    side_effects_patch = [
        _fake_response(422, text="stale ref"),    # attempt 1: raced
        _fake_response(200),                      # attempt 2: success
    ]

    with patch("backend.github_publisher.requests.get", side_effect=side_effects_get), \
         patch("backend.github_publisher.requests.post", side_effect=side_effects_post), \
         patch("backend.github_publisher.requests.patch", side_effect=side_effects_patch):
        result = publish_viz(
            project_dir=str(project_dir),
            repo="viz-aiml",
            vercel_base="https://viz-aiml.vercel.app",
            module_slug="m",
            viz_slug="v",
            description="d",
        )

    assert result.commit_sha == "commit-2"  # second attempt's commit
    assert result.file_count == 2
