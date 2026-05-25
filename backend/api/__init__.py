"""HTTP API surface.

Each resource has its own router module. The single public function here
(`mount_routers`) wires every router onto the FastAPI app — main.py calls
it once at startup.

Layering contract (enforced by import-linter in Task 13):
  api/ → services/ → {llm, viz_generator, store, models, config}
"""
from __future__ import annotations

from fastapi import FastAPI


def mount_routers(app: FastAPI) -> None:
    """Mount every API router. Called from main.py during app construction."""
    from backend.api import health, jobs, suggestions, builds, manifest, preview, spa
    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(suggestions.router)
    app.include_router(builds.router)
    app.include_router(manifest.router)
    app.include_router(preview.router)
    app.include_router(spa.router)
    spa.mount_v2(app)
