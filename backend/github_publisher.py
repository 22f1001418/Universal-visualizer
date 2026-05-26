"""GitHub publisher — push each viz as its own standalone repo.

One build → one new repo containing the full Vite source (plus optional dist/).
The caller deploys the repo separately (Vercel, Netlify, Pages, etc.); this
module does NOT enable Pages or wait for any deployment to go live.

Design notes for past-bugs:
  • GITHUB_TOKEN is read lazily inside `_h()`, never at import time. .env loaded
    after import still works.
  • Default owner is the authenticated user via `/user/repos`. Override with
    GITHUB_OWNER (user OR org); we auto-detect which endpoint to use.
  • `_repo_exists()` distinguishes 404 from auth failures and raises on
    401/403 so silent misconfiguration can't masquerade as "doesn't exist".
  • Upload is a single atomic Git Data API commit (blobs → tree → commit →
    ref), not N sequential Contents-API PUTs. Partial-state failures gone.
  • Every step calls the optional `on_log` so the UI sees progress.
  • No `wait_for_pages_live` blocking — Pages is no longer in scope.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

import requests

from backend.config import settings

logger = logging.getLogger("hackmd-orch.github")

GITHUB_API = "https://api.github.com"

# Files/dirs we never want in the published repo
SKIP_DIRS = {"node_modules", ".git", "dist-ssr", ".vite", "__pycache__", ".next", ".cache"}
SKIP_FILES = {".DS_Store", ".env", ".env.local", ".env.production", "npm-debug.log", "yarn-error.log"}
SKIP_SUFFIXES = (".log", ".pyc")

# Per-file size guard. GitHub blob API technically allows up to 100MB but
# anything over a few MB in a viz source tree is almost certainly an accident.
MAX_FILE_BYTES = 25 * 1024 * 1024  # 25 MB

LogFn = Optional[Callable[[str], None]]


# ── small helpers ─────────────────────────────────────────────────────────────

def _log(on_log: LogFn, msg: str) -> None:
    logger.info(msg)
    if on_log:
        try:
            on_log(msg)
        except Exception:  # noqa: BLE001 — never let a log callback break a publish
            pass


def _h() -> dict[str, str]:
    """Build auth headers. Reads GITHUB_TOKEN at call time, not import time."""
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


# ── repo naming ───────────────────────────────────────────────────────────────

def sanitize_repo_name(s: str) -> str:
    """GitHub repo names: alphanumeric, hyphens, underscores, dots."""
    s = s.strip().lower()
    s = re.sub(r"[^a-z0-9._-]", "-", s)
    s = re.sub(r"-+", "-", s).strip("-.")
    if not s:
        s = "viz"
    return s[:90]  # leave headroom for a `-N` collision suffix


# ── owner resolution ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Owner:
    name: str
    is_org: bool   # True → POST /orgs/{name}/repos, False → POST /user/repos


def _resolve_owner(on_log: LogFn) -> Owner:
    """Pick the GitHub owner for new repos.

    Precedence:
      1. GITHUB_OWNER env var (auto-detect user vs org via /users/{name})
      2. Authenticated user via /user
    """
    override = settings.github_owner
    if override:
        r = requests.get(f"{GITHUB_API}/users/{override}", headers=_h(), timeout=10)
        if r.status_code != 200:
            raise _err(r, f"lookup owner {override!r}")
        is_org = r.json().get("type", "User") == "Organization"
        _log(on_log, f"[GitHub] owner={override} ({'org' if is_org else 'user'}) from GITHUB_OWNER")
        return Owner(name=override, is_org=is_org)

    r = requests.get(f"{GITHUB_API}/user", headers=_h(), timeout=10)
    if r.status_code == 401:
        raise RuntimeError("GitHub auth failed (401) — check GITHUB_TOKEN scope (needs `repo`)")
    if r.status_code != 200:
        raise _err(r, "lookup authenticated user")
    login = r.json().get("login")
    if not login:
        raise RuntimeError("GitHub /user returned no login field")
    _log(on_log, f"[GitHub] owner={login} (authenticated user)")
    return Owner(name=login, is_org=False)


# ── repo existence + creation ─────────────────────────────────────────────────

def _repo_exists(owner: Owner, repo: str) -> bool:
    r = requests.get(f"{GITHUB_API}/repos/{owner.name}/{repo}", headers=_h(), timeout=10)
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    # 401/403/5xx — never silently treat as "doesn't exist"
    raise _err(r, f"check repo {owner.name}/{repo}")


def _create_repo(owner: Owner, repo: str, description: str, private: bool, on_log: LogFn) -> dict:
    if owner.is_org:
        url = f"{GITHUB_API}/orgs/{owner.name}/repos"
    else:
        url = f"{GITHUB_API}/user/repos"   # authenticated-user endpoint
    r = requests.post(
        url,
        headers=_h(),
        json={
            "name": repo,
            "private": private,
            # auto_init MUST be True: GitHub's Git Data API rejects blob creation
            # on an empty repo with HTTP 409 "Git Repository is empty". We let
            # GitHub create a placeholder README + initial commit, then force-
            # update refs/heads/main to point at our orphan commit below.
            "auto_init": True,
            "description": description[:350],
            "has_issues": True,
            "has_wiki": False,
        },
        timeout=20,
    )
    if r.status_code not in (200, 201):
        raise _err(r, f"create repo {owner.name}/{repo}")
    _log(on_log, f"[GitHub] created repo {owner.name}/{repo}")
    return r.json()


def _pick_unique_repo_name(owner: Owner, base: str, on_log: LogFn) -> str:
    """Append `-2`, `-3`, … if the name is taken. Bounded to avoid infinite loops."""
    name = base
    for i in range(2, 20):
        if not _repo_exists(owner, name):
            return name
        name = f"{base}-{i}"
        _log(on_log, f"[GitHub] {base} taken, trying {name}")
    raise RuntimeError(f"Could not find an available repo name starting from {base!r}")


# ── file collection ───────────────────────────────────────────────────────────

def _iter_publishable_files(root: Path, include_dist: bool) -> Iterable[Path]:
    """Yield files under `root` that should be uploaded, applying skip rules."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        # Directory-level skips (any segment)
        if any(part in SKIP_DIRS for part in rel_parts[:-1]):
            continue
        # `dist/` is opt-in — most consumers will rebuild from source
        if not include_dist and rel_parts and rel_parts[0] == "dist":
            continue
        if path.name in SKIP_FILES:
            continue
        if path.name.endswith(SKIP_SUFFIXES):
            continue
        yield path


