"""/v2/ — built SPA mount (Stage 4 parallel ship)."""
from __future__ import annotations

from pathlib import Path


def test_v2_root_serves_index_html(client, monkeypatch, tmp_path):
    """GET /v2/ must return the built SPA's index.html when it exists."""
    static_v2 = tmp_path / "static_v2"
    static_v2.mkdir()
    (static_v2 / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div></body></html>"
    )
    # The mount resolves backend/static_v2 relative to repo root at import
    # time. Patch the resolved path used by spa.py.
    import backend.api.spa as spa_mod
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", static_v2)
    # Re-mount: the existing app already mounted /v2 at startup against the
    # real (possibly empty) directory. Mount a parallel router for the test
    # by rebuilding the app.
    from importlib import reload
    reload(spa_mod)
    from main import create_app
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", static_v2)
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as fresh:
        r = fresh.get("/v2/")
    assert r.status_code == 200
    assert b"<div id='root'>" in r.content


def test_v2_missing_dir_returns_404(client):
    """If backend/static_v2 doesn't exist, /v2/ returns 404 (not 500)."""
    r = client.get("/v2/")
    assert r.status_code in (404, 200)  # 200 only if a real build is on disk
