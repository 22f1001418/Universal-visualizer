from __future__ import annotations

from backend.llm.errors import (
    extract_openai_error,
    is_retryable_status,
    is_permanent_status,
    PERMANENT_STATUS,
    RETRYABLE_STATUS,
)


class _FakeExc(Exception):
    def __init__(self, body=None, response=None, message=None):
        self.body = body
        self.response = response
        self.message = message


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_extract_from_body_dict():
    e = _FakeExc(body={"error": {"code": "model_not_found", "message": "no such model"}})
    code, msg = extract_openai_error(e)
    assert code == "model_not_found"
    assert msg == "no such model"


def test_extract_from_response_json_fallback():
    e = _FakeExc(response=_FakeResp({"error": {"code": "rate_limit_exceeded",
                                                "message": "slow down"}}))
    code, msg = extract_openai_error(e)
    assert code == "rate_limit_exceeded"
    assert msg == "slow down"


def test_extract_from_message_attr_when_body_missing():
    e = _FakeExc(message="generic boom")
    code, msg = extract_openai_error(e)
    assert code is None
    assert msg == "generic boom"


def test_extract_message_truncated_to_300_chars():
    long = "x" * 1000
    e = _FakeExc(message=long)
    _, msg = extract_openai_error(e)
    assert msg is not None and len(msg) <= 300


def test_retryable_set_contains_429_and_5xx():
    assert 429 in RETRYABLE_STATUS
    assert 500 in RETRYABLE_STATUS
    assert 502 in RETRYABLE_STATUS
    assert 503 in RETRYABLE_STATUS
    assert 504 in RETRYABLE_STATUS


def test_permanent_set_contains_4xx_no_retry():
    assert 400 in PERMANENT_STATUS
    assert 401 in PERMANENT_STATUS
    assert 403 in PERMANENT_STATUS
    assert 404 in PERMANENT_STATUS


def test_is_retryable_status_helper():
    assert is_retryable_status(429) is True
    assert is_retryable_status(500) is True
    assert is_retryable_status(401) is False


def test_is_permanent_status_helper():
    assert is_permanent_status(401) is True
    assert is_permanent_status(429) is False
