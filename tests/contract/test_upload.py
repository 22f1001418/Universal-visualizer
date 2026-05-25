"""POST /upload contract — locked at Stage 1."""
from __future__ import annotations

_ONE_TOPIC_RESPONSE = (
    '{"extraction_note": "one topic", "topics": [{'
    '"section": "## Test", "topic": "Test Topic",'
    '"embed_after_sentence": "A short script.",'
    '"why_visual_helps": "Visual aids comprehension.",'
    '"audience_difficulty": "beginner"'
    '}]}'
)


def test_upload_minimal_md_returns_upload_response(client, fake_llm):
    fake_llm.responses["agent_A_topic_extraction"] = _ONE_TOPIC_RESPONSE
    files = {"file": ("test.md", b"# Test\n\nA short script.\n", "text/markdown")}
    r = client.post("/upload", files=files, data={"track": "Academy DSA"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"job_id", "script_name", "char_count", "status"} <= set(body.keys())
    assert body["script_name"] == "test.md"
    assert isinstance(body["job_id"], str) and len(body["job_id"]) > 0
    assert isinstance(body["char_count"], int) and body["char_count"] > 0
    assert body["status"] in {"uploaded", "topics_extracted",
                              "awaiting_user_picks", "building",
                              "done", "failed"}


def test_upload_rejects_non_md_file(client, fake_llm):
    # .pdf is not in the allowed list (.md / .markdown / .txt) — main.py returns 400
    files = {"file": ("hello.pdf", b"%PDF-1.4 content", "application/pdf")}
    r = client.post("/upload", files=files, data={"track": "Academy DSA"})
    # Observed: main.py raises HTTPException(400, ...) for non-.md/.txt/.markdown files
    assert r.status_code == 400
