"""GET /healthz contract — locked at Stage 1."""
from __future__ import annotations


def test_healthz_returns_documented_shape(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert {"ok", "fixed_main_path", "fixed_main_exists",
            "text_model", "output_dir"} <= set(body.keys())
    assert isinstance(body["ok"], bool)
    assert isinstance(body["fixed_main_exists"], bool)
    assert isinstance(body["text_model"], str)
