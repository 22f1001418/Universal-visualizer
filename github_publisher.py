"""GitHub publisher — pushes built viz dist/ to the scaler-content org.

Repo structure on GitHub:
  <track-repo>/           e.g. AIML, Academy-DSA, DevOps
    <module>/             e.g. neural-networks, sorting-algorithms
      <class>/            e.g. class-01, week-3
        index.html
        assets/
          index-abc123.js
          index-abc123.css

GitHub Pages URL: https://scaler-content.github.io/<repo>/<module>/<class>/
"""
from __future__ import annotations

import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("hackmd-orch.github")

GITHUB_ORG    = "scaler-content"
GITHUB_API    = "https://api.github.com"
GITHUB_PAGES_DOMAIN = f"https://{GITHUB_ORG}.github.io"

TRACK_TO_REPO: dict[str, str] = {
    "Academy DSA":      "Academy-DSA",
    "Academy Fullstack": "Academy-Fullstack",
    "Academy Backend":  "Academy-Backend",
    "DSML DA":          "DSML-DA",
    "DSML DS":          "DSML-DS",
    "AIML":             "AIML",
    "DevOps":           "DevOps",
}

PAGES_POLL_INTERVAL = 10   # seconds between live-checks
PAGES_POLL_TIMEOUT  = 300  # give up after 5 minutes


# ── Auth header ───────────────────────────────────────────────────────────────

def _h() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise RuntimeError("GITHUB_TOKEN env var not set — cannot publish to GitHub")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


# ── Track → repo name ─────────────────────────────────────────────────────────

def track_to_repo(track: str) -> str:
    repo = TRACK_TO_REPO.get(track)
    if not repo:
        raise ValueError(f"Unknown track '{track}'. Known: {list(TRACK_TO_REPO)}")
    return repo


# ── Path sanitization ─────────────────────────────────────────────────────────

def sanitize_path_component(s: str) -> str:
    """Convert user input to a safe GitHub path component (folder name)."""
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9._-]", "-", s)   # replace invalid chars with -
    s = re.sub(r"-+", "-", s)                  # collapse repeated hyphens
    s = s.strip("-")                            # no leading/trailing hyphens
    return s[:100]


# ── Repo lifecycle ────────────────────────────────────────────────────────────

def _repo_exists(repo: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}",
        headers=_h(), timeout=10,
    )
    return r.status_code == 200


def _create_repo(repo: str) -> None:
    r = requests.post(
        f"{GITHUB_API}/user/repos",
        headers=_h(),
        json={
            "name": repo,
            "private": False,
            "auto_init": True,          # initial README commit so Pages can be enabled
            "description": f"Scaler interactive visualizations — {repo}",
        },
        timeout=20,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create repo {repo}: {r.status_code} {r.text[:300]}")
    logger.info("[GitHub] Created repo %s/%s", GITHUB_ORG, repo)


def _pages_enabled(repo: str) -> bool:
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/pages",
        headers=_h(), timeout=10,
    )
    return r.status_code == 200


def _enable_pages(repo: str) -> None:
    r = requests.post(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/pages",
        headers=_h(),
        json={"source": {"branch": "main", "path": "/"}},
        timeout=15,
    )
    if r.status_code in (200, 201):
        logger.info("[GitHub] Pages enabled for %s/%s", GITHUB_ORG, repo)
    elif r.status_code == 409:
        logger.info("[GitHub] Pages already enabled for %s/%s", GITHUB_ORG, repo)
    else:
        logger.warning("[GitHub] Pages enable returned %d for %s: %s",
                       r.status_code, repo, r.text[:200])


def ensure_repo_exists(repo: str) -> None:
    """Create the repo + enable Pages if it doesn't exist yet. Idempotent."""
    if _repo_exists(repo):
        if not _pages_enabled(repo):
            _enable_pages(repo)
        return
    _create_repo(repo)
    time.sleep(3)   # let GitHub settle before enabling Pages
    _enable_pages(repo)


# ── Directory listing ─────────────────────────────────────────────────────────

def list_modules(repo: str) -> list[str]:
    """Top-level folders in the repo — each represents a module."""
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/contents/",
        headers=_h(), timeout=10,
    )
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        logger.warning("[GitHub] list_modules %s: %d", repo, r.status_code)
        return []
    return sorted(item["name"] for item in r.json() if item["type"] == "dir")


def list_classes(repo: str, module: str) -> list[str]:
    """Folders inside <module>/ — each represents a class."""
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/contents/{module}",
        headers=_h(), timeout=10,
    )
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        logger.warning("[GitHub] list_classes %s/%s: %d", repo, module, r.status_code)
        return []
    return sorted(item["name"] for item in r.json() if item["type"] == "dir")


# ── File push ─────────────────────────────────────────────────────────────────

def _get_sha(repo: str, path: str) -> Optional[str]:
    """Return the blob SHA of an existing file, or None if it doesn't exist."""
    r = requests.get(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/contents/{path}",
        headers=_h(), timeout=10,
    )
    if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):     # file — directories return a list
            return data.get("sha")
    return None


def _push_file(repo: str, path: str, content: bytes, message: str) -> None:
    """Create or update a single file in the repo via the Contents API."""
    sha = _get_sha(repo, path)
    payload: dict = {
        "message": message,
        "content": base64.b64encode(content).decode(),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha   # required for updates; omitted for new files

    r = requests.put(
        f"{GITHUB_API}/repos/{GITHUB_ORG}/{repo}/contents/{path}",
        headers=_h(),
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Failed to push {path}: {r.status_code} {r.text[:300]}")
    logger.info("[GitHub] pushed %s/%s", repo, path)


# ── Publish ───────────────────────────────────────────────────────────────────

def publish_dist(
    repo: str,
    module: str,
    class_name: str,
    dist_dir: str,
) -> str:
    """Push all files from dist_dir → <repo>/<module>/<class_name>/ on GitHub.

    Returns the GitHub Pages URL for the published viz.
    """
    dist = Path(dist_dir).resolve()
    if not dist.exists() or not (dist / "index.html").exists():
        raise RuntimeError(f"dist/ missing or no index.html: {dist}")

    ensure_repo_exists(repo)

    commit_msg = f"viz: add {module}/{class_name}"
    for file_path in sorted(dist.rglob("*")):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(dist)
        github_path = f"{module}/{class_name}/{rel.as_posix()}"
        _push_file(repo, github_path, file_path.read_bytes(), commit_msg)

    url = pages_url(repo, module, class_name)
    logger.info("[GitHub] published %d files → %s", sum(1 for f in dist.rglob("*") if f.is_file()), url)
    return url


def pages_url(repo: str, module: str, class_name: str) -> str:
    return f"{GITHUB_PAGES_DOMAIN}/{repo}/{module}/{class_name}/"


# ── Pages liveness check ──────────────────────────────────────────────────────

def check_pages_live(url: str) -> bool:
    """Single HTTP check — True if the page responds with 200."""
    try:
        r = requests.get(url, timeout=10, allow_redirects=True)
        return r.status_code == 200
    except Exception:
        return False


def wait_for_pages_live(
    url: str,
    timeout: int = PAGES_POLL_TIMEOUT,
    interval: int = PAGES_POLL_INTERVAL,
) -> bool:
    """Block until the Pages URL returns 200 or timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check_pages_live(url):
            logger.info("[GitHub] Pages live: %s", url)
            return True
        time.sleep(interval)
    logger.warning("[GitHub] Pages did not go live within %ds: %s", timeout, url)
    return False
