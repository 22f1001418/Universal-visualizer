"""Unit tests for the monorepo + GitHub Pages publisher.

We mock requests at the module level (matches the rest of the suite) and
verify the high-level flow: monorepo creation, Pages enablement, slug
collision suffixing, two-file commit, embed_url shape, retry on stale ref.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock, call

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
    """Force the publisher to read a predictable github token + owner."""
    from backend.config import settings
    monkeypatch.setattr(settings, "github_token", "ghp_fake", raising=False)
    monkeypatch.setattr(settings, "github_owner", "tester", raising=False)
    monkeypatch.setattr(settings, "viz_monorepo_name", "monorepo", raising=False)
    yield


def test_creates_monorepo_when_missing(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    side_effects_get = [
        _fake_response(200, {"type": "User", "login": "tester"}),         # owner lookup
        _fake_response(404),                                              # repo exists?
        _fake_response(404),                                              # pages enabled?
        _fake_response(404),                                              # subdir exists?
        _fake_response(200, {"object": {"sha": "parent"}}),              # ref
        _fake_response(200, {"sha": "tree-base"}),                       # base tree
    ]
    side_effects_post = [
        _fake_response(201, {"default_branch": "main", "name": "monorepo"}),   # create repo
        _fake_response(201),                                                    # enable pages
        _fake_response(201, {"sha": "blob1"}),                                  # blob index.html
        _fake_response(201, {"sha": "blob2"}),                                  # blob screenshot
        _fake_response(201, {"sha": "tree-new"}),                               # new tree
        _fake_response(201, {"sha": "commit-1"}),                               # commit
    ]
    side_effects_patch = [
        _fake_response(200),  # update ref
    ]

    with patch("backend.github_publisher.requests.get", side_effect=side_effects_get) as mget, \
         patch("backend.github_publisher.requests.post", side_effect=side_effects_post) as mpost, \
         patch("backend.github_publisher.requests.patch", side_effect=side_effects_patch) as mpatch:
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir),
            slug="binary-search",
            description="d",
        )

    assert result.embed_url == "https://tester.github.io/monorepo/binary-search/"
    assert result.repo_edit_url == "https://github.com/tester/monorepo/tree/main/binary-search"
    assert result.commit_sha == "commit-1"
    assert result.file_count == 2


def test_skips_repo_create_when_monorepo_exists(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {"default_branch": "main"}),                  # repo exists
        _fake_response(200, {"source": {"branch": "main"}}),              # pages enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls) as mget, \
         patch("backend.github_publisher.requests.post", side_effect=post_calls) as mpost, \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )

    posted_urls = [c.args[0] for c in mpost.call_args_list]
    # No POST to /user/repos or /orgs/.../repos
    assert not any(u.endswith("/user/repos") or "/repos" == u[-6:] for u in posted_urls)
    assert result.embed_url == "https://tester.github.io/monorepo/bs/"


def test_treats_422_pages_already_exists_as_success(project_dir: Path):
    """When Pages enable returns 422 (already on), continue without raising."""
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(404),                                              # pages NOT enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(422, text="Pages already exists"),                 # enable -> race
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.commit_sha == "commit-1"


def test_subdir_collision_suffixes_with_2(project_dir: Path):
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(200, {}),                                          # pages enabled
        _fake_response(200, {}),                                          # subdir "bs" exists
        _fake_response(404),                                              # "bs-2" vacant
        _fake_response(200, {"object": {"sha": "parent"}}),
        _fake_response(200, {"sha": "tree-base"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new"}),
        _fake_response(201, {"sha": "commit-1"}),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", return_value=_fake_response(200)):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.embed_url.endswith("/bs-2/")


def test_retries_on_stale_parent_sha(project_dir: Path):
    """Concurrent push race: ref update returns 422; we refetch ref + retry."""
    from backend.github_publisher import publish_viz_to_monorepo

    get_calls = [
        _fake_response(200, {"type": "User", "login": "tester"}),
        _fake_response(200, {}),                                          # repo exists
        _fake_response(200, {}),                                          # pages enabled
        _fake_response(404),                                              # subdir vacant
        _fake_response(200, {"object": {"sha": "parent-A"}}),            # ref (attempt 1)
        _fake_response(200, {"sha": "tree-base-A"}),
        _fake_response(200, {"object": {"sha": "parent-B"}}),            # ref refresh after 422
        _fake_response(200, {"sha": "tree-base-B"}),
    ]
    post_calls = [
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new-A"}),
        _fake_response(201, {"sha": "commit-A"}),
        _fake_response(201, {"sha": "blob1"}),
        _fake_response(201, {"sha": "blob2"}),
        _fake_response(201, {"sha": "tree-new-B"}),
        _fake_response(201, {"sha": "commit-B"}),
    ]
    patch_calls = [
        _fake_response(422, text="ref not at expected SHA"),
        _fake_response(200),
    ]

    with patch("backend.github_publisher.requests.get", side_effect=get_calls), \
         patch("backend.github_publisher.requests.post", side_effect=post_calls), \
         patch("backend.github_publisher.requests.patch", side_effect=patch_calls):
        result = publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
    assert result.commit_sha == "commit-B"


def test_raises_when_monorepo_name_not_configured(project_dir: Path, monkeypatch):
    from backend.config import settings
    from backend.github_publisher import publish_viz_to_monorepo
    monkeypatch.setattr(settings, "viz_monorepo_name", "", raising=False)
    with pytest.raises(RuntimeError, match="VIZ_MONOREPO_NAME"):
        publish_viz_to_monorepo(
            project_dir=str(project_dir), slug="bs", description="d",
        )
