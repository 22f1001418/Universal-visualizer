"""GET /jobs and GET /jobs/{id} contracts."""
from __future__ import annotations

_ONE_TOPIC_RESPONSE = (
    '{"extraction_note": "one topic", "topics": [{'
    '"section": "## Seed", "topic": "Seed Topic",'
    '"embed_after_sentence": "Seed content.",'
    '"why_visual_helps": "Visual aids comprehension.",'
    '"audience_difficulty": "beginner"'
    '}]}'
)


def _seed_job(client, fake_llm) -> str:
    fake_llm.responses["agent_A_topic_extraction"] = _ONE_TOPIC_RESPONSE
    files = {"file": ("seed.md", b"# Seed\n\nSeed content.\n", "text/markdown")}
    r = client.post("/upload", files=files, data={"track": "Academy DSA", "module": "intro"})
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_list_jobs_returns_list(client, fake_llm):
    _seed_job(client, fake_llm)
    r = client.get("/jobs")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    if body:
        first = body[0]
        assert {"job_id", "script_name", "status", "created_at",
                "topic_count", "build_count"} <= set(first.keys())


def test_get_job_returns_full_state(client, fake_llm):
    job_id = _seed_job(client, fake_llm)
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    body = r.json()
    assert {"job_id", "script_name", "status", "topics",
            "suggestions", "builds", "manifest", "logs",
            "token_usage", "created_at"} <= set(body.keys())
    assert body["job_id"] == job_id


def test_get_job_404(client):
    r = client.get("/jobs/does-not-exist")
    # Observed: main.py raises HTTPException(404, ...) via get_or_404 + KeyError path
    assert r.status_code == 404
