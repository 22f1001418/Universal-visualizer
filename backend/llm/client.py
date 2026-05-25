"""The single LLM call entry point.

Public surface:
  - get_client() → lazy OpenAI() singleton, with timeouts and silenced SDK logs
  - llm_call(...) → the call function used by every agent and the viz generator

Behaviour preserved from the original llm_client:
  - SDK retries disabled (our loop is the sole retry layer)
  - Reasoning-model handling (max_completion_tokens + reasoning_effort, no temp)
  - 5 retries, exponential backoff with cap
  - Permanent vs retryable classification via backend.llm.errors
  - Per-call token tracking via backend.llm.tracker
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

from backend.llm.errors import extract_openai_error, is_retryable_status
from backend.llm.reasoning import is_reasoning_model
from backend.llm.tasks import LLMTask, resolve_model
from backend.llm.tracker import token_tracker

logger = logging.getLogger("hackmd-orch.llm")


REASONING_EFFORT = (os.getenv("REASONING_EFFORT", "low") or "low").lower()
if REASONING_EFFORT not in ("low", "medium", "high"):
    REASONING_EFFORT = "low"

MAX_OUTPUT_TOKENS_DEFAULT = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))


_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    """Return the lazily-initialised OpenAI client singleton."""
    global _client
    if _client is not None:
        return _client

    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        logger.error("[FATAL] OPENAI_API_KEY missing. Add it to .env")
        sys.exit(1)

    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    client_timeout = float(os.getenv("LLM_CLIENT_TIMEOUT", "600"))
    _client = OpenAI(api_key=api_key, timeout=client_timeout, max_retries=0)
    logger.info("[LLM] OpenAI client ready  timeout=%.0fs", client_timeout)
    return _client


def llm_call(
    system_prompt: str,
    user_prompt: str,
    step_label: str,
    job_id: Optional[str] = None,
    task: Optional[LLMTask] = None,
    temperature: float = 0.2,
    max_tokens: int = MAX_OUTPUT_TOKENS_DEFAULT,
    json_mode: bool = False,
) -> str:
    """Single LLM call with retries, token tracking, and reasoning-model handling."""
    client = get_client()
    model = resolve_model(task)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    create_kwargs: dict = {"model": model, "messages": messages}
    if is_reasoning_model(model):
        create_kwargs["max_completion_tokens"] = max_tokens
        create_kwargs["reasoning_effort"] = REASONING_EFFORT
    else:
        create_kwargs["temperature"] = temperature
        create_kwargs["max_tokens"] = max_tokens

    if json_mode:
        create_kwargs["response_format"] = {"type": "json_object"}

    MAX_RETRIES = 5
    BASE_DELAY = 5.0
    MAX_DELAY = 90.0

    last_exc: Optional[Exception] = None
    logger.info("[LLM] -> %s  (model=%s)", step_label, model)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = client.chat.completions.create(**create_kwargs)
            break
        except AuthenticationError as e:
            logger.error("[LLM] FAIL %s — auth error (401). Check OPENAI_API_KEY.", step_label)
            raise RuntimeError(f"OpenAI auth failed: {e}") from e
        except APIStatusError as exc:
            code, message = extract_openai_error(exc)
            if is_retryable_status(exc.status_code):
                delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
                logger.warning("[LLM] retry %d/%d %s — %d (waiting %.0fs)",
                               attempt, MAX_RETRIES, step_label, exc.status_code, delay)
                last_exc = exc
                time.sleep(delay)
                continue
            if exc.status_code == 403 and code == "model_not_found":
                logger.error("[LLM] FAIL %s — 403 model_not_found for '%s': %s",
                             step_label, model, message)
            elif exc.status_code == 404:
                logger.error("[LLM] FAIL %s — model '%s' not found.", step_label, model)
            else:
                logger.error("[LLM] FAIL %s — %d: %s", step_label, exc.status_code, message)
            raise RuntimeError(f"OpenAI permanent error {exc.status_code}: {message}") from exc
        except RateLimitError as e:
            delay = min(BASE_DELAY * (3 ** (attempt - 1)), MAX_DELAY)
            logger.warning("[LLM] retry %d/%d %s — rate limited (waiting %.0fs)",
                           attempt, MAX_RETRIES, step_label, delay)
            last_exc = e
            time.sleep(delay)
            continue
        except APITimeoutError as e:
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            logger.warning("[LLM] retry %d/%d %s — client timeout. Waiting %.0fs.",
                           attempt, MAX_RETRIES, step_label, delay)
            last_exc = e
            time.sleep(delay)
            continue
        except APIConnectionError as e:
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            logger.warning("[LLM] retry %d/%d %s — network error: %s",
                           attempt, MAX_RETRIES, step_label, str(e)[:120])
            last_exc = e
            time.sleep(delay)
            continue
        except Exception as e:  # noqa: BLE001 — final-fallback retry
            delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
            logger.warning("[LLM] retry %d/%d %s — %s: %s",
                           attempt, MAX_RETRIES, step_label,
                           type(e).__name__, str(e)[:120])
            last_exc = e
            time.sleep(delay)
            continue
    else:
        raise RuntimeError(
            f"LLM call '{step_label}' failed after {MAX_RETRIES} retries. "
            f"Last error: {type(last_exc).__name__}: {last_exc}"
        )

    choice = r.choices[0]
    raw = choice.message.content
    if raw is None:
        logger.error("[LLM] %s returned None content. finish_reason=%s",
                     step_label, choice.finish_reason)
        raw = getattr(choice.message, "reasoning_content", None) or ""
        if not raw:
            raise RuntimeError(f"LLM '{step_label}' returned empty content")

    if r.usage is not None:
        in_t = r.usage.prompt_tokens or 0
        out_t = r.usage.completion_tokens or 0
        details = getattr(r.usage, "completion_tokens_details", None)
        reasoning_t = 0
        if details is not None:
            reasoning_t = (
                getattr(details, "reasoning_tokens", None)
                or (details.get("reasoning_tokens") if isinstance(details, dict) else 0)
                or 0
            )
        token_tracker.record(
            step_label=step_label,
            input_tokens=in_t,
            output_tokens=out_t,
            job_id=job_id,
            model=model,
            reasoning_tokens=reasoning_t,
        )

    logger.info("[LLM] OK %s  (%d chars)", step_label, len(raw))
    return raw.strip()
