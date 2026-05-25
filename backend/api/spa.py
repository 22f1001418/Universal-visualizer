"""GET / — serve the React frontend (index.html at project root).

When Stage 4 lands, this will return built static assets from backend/static/
instead. For now it serves the legacy CDN-React SPA verbatim.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["spa"])


# Resolve project root from this file's location.
# __file__ = backend/api/spa.py → parents[2] = repo root
_FRONTEND_FILE = Path(__file__).resolve().parents[2] / "index.html"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the SPA HTML."""
    if not _FRONTEND_FILE.exists():
        return HTMLResponse(
            "<h1>Frontend not found</h1><p>Expected at: " + str(_FRONTEND_FILE) + "</p>",
            status_code=500,
        )
    return HTMLResponse(_FRONTEND_FILE.read_text(encoding="utf-8"))
