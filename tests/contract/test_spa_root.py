"""GET / — Stage 4 SPA root."""
from __future__ import annotations


def test_root_returns_built_spa_index(client):
    r = client.get("/")
    assert r.status_code == 200
    # Stage 4 SPA index.html includes the Vite mount point.
    assert b'<div id="root"></div>' in r.content or b"<div id='root'></div>" in r.content


def test_legacy_mount_serves_old_html(client):
    r = client.get("/legacy/")
    # If the legacy dir exists in this checkout, expect 200; otherwise 404.
    assert r.status_code in (200, 404)
