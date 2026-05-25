"""POST /jobs/{id}/topics/{tid}/suggestions contract."""
from __future__ import annotations


def test_suggestions_endpoint_404_for_unknown_job(client):
    r = client.post("/jobs/nope/topics/topic_1/suggestions")
    # Observed: main.py raises HTTPException(404, ...) via get_or_404 + KeyError path
    assert r.status_code == 404
