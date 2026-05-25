"""GET /jobs/{id}/manifest contract."""
from __future__ import annotations


def test_manifest_404_for_unknown_job(client):
    r = client.get("/jobs/nope/manifest")
    # Observed: main.py raises HTTPException(404, ...) via get_or_404 + KeyError path
    assert r.status_code == 404
