"""GitHub publisher — push each viz as a subdirectory in a single monorepo.

One monorepo (settings.viz_monorepo_name) holds every viz the user has ever
generated; each viz lives at <slug>/{index.html,screenshot.png}. The
monorepo has GitHub Pages enabled, so each viz is served at
https://<owner>.github.io/<monorepo>/<slug>/.

Design notes:
  • GITHUB_TOKEN is read lazily inside `_h()`, never at import time.
  • Concurrent pushes race on the ref update — handled by retrying with
    a refreshed parent SHA (up to MAX_REF_RETRIES).
  • Pages enable: 404 → enable; 422 "already exists" → treat as success
    (idempotent for the race where another worker enabled it first).
  • Monorepo creation uses auto_init=True so refs/heads/main exists
    before we attempt to create blobs against it.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests

from backend.config import settings

logger = logging.getLogger("hackmd-orch.github")

GITHUB_API = "https://api.github.com"

# Max ref-update retries on stale parent SHA. 3 is generous given that
# per-job builds are sequential and the only racers are concurrent users.
MAX_REF_RETRIES: int = 3

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
    """URL-clean subdir name: lower, alnum + dash only."""
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


# ── monorepo + pages ──────────────────────────────────────────────────────────

def _ensure_monorepo_exists(
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
        raise _err(r, f"create monorepo {owner.name}/{name}")
    _log(on_log, f"[GitHub] created monorepo {owner.name}/{name}")


def _ensure_pages_enabled(owner: Owner, name: str, on_log: LogFn) -> None:
    r = requests.get(f"{GITHUB_API}/repos/{owner.name}/{name}/pages", headers=_h(), timeout=10)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        raise _err(r, f"check pages {owner.name}/{name}")
    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{name}/pages",
        headers=_h(), timeout=20,
        json={"source": {"branch": "main", "path": "/"}},
    )
    if r.status_code == 422:
        # Race: another worker enabled Pages between our GET and POST.
        _log(on_log, f"[GitHub] pages already enabled on {owner.name}/{name}")
        return
    if r.status_code not in (200, 201):
        raise _err(r, f"enable pages {owner.name}/{name}")
    _log(on_log, f"[GitHub] enabled Pages on {owner.name}/{name}")


def _subdir_exists(owner: Owner, name: str, subdir: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{owner.name}/{name}/contents/{subdir}",
        headers=_h(), timeout=10,
    )
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    raise _err(r, f"check subdir {subdir}")


def _pick_unique_subdir(owner: Owner, name: str, base: str, on_log: LogFn) -> str:
    if not _subdir_exists(owner, name, base):
        return base
    for i in range(2, 50):
        candidate = f"{base}-{i}"
        _log(on_log, f"[GitHub] {base} taken, trying {candidate}")
        if not _subdir_exists(owner, name, candidate):
            return candidate
    raise RuntimeError(f"Could not find an available subdir starting from {base!r}")


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


def _push_subdir_commit(
    owner: Owner, repo: str, subdir: str,
    html_bytes: bytes, png_bytes: bytes,
    commit_message: str, on_log: LogFn,
) -> str:
    """blobs → ref → base tree → new tree → commit → ref update.

    Retries the whole sequence on stale-ref 422 up to MAX_REF_RETRIES times.
    Blobs are re-created each retry — that's slower but correct (the tree
    references blob SHAs that exist in the repo regardless of ref state).
    """
    last_exc: RuntimeError | None = None
    for attempt in range(1, MAX_REF_RETRIES + 1):
        try:
            blob_html = _create_blob(owner, repo, html_bytes)
            blob_png = _create_blob(owner, repo, png_bytes)

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
                json={
                    "base_tree": base_tree_sha,
                    "tree": [
                        {"path": f"{subdir}/index.html", "mode": "100644", "type": "blob", "sha": blob_html},
                        {"path": f"{subdir}/screenshot.png", "mode": "100644", "type": "blob", "sha": blob_png},
                    ],
                },
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

            _log(on_log, f"[GitHub] committed {subdir}/ @ {commit_sha[:7]}")
            return commit_sha
        except RuntimeError as e:
            last_exc = e
            # Only retry on the ref-update path; other failures bail immediately.
            if "fast-forward refs/heads/main" not in str(e):
                raise

    assert last_exc is not None
    raise last_exc


# ── public API ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublishResult:
    repo_name: str           # the monorepo name (used by build_orchestrator for back-compat)
    owner: str
    html_url: str            # https://github.com/<owner>/<monorepo>
    clone_url: str           # https://github.com/<owner>/<monorepo>.git
    commit_sha: str
    file_count: int          # always 2 for vanilla (index.html + screenshot.png)
    embed_url: str           # https://<owner>.github.io/<monorepo>/<slug>/
    repo_edit_url: str       # https://github.com/<owner>/<monorepo>/tree/main/<slug>


def publish_viz_to_monorepo(
    project_dir: str,
    slug: str,
    description: str = "",
    private: bool = False,
    on_log: LogFn = None,
) -> PublishResult:
    """Push project_dir/{index.html,screenshot.png} into the monorepo as <slug>/."""
    monorepo = settings.viz_monorepo_name
    if not monorepo:
        raise RuntimeError(
            "VIZ_MONOREPO_NAME not set — cannot publish without a monorepo name"
        )

    root = Path(project_dir).resolve()
    html_path = root / "index.html"
    png_path = root / "screenshot.png"
    if not html_path.is_file():
        raise RuntimeError(f"index.html missing in {root}")
    if not png_path.is_file():
        raise RuntimeError(f"screenshot.png missing in {root}")

    owner = _resolve_owner(on_log)
    _ensure_monorepo_exists(owner, monorepo, description, private, on_log)
    _ensure_pages_enabled(owner, monorepo, on_log)

    base_subdir = sanitize_subdir_name(slug)
    subdir = _pick_unique_subdir(owner, monorepo, base_subdir, on_log)

    commit_sha = _push_subdir_commit(
        owner=owner, repo=monorepo, subdir=subdir,
        html_bytes=html_path.read_bytes(),
        png_bytes=png_path.read_bytes(),
        commit_message=f"Add viz: {subdir}",
        on_log=on_log,
    )

    html_url = f"https://github.com/{owner.name}/{monorepo}"
    return PublishResult(
        repo_name=monorepo,
        owner=owner.name,
        html_url=html_url,
        clone_url=f"{html_url}.git",
        commit_sha=commit_sha,
        file_count=2,
        embed_url=f"https://{owner.name}.github.io/{monorepo}/{subdir}/",
        repo_edit_url=f"{html_url}/tree/main/{subdir}",
    )
