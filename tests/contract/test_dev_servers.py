"""GET /dev-servers contract.

NOTE: This endpoint does not exist in the current main.py (as of Stage 1).
The test documents this absence — a 404 or 405 is expected. This test
should be updated when the endpoint is added in a future stage.
"""
from __future__ import annotations


def test_dev_servers_not_in_current_main(client):
    """GET /dev-servers is not defined in main.py at Stage 1 — expect 404."""
    r = client.get("/dev-servers")
    # Observed: no route registered → FastAPI returns 404 Not Found
    assert r.status_code == 404
