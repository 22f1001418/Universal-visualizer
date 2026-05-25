"""GET / — serve the legacy CDN-React SPA. Mount /v2/ for the new Vite SPA.

Stage 4 introduces the new SPA at /v2/. After parity QA, Task 14 flips:
the new SPA moves to /, the legacy HTML moves to /legacy/.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter(tags=["spa"])

# Resolve project root from this file's location.
# __file__ = backend/api/spa.py → parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_FILE = _REPO_ROOT / "index.html"
_STATIC_V2_DIR = _REPO_ROOT / "backend" / "static_v2"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the legacy SPA HTML (flipped in Task 14)."""
    if not _FRONTEND_FILE.exists():
        return HTMLResponse(
            "<h1>Frontend not found</h1><p>Expected at: " + str(_FRONTEND_FILE) + "</p>",
            status_code=500,
        )
    return HTMLResponse(_FRONTEND_FILE.read_text(encoding="utf-8"))


def mount_v2(app: FastAPI) -> None:
    """Mount the built Stage 4 SPA at /v2/ if its dist exists.

    Called from backend.api.__init__.mount_routers. Skipped silently if the
    SPA hasn't been built yet — keeps the API usable in fresh checkouts
    where someone hasn't run `cd frontend && npm run build`.
    """
    if _STATIC_V2_DIR.exists() and (_STATIC_V2_DIR / "index.html").exists():
        app.mount(
            "/v2",
            StaticFiles(directory=str(_STATIC_V2_DIR), html=True),
            name="spa_v2",
        )
