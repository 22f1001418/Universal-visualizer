"""POST /jobs/{id}/topics/{tid}/build contract."""
from __future__ import annotations


def test_build_endpoint_404_for_unknown_job(client):
    r = client.post(
        "/jobs/nope/topics/topic_1/build",
        json={"suggestion_id": "viz_1", "custom_notes": ""},
    )
    # Observed: main.py raises HTTPException(404, ...) via get_or_404 + KeyError path
    assert r.status_code == 404
