"""GET /preview contract."""
from __future__ import annotations


def test_preview_missing_path_param(client):
    r = client.get("/preview")
    # Observed: FastAPI returns 422 (Unprocessable Entity) when required query
    # param `path` is missing
    assert r.status_code == 422


def test_preview_outside_output_dir_rejected(client):
    r = client.get("/preview?path=/etc/passwd")
    # Observed: main.py raises HTTPException(400, "Path outside VIZ_OUTPUT_DIR")
    assert r.status_code == 400
