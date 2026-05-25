"""GET /preview — serve screenshot/preview files from VIZ_OUTPUT_DIR."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.config import settings

router = APIRouter(tags=["preview"])


@router.get("/preview")
def preview_file(path: str) -> FileResponse:
    """Serve a screenshot or generated file by absolute path.

    Locked down to paths inside VIZ_OUTPUT_DIR — no traversal allowed.
    """
    target = Path(path).resolve()
    # Resolve VIZ_OUTPUT_DIR too — otherwise comparing an absolute resolved
    # target against a relative-string config silently rejects every real file
    # (and on macOS, symlinks like /tmp → /private/tmp diverge mid-traversal).
    viz_root = Path(settings.viz_output_dir).resolve()
    try:
        target.relative_to(viz_root)
    except ValueError:
        raise HTTPException(400, "Path outside VIZ_OUTPUT_DIR")
    if not target.exists():
        raise HTTPException(404, "File not found")
    media_type = "image/png" if target.suffix.lower() == ".png" else None
    return FileResponse(path=str(target), media_type=media_type, filename=target.name)
