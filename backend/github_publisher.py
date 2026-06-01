"""GitHub publisher — push each viz into a per-program repo on `main`.

One repo per program (course/track). Each viz lives at
<module>/<viz>/{index.html,screenshot.png}. A static `vercel.json` is
committed at the repo root on first publish so the connected Vercel project
serves the files with clean, trailing-slash URLs:
    <vercel_base>/<module>/<viz>/

Design notes:
  • GITHUB_TOKEN is read lazily inside `_h()`, never at import time.
  • Concurrent pushes race on the ref update — handled by retrying with a
    refreshed parent SHA (up to MAX_REF_RETRIES).
  • Repo creation uses auto_init=True so refs/heads/main exists before we
    create blobs against it.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from backend.config import settings

logger = logging.getLogger("hackmd-orch.github")

GITHUB_API = "https://api.github.com"
MAX_REF_RETRIES: int = 3

# Committed once per repo so Vercel serves /<module>/<viz>/ → index.html.
VERCEL_JSON: bytes = json.dumps(
    {"cleanUrls": True, "trailingSlash": True}, indent=2
).encode("utf-8")

LogFn = Optional[Callable[[str], None]]


# ── small helpers ─────────────────────────────────────────────────────────────

def _log(on_log: LogFn, msg: str) -> None:
    logger.info(msg)
    if on_log:
        try:
            on_log(msg)
        except Exception:  # noqa: BLE001
            pass


def _h() -> dict[str, str]:
    token = settings.github_token
    if not token:
        raise RuntimeError("GITHUB_TOKEN env var not set — cannot publish to GitHub")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _err(resp: requests.Response, action: str) -> RuntimeError:
    body = resp.text[:400] if resp.text else "<empty>"
    return RuntimeError(f"GitHub {action} failed: HTTP {resp.status_code} {body}")


def sanitize_subdir_name(s: str) -> str:
    """URL-clean path segment: lower, alnum + dash only."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    if not s:
        s = "viz"
    return s[:90]


# ── owner resolution ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Owner:
    name: str
    is_org: bool


def _resolve_owner(on_log: LogFn) -> Owner:
    override = settings.github_owner
    if override:
        r = requests.get(f"{GITHUB_API}/users/{override}", headers=_h(), timeout=10)
        if r.status_code != 200:
            raise _err(r, f"lookup owner {override!r}")
        is_org = r.json().get("type", "User") == "Organization"
        _log(on_log, f"[GitHub] owner={override} ({'org' if is_org else 'user'})")
        return Owner(name=override, is_org=is_org)

    r = requests.get(f"{GITHUB_API}/user", headers=_h(), timeout=10)
    if r.status_code == 401:
        raise RuntimeError("GitHub auth failed (401) — check GITHUB_TOKEN scope")
    if r.status_code != 200:
        raise _err(r, "lookup authenticated user")
    login = r.json().get("login")
    if not login:
        raise RuntimeError("GitHub /user returned no login field")
    return Owner(name=login, is_org=False)


# ── repo + content checks ──────────────────────────────────────────────────────

def _ensure_repo_exists(
    owner: Owner, name: str, description: str, private: bool, on_log: LogFn,
) -> None:
    r = requests.get(f"{GITHUB_API}/repos/{owner.name}/{name}", headers=_h(), timeout=10)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise _err(r, f"check repo {owner.name}/{name}")

    create_url = (
        f"{GITHUB_API}/orgs/{owner.name}/repos" if owner.is_org
        else f"{GITHUB_API}/user/repos"
    )
    r = requests.post(
        create_url, headers=_h(), timeout=20,
        json={
            "name": name, "private": private,
            "auto_init": True,
            "description": description[:350] or f"Visualizations: {name}",
            "has_issues": True, "has_wiki": False,
        },
    )
    if r.status_code not in (200, 201):
        raise _err(r, f"create repo {owner.name}/{name}")
    _log(on_log, f"[GitHub] created repo {owner.name}/{name}")


def _path_exists(owner: Owner, name: str, path: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{owner.name}/{name}/contents/{path}",
        headers=_h(), timeout=10,
    )
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    raise _err(r, f"check path {path}")


def _pick_unique_subdir(owner: Owner, name: str, base: str, on_log: LogFn) -> str:
    if not _path_exists(owner, name, base):
        return base
    for i in range(2, 50):
        candidate = f"{base}-{i}"
        _log(on_log, f"[GitHub] {base} taken, trying {candidate}")
        if not _path_exists(owner, name, candidate):
            return candidate
    raise RuntimeError(f"Could not find an available path starting from {base!r}")


# ── atomic commit (with retry on stale ref) ──────────────────────────────────

def _create_blob(owner: Owner, repo: str, data: bytes) -> str:
    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/blobs",
        headers=_h(), timeout=60,
        json={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
    )
    if r.status_code not in (200, 201):
        raise _err(r, "create blob")
    return r.json()["sha"]


