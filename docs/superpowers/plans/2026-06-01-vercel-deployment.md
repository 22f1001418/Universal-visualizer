# Vercel Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish each generated viz into a per-program GitHub repo at `<module>/<viz>/`, hosted on Vercel, and host the tool itself on Render.

**Architecture:** The tool stays a Dockerized FastAPI service (now on Render). On each successful build it pushes the self-contained `index.html` + `screenshot.png` into the program's repo (resolved from the job's `track`) under a `<module-slug>/<viz-slug>/` folder on `main`. Each program repo is imported once into Vercel, which auto-deploys on push and serves `<vercel_base>/<module>/<viz>/`. GitHub Pages is dropped.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, pytest, React/TypeScript (Vite), Docker, Render, Vercel.

---

## File Structure

- `backend/config.py` — **modify**: replace single `viz_monorepo_name` with a `program_repos` map (`track → {repo, vercel_base}`); add `ProgramRepo` model.
- `backend/github_publisher.py` — **rewrite**: per-program repo + `<module>/<viz>/` path + `vercel.json` on first push; drop Pages. New public fn `publish_viz`.
- `backend/models.py` — **modify**: add `module` to `JobState`; refresh stale comments on `EmbedManifestEntry`.
- `backend/api/jobs.py` — **modify**: add required `module` form field to `POST /upload`; sanitize + validate; store on job.
- `backend/services/build_orchestrator.py` — **modify**: resolve program from `job.track`, pass `module`/`viz` slugs to `publish_viz`, update the skip-gate.
- `frontend/src/pages/Upload.tsx` — **modify**: add a module-slug input; send it in the upload FormData.
- `render.yaml` — **create**: Render Docker Web Service blueprint.
- `railway.toml` — **delete**.
- `tests/unit/github/test_publisher.py` — **rewrite** for the new flow.
- `tests/contract/test_upload.py` — **modify**: include `module`; add required-field test.
- `tests/unit/config/test_program_repos.py` — **create**: resolver tests.
- `README.md`, `env.example` — **modify**: publish/embed/deploy docs + new env var.

---

## Task 1: Config — `ProgramRepo` map + resolver

**Files:**
- Modify: `backend/config.py`
- Test: `tests/unit/config/test_program_repos.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/config/test_program_repos.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/config/test_program_repos.py -v`
Expected: FAIL (`Settings` has no attribute `program_repos`, or import error).

- [ ] **Step 3: Implement the config changes**

In `backend/config.py`, add a `ProgramRepo` model near the top (after imports, before the `Settings` class). pydantic-settings parses a nested-model dict field from a JSON string env var automatically.

```python
from pydantic import BaseModel


class ProgramRepo(BaseModel):
    """Where one program's vizzes are stored + served."""
    repo: str          # GitHub repo name, e.g. "viz-aiml"
    vercel_base: str   # Vercel production URL, e.g. "https://viz-aiml.vercel.app"
```

Replace the `viz_monorepo_name` block:

```python
    # ── Vanilla viz monorepo (vanilla-viz-stage-1) ───────────
    # Name of the GitHub repo that holds all published vanilla vizes as
    # subdirectories. REQUIRED at publish time; empty default makes test
    # construction work without env config.
    viz_monorepo_name: str = ""
```

with:

```python
    # ── Per-program viz repos (Vercel deploy) ────────────────
    # Maps a program (= upload `track`) to the GitHub repo that stores its
    # vizzes and the Vercel base URL that serves them. Populated from the
    # PROGRAM_REPOS env var as a JSON object, e.g.:
    #   PROGRAM_REPOS='{"AIML": {"repo": "viz-aiml", "vercel_base": "https://viz-aiml.vercel.app"}}'
    # Empty default keeps test construction working without env config.
    program_repos: dict[str, ProgramRepo] = {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/config/test_program_repos.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/config.py tests/unit/config/test_program_repos.py
git commit -m "feat(config): per-program repo map (track -> repo + vercel_base)"
```

---

## Task 2: Rewrite `github_publisher` for per-program repos

**Files:**
- Modify: `backend/github_publisher.py` (full rewrite)
- Test: `tests/unit/github/test_publisher.py` (full rewrite)

- [ ] **Step 1: Write the failing test**

