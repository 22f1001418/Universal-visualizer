"""Serve the production Stage 4 SPA at /; keep legacy at /legacy/ for one release."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["spa"])

# Resolve project root from this file's location.
# __file__ = backend/api/spa.py → parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = _REPO_ROOT / "backend" / "static"
_LEGACY_DIR = _REPO_ROOT / "backend" / "legacy"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the built SPA's index.html."""
    target = _STATIC_DIR / "index.html"
    if not target.exists():
        raise HTTPException(
            500,
            f"SPA not built. Run: cd frontend && npm run build  (expected at {target})",
        )
    return HTMLResponse(target.read_text(encoding="utf-8"))


def mount_static(app: FastAPI) -> None:
    """Mount /assets and /legacy. Called from backend.api.mount_routers."""
    from fastapi.staticfiles import StaticFiles
    if _STATIC_DIR.exists():
        # /assets is where Vite puts hashed JS/CSS chunks.
        assets_dir = _STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    if _LEGACY_DIR.exists() and (_LEGACY_DIR / "index.html").exists():
        app.mount(
            "/legacy",
            StaticFiles(directory=str(_LEGACY_DIR), html=True),
            name="legacy",
        )
