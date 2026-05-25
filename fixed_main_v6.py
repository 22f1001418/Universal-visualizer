"""Compatibility stub — the viz generator now lives in
backend/viz_generator/. The orchestrator's FIXED_MAIN_PATH env var still
points here so the subprocess spawn contract is preserved.
"""
from backend.viz_generator.cli import main

if __name__ == "__main__":
    main()
