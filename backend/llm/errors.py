"""OpenAI error extraction and classification.

Two responsibilities:
  1. Pull a structured (code, message) pair out of the various shapes the SDK
     uses for API errors (the body attr, the response.json(), or fallback
     str(exc)).
  2. Classify status codes as permanent vs retryable so the retry loop knows
     when to give up.
"""
from __future__ import annotations

from typing import Optional, Tuple

PERMANENT_STATUS: frozenset[int] = frozenset({400, 401, 403, 404})
RETRYABLE_STATUS: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


def is_permanent_status(status: int) -> bool:
    return status in PERMANENT_STATUS


def is_retryable_status(status: int) -> bool:
    return status in RETRYABLE_STATUS


def extract_openai_error(exc: Exception) -> Tuple[Optional[str], Optional[str]]:
    """Return (code, message) pulled from whatever shape the SDK exception has.

    Tries in order:
      1. exc.body['error'] (or exc.body directly)
      2. exc.response.json()['error'] (or .json() top-level)
      3. exc.message (or str(exc), capped at 300 chars)
    """
    code: Optional[str] = None
    message: Optional[str] = None

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error") if isinstance(body.get("error"), dict) else None
        if isinstance(err, dict):
            code = err.get("code")
            message = err.get("message")
        else:
            code = body.get("code")
            message = body.get("message")

    if code is None or message is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    err2 = payload.get("error") if isinstance(payload.get("error"), dict) else None
                    if isinstance(err2, dict):
                        code = code or err2.get("code")
                        message = message or err2.get("message")
                    else:
                        code = code or payload.get("code")
                        message = message or payload.get("message")
            except Exception:
                pass

    if message is None:
        msg = getattr(exc, "message", None) or str(exc)
        if msg:
            message = msg[:300]

    return code, message
