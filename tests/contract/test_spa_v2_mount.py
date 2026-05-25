"""/v2/ — built SPA mount (Stage 4 parallel ship)."""
from __future__ import annotations

from pathlib import Path


def test_v2_root_serves_index_html(monkeypatch, tmp_path):
    """GET /v2/ must return the built SPA's index.html when it exists."""
    static_v2 = tmp_path / "static_v2"
    static_v2.mkdir()
    (static_v2 / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div></body></html>"
    )
    import backend.api.spa as spa_mod
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", static_v2)
    from main import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as fresh:
        r = fresh.get("/v2/")
    assert r.status_code == 200
    assert b"<div id='root'>" in r.content


def test_v2_missing_dir_returns_404(monkeypatch, tmp_path):
    """If backend/static_v2 doesn't exist, /v2/ returns 404."""
    import backend.api.spa as spa_mod
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", tmp_path / "nonexistent")
    from main import create_app
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as fresh:
        r = fresh.get("/v2/")
    assert r.status_code == 404