Replace the entire contents of `tests/unit/github/test_publisher.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/github/test_publisher.py -v`
Expected: FAIL (`publish_viz` does not exist).

- [ ] **Step 3: Rewrite the publisher**

Replace the entire contents of `backend/github_publisher.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/github/test_publisher.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/github_publisher.py tests/unit/github/test_publisher.py
git commit -m "feat(publish): per-program repo + module/viz path + vercel.json (drop Pages)"
```

---

## Task 3: Add `module` to `JobState`

**Files:**
- Modify: `backend/models.py`

- [ ] **Step 1: Add the field**

In `backend/models.py`, in the `JobState` model, immediately after the `track` field, add:

```python
    module: str = ""    # module slug within a program; routes the publish path
```

- [ ] **Step 2: Refresh stale comments on `EmbedManifestEntry`**

In `EmbedManifestEntry`, replace the two trailing comments so they no longer reference GitHub Pages / monorepo:

```python
    github_repo_url: str = ""       # program repo URL hosting this viz (if published)
    status: Literal["ok", "failed", "skipped"] = "ok"
    embed_url: str = ""                # <vercel_base>/<module>/<viz>/
    repo_edit_url: str = ""            # https://github.com/<owner>/<repo>/tree/main/<module>/<viz>
```

- [ ] **Step 3: Run the existing suite to confirm nothing breaks**

Run: `pytest tests/unit -q`
Expected: PASS (no behavior change yet; new field defaults to `""`).

- [ ] **Step 4: Commit**

```bash
git add backend/models.py
git commit -m "feat(models): add JobState.module + refresh embed manifest comments"
```

---

## Task 4: `POST /upload` accepts + validates `module`

**Files:**
- Modify: `backend/api/jobs.py`
- Test: `tests/contract/test_upload.py`

- [ ] **Step 1: Write/Update the failing tests**

In `tests/contract/test_upload.py`, update the two existing calls to include `module` in `data`, and add a new test. The existing two `client.post` lines become:

```python
    r = client.post("/upload", files=files, data={"track": "Academy DSA", "module": "intro"})
```

(apply to both `test_upload_minimal_md_returns_upload_response` and `test_upload_rejects_non_md_file`).

Add this new test to the file:

```python
def test_upload_requires_module(client, fake_llm):
    files = {"file": ("test.md", b"# Test\n\nA short script.\n", "text/markdown")}
    # Missing module → FastAPI form validation rejects with 422
    r = client.post("/upload", files=files, data={"track": "Academy DSA"})
    assert r.status_code == 422, r.text


def test_upload_rejects_blank_module(client, fake_llm):
    files = {"file": ("test.md", b"# Test\n\nA short script.\n", "text/markdown")}
    # A module that sanitizes to empty (only punctuation) → 400
    r = client.post("/upload", files=files, data={"track": "Academy DSA", "module": "  ///  "})
    assert r.status_code == 400, r.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/contract/test_upload.py -v`
Expected: FAIL — `test_upload_requires_module` returns 200 (module not required yet); `test_upload_rejects_blank_module` returns 200.

- [ ] **Step 3: Implement the endpoint change**

In `backend/api/jobs.py`, add the import for the sanitizer near the other backend imports in the upload section:

```python
from backend.github_publisher import sanitize_subdir_name
```

Change the `upload_script` signature to require `module`:

```python
@router.post("/upload", response_model=UploadResponse)
async def upload_script(
    track: str = Form(...),
    module: str = Form(...),
    file: UploadFile = File(...),
) -> UploadResponse:
```

Immediately after the existing `if track not in ALLOWED_TRACKS:` block, add module validation. Add `import re` to the upload section imports, then:

```python
    if not re.search(r"[a-z0-9]", module.lower()):
        # No usable alphanumeric character → sanitize would fall back to "viz".
        raise HTTPException(400, "module must contain letters or digits")
    module_slug = sanitize_subdir_name(module)
```

Then set it on the `JobState(...)` constructor (add the `module=module_slug` kwarg):

```python
    job = JobState(
        job_id=job_id,
        script_name=file.filename,
        track=track,
        module=module_slug,
        status=JobStatus.UPLOADED,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/contract/test_upload.py -v`