def _push_files_commit(
    owner: Owner, repo: str, files: dict[str, bytes],
    commit_message: str, on_log: LogFn,
) -> str:
    """blobs → ref → base tree → new tree → commit → ref update.

    `files` maps repo-relative path → bytes. Retries the whole sequence on
    stale-ref 422 up to MAX_REF_RETRIES times. Blobs are re-created each
    retry — slower but correct (the tree references blob SHAs that exist in
    the repo regardless of ref state).
    """
    last_exc: RuntimeError | None = None
    for attempt in range(1, MAX_REF_RETRIES + 1):
        try:
            tree_items = []
            for path, data in files.items():
                blob_sha = _create_blob(owner, repo, data)
                tree_items.append(
                    {"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}
                )

            r = requests.get(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs/heads/main",
                headers=_h(), timeout=15,
            )
            if r.status_code != 200:
                raise _err(r, "get refs/heads/main")
            parent_sha = r.json()["object"]["sha"]

            r = requests.get(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/trees/{parent_sha}",
                headers=_h(), timeout=15,
            )
            if r.status_code != 200:
                raise _err(r, "get base tree")
            base_tree_sha = r.json()["sha"]

            r = requests.post(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/trees",
                headers=_h(), timeout=30,
                json={"base_tree": base_tree_sha, "tree": tree_items},
            )
            if r.status_code not in (200, 201):
                raise _err(r, "create tree")
            tree_sha = r.json()["sha"]

            r = requests.post(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/commits",
                headers=_h(), timeout=30,
                json={"message": commit_message, "tree": tree_sha, "parents": [parent_sha]},
            )
            if r.status_code not in (200, 201):
                raise _err(r, "create commit")
            commit_sha = r.json()["sha"]

            r = requests.patch(
                f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs/heads/main",
                headers=_h(), timeout=30,
                json={"sha": commit_sha, "force": False},
            )
            if r.status_code == 422:
                _log(on_log, f"[GitHub] ref update raced (attempt {attempt}) — retrying")
                last_exc = _err(r, "fast-forward refs/heads/main")
                continue
            if r.status_code not in (200, 201):
                raise _err(r, "fast-forward refs/heads/main")

            _log(on_log, f"[GitHub] committed {len(files)} file(s) @ {commit_sha[:7]}")
            return commit_sha
        except RuntimeError as e:
            last_exc = e
            if "fast-forward refs/heads/main" not in str(e):
                raise

    assert last_exc is not None
    raise last_exc


# ── public API ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublishResult:
    repo_name: str
    owner: str
    html_url: str            # https://github.com/<owner>/<repo>
    clone_url: str           # https://github.com/<owner>/<repo>.git
    commit_sha: str
    file_count: int          # 2 (index + screenshot) or 3 (+ vercel.json on first push)
    embed_url: str           # <vercel_base>/<module>/<viz>/
    repo_edit_url: str       # https://github.com/<owner>/<repo>/tree/main/<module>/<viz>


def publish_viz(
    project_dir: str,
    repo: str,
    vercel_base: str,
    module_slug: str,
    viz_slug: str,
    description: str = "",
    private: bool = False,
    on_log: LogFn = None,
) -> PublishResult:
    """Push project_dir/{index.html,screenshot.png} into `repo` at
    <module_slug>/<viz_slug>/, seeding vercel.json on first publish."""
    root = Path(project_dir).resolve()
    html_path = root / "index.html"
    png_path = root / "screenshot.png"
    if not html_path.is_file():
        raise RuntimeError(f"index.html missing in {root}")
    if not png_path.is_file():
        raise RuntimeError(f"screenshot.png missing in {root}")

    owner = _resolve_owner(on_log)
    _ensure_repo_exists(owner, repo, description, private, on_log)

    has_vercel_json = _path_exists(owner, repo, "vercel.json")

    module_slug = sanitize_subdir_name(module_slug)
    viz_base = f"{module_slug}/{sanitize_subdir_name(viz_slug)}"
    viz_path = _pick_unique_subdir(owner, repo, viz_base, on_log)

    files: dict[str, bytes] = {
        f"{viz_path}/index.html": html_path.read_bytes(),
        f"{viz_path}/screenshot.png": png_path.read_bytes(),
    }
    if not has_vercel_json:
        files["vercel.json"] = VERCEL_JSON

    commit_sha = _push_files_commit(
        owner=owner, repo=repo, files=files,
        commit_message=f"Add viz: {viz_path}",
        on_log=on_log,
    )

    base = vercel_base.rstrip("/")
    html_url = f"https://github.com/{owner.name}/{repo}"
    return PublishResult(
        repo_name=repo,
        owner=owner.name,
        html_url=html_url,
        clone_url=f"{html_url}.git",
        commit_sha=commit_sha,
        file_count=len(files),
        embed_url=f"{base}/{viz_path}/",
        repo_edit_url=f"{html_url}/tree/main/{viz_path}",
    )


# ── backward-compat shim (removed by Task 5) ─────────────────────────────────
# build_orchestrator still imports this name; Task 5 will switch it to publish_viz.
publish_viz_to_monorepo = publish_viz
