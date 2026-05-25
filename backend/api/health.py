"""GET /healthz — sanity check endpoint."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from backend.config import settings
from backend.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthResponse)
def healthz() -> HealthResponse:
    """Sanity check + report fixed_main_v6.py reachability + active model."""
    fm = Path(settings.fixed_main_path)
    return HealthResponse(
        ok=True,
        fixed_main_path=str(fm),
        fixed_main_exists=fm.exists(),
        text_model=settings.openai_text_model,
        output_dir=settings.viz_output_dir,
    )
