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


def test_preview_serves_file_inside_viz_output_dir(client, tmp_path):
    """Happy path — must accept files inside VIZ_OUTPUT_DIR.

    Regression guard: a previous version of preview.py compared a resolved
    target path against a raw-string `viz_output_dir`, which silently rejected
    every legitimate file (Stage 3 final review finding).
    """
    # The `client` fixture sets VIZ_OUTPUT_DIR to `tmp_path/viz_outputs`.
    viz_dir = tmp_path / "viz_outputs"
    viz_dir.mkdir(parents=True, exist_ok=True)
    shot = viz_dir / "screenshot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")
    r = client.get(f"/preview?path={shot}")
    assert r.status_code == 200, r.text
    assert r.content.startswith(b"\x89PNG")