# ── atomic Git Data API push ──────────────────────────────────────────────────

def _create_blob(owner: Owner, repo: str, data: bytes) -> str:
    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/blobs",
        headers=_h(),
        json={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
        timeout=60,
    )
    if r.status_code not in (200, 201):
        raise _err(r, "create blob")
    return r.json()["sha"]


def _push_initial_commit(
    owner: Owner,
    repo: str,
    root: Path,
    files: list[Path],
    commit_message: str,
    on_log: LogFn,
) -> str:
    """Blobs → tree → orphan commit → force-update refs/heads/main. Returns commit SHA.

    The repo was created with auto_init=True so refs/heads/main already exists
    (pointing at GitHub's auto-generated README commit). We force-update it to
    our orphan commit so the published history contains only our viz files.
    """
    tree_entries: list[dict] = []
    for i, path in enumerate(files, 1):
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            _log(on_log, f"[GitHub] skip {path.relative_to(root)} ({size} bytes > limit)")
            continue
        sha = _create_blob(owner, repo, path.read_bytes())
        tree_entries.append({
            "path": path.relative_to(root).as_posix(),
            "mode": "100644",
            "type": "blob",
            "sha": sha,
        })
        if i % 10 == 0 or i == len(files):
            _log(on_log, f"[GitHub] uploaded {i}/{len(files)} blobs")

    if not tree_entries:
        raise RuntimeError("Nothing to publish: no files passed the skip rules")

    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/trees",
        headers=_h(),
        json={"tree": tree_entries},
        timeout=60,
    )
    if r.status_code not in (200, 201):
        raise _err(r, "create tree")
    tree_sha = r.json()["sha"]

    r = requests.post(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/commits",
        headers=_h(),
        json={"message": commit_message, "tree": tree_sha, "parents": []},
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise _err(r, "create commit")
    commit_sha = r.json()["sha"]

    # Force-update existing main (created by auto_init) to our orphan commit.
    # Falls back to creating the ref if PATCH unexpectedly 404s (e.g. an org
    # configured to use `master` instead of `main` as the default branch).
    r = requests.patch(
        f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs/heads/main",
        headers=_h(),
        json={"sha": commit_sha, "force": True},
        timeout=30,
    )
    if r.status_code == 404:
        r = requests.post(
            f"{GITHUB_API}/repos/{owner.name}/{repo}/git/refs",
            headers=_h(),
            json={"ref": "refs/heads/main", "sha": commit_sha},
            timeout=30,
        )
        if r.status_code not in (200, 201):
            raise _err(r, "create refs/heads/main")
    elif r.status_code not in (200, 201):
        raise _err(r, "force-update refs/heads/main")

    _log(on_log, f"[GitHub] committed {len(tree_entries)} files @ {commit_sha[:7]}")
    return commit_sha


# ── public API ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PublishResult:
    repo_name: str
    owner: str
    html_url: str         # https://github.com/owner/repo
    clone_url: str        # https://github.com/owner/repo.git
    commit_sha: str
    file_count: int


def publish_viz_repo(
    project_dir: str,
    slug: str,
    description: str = "",
    include_dist: bool = True,
    private: bool = False,
    on_log: LogFn = None,
) -> PublishResult:
    """Push the viz project at `project_dir` to a brand-new GitHub repo.

    Args:
        project_dir: Local path to the generated Vite project root.
        slug: Desired repo name (will be sanitized + de-duped on collision).
        description: Repo description.
        include_dist: If True, also upload the built `dist/` folder.
        private: Create the repo as private.
        on_log: Optional callback for progress lines (streamed to BuildTask.logs).

    Raises:
        RuntimeError on any GitHub error (auth, permission, API failure).
    """
    root = Path(project_dir).resolve()
    if not root.is_dir():
        raise RuntimeError(f"project_dir does not exist or is not a directory: {root}")
    if not (root / "package.json").exists():
        raise RuntimeError(f"project_dir has no package.json — refusing to publish: {root}")

    owner = _resolve_owner(on_log)
    base_name = sanitize_repo_name(slug)
    repo_name = _pick_unique_repo_name(owner, base_name, on_log)

    _log(on_log, f"[GitHub] creating {owner.name}/{repo_name} (private={private})")
    _create_repo(owner, repo_name, description or f"Visualization: {slug}", private, on_log)

    files = list(_iter_publishable_files(root, include_dist=include_dist))
    _log(on_log, f"[GitHub] pushing {len(files)} files (include_dist={include_dist})")

    commit_sha = _push_initial_commit(
        owner=owner,
        repo=repo_name,
        root=root,
        files=files,
        commit_message=f"Initial commit: {slug}",
        on_log=on_log,
    )

    html_url = f"https://github.com/{owner.name}/{repo_name}"
    clone_url = f"{html_url}.git"
    _log(on_log, f"[GitHub] published → {html_url}")

    return PublishResult(
        repo_name=repo_name,
        owner=owner.name,
        html_url=html_url,
        clone_url=clone_url,
        commit_sha=commit_sha,
        file_count=len(files),
    )


# ── Monorepo publisher stub (Task 16 will provide the real implementation) ────

@dataclass(frozen=True)
class MonorepoPublishResult:
    """Result returned by publish_viz_to_monorepo.

    embed_url: GitHub Pages URL for the viz subdir.
    repo_edit_url: URL to browse/edit the subdir on GitHub.
    repo_name: The monorepo repo name (not a per-viz repo).
    owner: GitHub owner/org.
    html_url: https://github.com/owner/monorepo
    clone_url: https://github.com/owner/monorepo.git
    commit_sha: SHA of the commit that added/updated the subdir.
    file_count: Number of files written into the subdir.
    """

    repo_name: str
    owner: str
    html_url: str
    clone_url: str
    commit_sha: str
    file_count: int
    embed_url: str
    repo_edit_url: str


def publish_viz_to_monorepo(
    project_dir: str,
    slug: str,
    description: str = "",
    private: bool = False,
    on_log: LogFn = None,
) -> MonorepoPublishResult:
    """Push the viz at ``project_dir`` as a subdirectory of the shared monorepo.

    This is a stub that raises NotImplementedError. Task 16 replaces the entire
    github_publisher module with a real monorepo-based implementation.
    """
    raise NotImplementedError(
        "publish_viz_to_monorepo is not yet implemented — Task 16 provides the real code"
    )