Expected: PASS (all upload tests, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/api/jobs.py tests/contract/test_upload.py
git commit -m "feat(upload): require + sanitize module slug, store on job"
```

---

## Task 5: Wire program/module/viz into the publish step

**Files:**
- Modify: `backend/services/build_orchestrator.py`

- [ ] **Step 1: Update the import**

In `backend/services/build_orchestrator.py`, change:

```python
from backend.github_publisher import publish_viz_to_monorepo
```

to:

```python
from backend.github_publisher import publish_viz, sanitize_subdir_name
```

- [ ] **Step 2: Replace the publish gate + call**

Replace the whole publish block (the `if result.success and result.project_dir and settings.publish_to_github:` section, from the `if not settings.github_token:` gate through the `on_log(f"[GitHub] FAILED — {exc}")` line) with:

```python
    if result.success and result.project_dir and settings.publish_to_github:
        prog = settings.program_repos.get(job.track)
        if not settings.github_token:
            task.github_status = "skipped"
            task.github_error = "GITHUB_TOKEN not set"
            on_log("[GitHub] skipped — GITHUB_TOKEN not set")
        elif prog is None:
            task.github_status = "skipped"
            task.github_error = f"No program repo configured for track {job.track!r}"
            on_log(f"[GitHub] skipped — no program repo for track {job.track!r}")
        else:
            _status("PUBLISH", f"job_id={job_id}  topic_id={topic_id}  project={result.project_dir}")
            task.phase = "publish"  # type: ignore[assignment]
            task.github_status = "publishing"
            try:
                viz_slug = task.short_topic or Path(result.project_dir).name
                pub = publish_viz(
                    project_dir=result.project_dir,
                    repo=prog.repo,
                    vercel_base=prog.vercel_base,
                    module_slug=job.module or "module",
                    viz_slug=viz_slug,
                    description=(task.final_viz_brief or viz_slug)[:300],
                    private=settings.github_repos_private,
                    on_log=on_log,
                )
                task.github_status = "published"
                task.github_repo_url = pub.html_url
                task.github_clone_url = pub.clone_url
                task.github_repo_name = pub.repo_name
                task.github_commit_sha = pub.commit_sha
                task.embed_url = pub.embed_url
                task.repo_edit_url = pub.repo_edit_url
                task.monorepo_name = pub.repo_name
                task.phase = "done"  # type: ignore[assignment]
                logger.info("[Build %s] Published to %s", topic_id, pub.embed_url)
            except Exception as exc:                # noqa: BLE001 — publish must never crash the build
                logger.exception("[Build %s] GitHub publish failed: %s", topic_id, exc)
                task.github_status = "failed"
                task.github_error = str(exc)[:500]
                task.phase = "done"  # type: ignore[assignment]
                on_log(f"[GitHub] FAILED — {exc}")
```

- [ ] **Step 3: Run the full suite**

Run: `pytest -q`
Expected: PASS. (If any test referenced `publish_viz_to_monorepo` or `viz_monorepo_name`, update it to the new names — search with `grep -rn "publish_viz_to_monorepo\|viz_monorepo_name" tests backend` and fix each occurrence.)

- [ ] **Step 4: Commit**

```bash
git add backend/services/build_orchestrator.py
git commit -m "feat(build): publish to per-program repo at module/viz path"
```

---

## Task 6: Frontend — module input on the upload screen

**Files:**
- Modify: `frontend/src/pages/Upload.tsx`

- [ ] **Step 1: Add module state**

In `frontend/src/pages/Upload.tsx`, after the `track` state line, add:

```tsx
  const [module, setModule] = useState('');
```

- [ ] **Step 2: Validate + send module**

In `submit()`, after the file-extension check and before `setBusy(true)`, add:

```tsx
    if (!module.trim()) {
      onError('Enter a module name.');
      return;
    }
```

And add the field to the FormData (after `fd.append('track', track);`):

```tsx
    fd.append('module', module.trim());
```

- [ ] **Step 3: Add the input to the form**

Inside the `grid-2` block, after the Track `<div>` and before the HackMD file `<div>`, add a new field:

```tsx
          <div>
            <label htmlFor="upload-module">Module</label>
            <input
              id="upload-module"
              type="text"
              placeholder="e.g. convolutional-neural-nets"
              value={module}
              onChange={(e) => setModule(e.target.value)}
            />
          </div>
```

- [ ] **Step 4: Build the frontend to verify it compiles**

Run: `cd frontend && npm run build`
Expected: build succeeds (no TypeScript errors).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Upload.tsx
git commit -m "feat(ui): add module field to upload form"
```

---

## Task 7: Render blueprint + remove Railway config

**Files:**
- Create: `render.yaml`
- Delete: `railway.toml`

- [ ] **Step 1: Create `render.yaml`**

```yaml
services:
  - type: web
    name: hackmd-viz-orchestrator
    runtime: docker
    dockerfilePath: ./Dockerfile
    plan: starter            # has a persistent disk; free has none
    healthCheckPath: /healthz
    disk:
      name: viz-outputs
      mountPath: /app/viz_outputs
      sizeGB: 1
    envVars:
      - key: OPENAI_API_KEY
        sync: false
      - key: GITHUB_TOKEN
        sync: false
      - key: GITHUB_OWNER
        sync: false
      - key: PROGRAM_REPOS
        sync: false
```

Note: the `Dockerfile` already binds `--port ${PORT:-8001}`; Render injects `$PORT`, so no command override is needed.

- [ ] **Step 2: Delete `railway.toml`**

Run: `git rm railway.toml`

- [ ] **Step 3: Commit**

```bash
git add render.yaml
git commit -m "chore(deploy): add Render blueprint, remove Railway config"
```

---

## Task 8: Docs — README publish/embed section + env example

**Files:**
- Modify: `README.md`
- Modify: `env.example` **and** `.env.example` (both exist and should stay in sync)

- [ ] **Step 1: Update `env.example` and `.env.example`**

In **both** files, under the GitHub publish section (after `GITHUB_REPOS_PRIVATE`), add:

```bash
# Per-program viz repos (JSON: track -> {repo, vercel_base}). One entry per
# active program. Each repo is imported once into Vercel; pushes auto-deploy.
PROGRAM_REPOS={"AIML": {"repo": "viz-aiml", "vercel_base": "https://viz-aiml.vercel.app"}}
```

Neither file currently has a `VIZ_MONOREPO_NAME` line, so there is nothing to remove — just add the block above to both.

- [ ] **Step 2: Update the README embed-manifest example + publishing prose**

In `README.md`, in the `## Embed manifest format` JSON example, replace the `project_dir` / `screenshot_path` fields in the manifest entry with the published-URL fields so it matches `EmbedManifestEntry`:

```json
      "viz_brief": "Convolutional Layer Operation (Matrix slide animation) — Show a 5x5 input grid…",
      "embed_url": "https://viz-aiml.vercel.app/convolutional-neural-nets/convolutional-layer-operation/",
      "repo_edit_url": "https://github.com/<owner>/viz-aiml/tree/main/convolutional-neural-nets/convolutional-layer-operation",
      "screenshot_path": "/app/viz_outputs/convolutional-layer-operation-viz/screenshot.png",
      "status": "ok"
```

Add a short `## Deployment` section near the end:

```markdown
## Deployment

- **Tool** → Render (Docker Web Service via `Dockerfile`, disk at `/app/viz_outputs`).
  See `render.yaml`. Set `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_OWNER`,
  `PROGRAM_REPOS` in the Render dashboard.
- **Animations** → one GitHub repo per program (`PROGRAM_REPOS`), each imported
  once into Vercel (framework "Other", no build command). On each successful
  build the tool pushes `<module>/<viz>/{index.html,screenshot.png}` to `main`;
  Vercel auto-deploys and serves `<vercel_base>/<module>/<viz>/`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md env.example
git commit -m "docs: document per-program Vercel publish + Render deploy + PROGRAM_REPOS"
```

---

## Final verification

- [ ] Run the full backend suite: `pytest -q` → all pass.
- [ ] Build the frontend: `cd frontend && npm run build` → succeeds.
- [ ] Grep for stale references: `grep -rn "publish_viz_to_monorepo\|viz_monorepo_name\|VIZ_MONOREPO_NAME\|github.io" backend tests README.md env.example` → no results (except intentional history/docs).
- [ ] Manual smoke (optional, needs real tokens): set `GITHUB_TOKEN`, `GITHUB_OWNER`, `PROGRAM_REPOS`; upload one `.md` under a configured track + a module slug; confirm files land at `<module>/<viz>/` on `main` and the Vercel URL serves them.
