"""FastAPI orchestrator entry point.

This module is the thinnest possible app factory + uvicorn entry. All
route handlers live under backend/api/. All business logic lives under
backend/services/. All env config lives in backend/config.py. All LLM
machinery lives in backend/llm/. The viz generator lives in
backend/viz_generator/.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv

# Load .env BEFORE constructing Settings or importing modules that read env.
load_dotenv()

from fastapi import FastAPI                               # noqa: E402
from fastapi.middleware.cors import CORSMiddleware       # noqa: E402

from backend.api import mount_routers                    # noqa: E402
from backend.config import settings                      # noqa: E402
from backend.llm import is_reasoning_model               # noqa: E402
from backend.logging_setup import configure_root_logger  # noqa: E402
from orchestrator import FIXED_MAIN_PATH, VIZ_OUTPUT_DIR # noqa: E402
from backend.store import job_store                              # noqa: E402

configure_root_logger(level=logging.INFO)
logger = logging.getLogger("hackmd-orch")


def create_app() -> FastAPI:
    """Construct and return the FastAPI app.

    Kept as a function (rather than a module-level instance) so tests and
    future multi-app embedding can call it explicitly. The default app
    instance below — `app` — is what uvicorn imports.
    """
    application = FastAPI(
        title="HackMD Visualization Orchestrator",
        description="Upload a HackMD lecture script, get embed-ready visualizations.",
        version="0.2.0",
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.on_event("startup")
    def _on_startup() -> None:
        VIZ_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        logger.info(
            "──── [STATUS] SERVER STARTUP — text_model=%s ────",
            settings.openai_text_model,
        )
        logger.info("[Startup] OPENAI_API_KEY present: %s",
                    "yes" if settings.openai_api_key else "NO")
        logger.info("[Startup] FIXED_MAIN_PATH = %s   exists=%s",
                    FIXED_MAIN_PATH, FIXED_MAIN_PATH.exists())
        logger.info("[Startup] VIZ_OUTPUT_DIR = %s", VIZ_OUTPUT_DIR)
        logger.info("[Startup] TOKEN_BUDGET_PER_JOB = %d", settings.token_budget_per_job)
        if is_reasoning_model(settings.openai_text_model):
            logger.info("[Startup] Reasoning model — REASONING_EFFORT=%s",
                        settings.reasoning_effort)
        if not FIXED_MAIN_PATH.exists():
            logger.warning(
                "[Startup] WARNING: fixed_main_v6.py not found. The /build endpoint will fail. "
                "Set FIXED_MAIN_PATH in .env to its absolute path."
            )
        job_store.purge_stale()

    mount_routers(application)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )
