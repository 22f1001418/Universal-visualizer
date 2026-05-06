"""LLM client wrapper.

All the patterns we settled on in fixed_main_v6.py and image_gen_main.py:
  - Reasoning-model aware (gpt-5*, o-series): max_completion_tokens + reasoning_effort
  - SDK retries disabled (max_retries=0) — our loop is the sole retry layer
  - Permanent vs retryable error classification with sub-cause routing
  - Per-call token tracking with USD cost
  - Per-job budget enforcement
"""
from __future__ import annotations

import logging
import os
import sys
import time
from threading import Lock
from typing import Optional

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

logger = logging.getLogger("hackmd-orch.llm")


# ───────────────────────────────────────────
# Config
# ───────────────────────────────────────────

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")

REASONING_EFFORT = os.getenv("REASONING_EFFORT", "low").lower()
if REASONING_EFFORT not in ("low", "medium", "high"):
    REASONING_EFFORT = "low"

# Per-call ceiling — gets passed as max_tokens (or max_completion_tokens for reasoning models)
MAX_OUTPUT_TOKENS = int(os.getenv("MAX_OUTPUT_TOKENS", "4096"))

# Per-job hard budget (input + output tokens combined). Aborts job on overrun.
TOKEN_BUDGET_PER_JOB = int(os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))

# OpenAI returns errors in these status code buckets
PERMANENT_STATUS = {400, 401, 403, 404}
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

# Reasoning model detection
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str) -> bool:
    name = (model or "").lower()
    return any(name.startswith(p) for p in _REASONING_PREFIXES)


# ───────────────────────────────────────────
# Pricing (USD per 1K tokens). Update when OpenAI changes prices.
# ───────────────────────────────────────────

PRICE_PER_1K = {
    "gpt-4o-mini":      {"input": 0.00015, "output": 0.0006},
    "gpt-4o":           {"input": 0.0025,  "output": 0.010},
    "gpt-4.1":          {"input": 0.0020,  "output": 0.008},
    "gpt-4.1-mini":     {"input": 0.00040, "output": 0.0016},
    "gpt-4-turbo":      {"input": 0.010,   "output": 0.030},
    "gpt-5":            {"input": 0.00125, "output": 0.010},
    "gpt-5-mini":       {"input": 0.00025, "output": 0.0020},
    "gpt-5-nano":       {"input": 0.00005, "output": 0.0004},
    "o1":               {"input": 0.015,   "output": 0.060},
    "o1-mini":          {"input": 0.0011,  "output": 0.0044},
    "o3":               {"input": 0.010,   "output": 0.040},
    "o3-mini":          {"input": 0.0011,  "output": 0.0044},
    "o4-mini":          {"input": 0.0011,  "output": 0.0044},
}


# ───────────────────────────────────────────
# Token usage tracker — process-lifetime + per-job buckets
# ───────────────────────────────────────────

class TokenUsageTracker:
    def __init__(self) -> None:
        self.total_input = 0
        self.total_output = 0
        self.total_calls = 0
        self._per_job: dict[str, dict] = {}
        self._lock = Lock()

    def _cost_usd(self, in_t: int, out_t: int, model: Optional[str] = None) -> float:
        prices = PRICE_PER_1K.get(model or TEXT_MODEL)
        if not prices:
            return 0.0
        return (in_t / 1000) * prices["input"] + (out_t / 1000) * prices["output"]

    def record(
        self,
        step_label: str,
        input_tokens: int,
        output_tokens: int,
        job_id: Optional[str] = None,
        model: Optional[str] = None,
        reasoning_tokens: int = 0,
    ) -> None:
        with self._lock:
            self.total_input += input_tokens
            self.total_output += output_tokens
            self.total_calls += 1
            job_total = 0
            if job_id is not None:
                bucket = self._per_job.setdefault(
                    job_id, {"input": 0, "output": 0, "calls": 0, "reasoning": 0}
                )
                bucket["input"] += input_tokens
                bucket["output"] += output_tokens
                bucket["calls"] += 1
                bucket["reasoning"] += reasoning_tokens
                job_total = bucket["input"] + bucket["output"]

        cost = self._cost_usd(input_tokens, output_tokens, model)
        model_tag = (model or TEXT_MODEL)[:18]

        if job_id is not None:
            logger.info(
                "[Tokens] %-30s [%s] in=%-6d out=%-5d cost=$%.4f  job=%d/%d (%.0f%%)",
                step_label, model_tag, input_tokens, output_tokens, cost,
                job_total, TOKEN_BUDGET_PER_JOB,
                (job_total / TOKEN_BUDGET_PER_JOB) * 100 if TOKEN_BUDGET_PER_JOB else 0,
            )
            if reasoning_tokens > 0 and output_tokens > 0:
                visible = max(output_tokens - reasoning_tokens, 0)
                logger.info(
                    "  [Reasoning] hidden=%d  visible=%d  ratio=%.0f%%",
                    reasoning_tokens, visible,
                    (reasoning_tokens / output_tokens) * 100,
                )
            if job_total > TOKEN_BUDGET_PER_JOB:
                raise RuntimeError(
                    f"Job {job_id} exceeded token budget: "
                    f"{job_total} > {TOKEN_BUDGET_PER_JOB}. Aborting."
                )
        else:
            logger.info(
                "[Tokens] %-30s [%s] in=%-6d out=%-5d cost=$%.4f",
                step_label, model_tag, input_tokens, output_tokens, cost,
            )

    def job_summary(self, job_id: str) -> dict:
        with self._lock:
            bucket = self._per_job.get(
                job_id, {"input": 0, "output": 0, "calls": 0, "reasoning": 0}
            )
        return {
            "calls": bucket["calls"],
            "input_tokens": bucket["input"],
            "output_tokens": bucket["output"],
            "reasoning_tokens": bucket.get("reasoning", 0),
            "total_tokens": bucket["input"] + bucket["output"],
            "estimated_cost_usd": round(self._cost_usd(bucket["input"], bucket["output"]), 4),
        }


token_tracker = TokenUsageTracker()


# ───────────────────────────────────────────
# OpenAI client init — disable SDK retries, silence its logger
# ───────────────────────────────────────────

_client: Optional[OpenAI] = None


def get_client() -> OpenAI:
    global _client
    if _client is not None:
        return _client

    if not OPENAI_API_KEY:
        logger.error("[FATAL] OPENAI_API_KEY missing. Add it to .env")
        sys.exit(1)

    # Silence the SDK's own retry logs — our loop is the sole retry layer.
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Reasoning models (gpt-5*, o-series) can spend several minutes generating
    # hidden thinking tokens before any output. 120s is too short for them.
    client_timeout = float(os.getenv("LLM_CLIENT_TIMEOUT", "600"))
    _client = OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=client_timeout,
        max_retries=0,
    )
    logger.info("[LLM] OpenAI client ready  model=%s  timeout=%.0fs",
                TEXT_MODEL, client_timeout)
    if is_reasoning_model(TEXT_MODEL):
        logger.info("[LLM] Detected REASONING model — using max_completion_tokens "
                    "+ reasoning_effort=%s", REASONING_EFFORT)
        logger.info("[LLM] (temperature/top_p will be omitted on this model)")
    return _client


# ───────────────────────────────────────────
# Error body extraction (for sub-cause routing on 403)
# ───────────────────────────────────────────

def _extract_openai_error(exc: Exception) -> tuple[Optional[str], Optional[str]]:
    code: Optional[str] = None
    message: Optional[str] = None

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        err = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
        code = err.get("code") or body.get("code")
        message = err.get("message") or body.get("message")

    if code is None or message is None:
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                payload = resp.json()
                if isinstance(payload, dict):
                    err2 = payload.get("error", {}) if isinstance(payload.get("error"), dict) else {}
                    code = code or err2.get("code") or payload.get("code")
                    message = message or err2.get("message") or payload.get("message")
            except Exception:
                pass

    if message is None:
        msg = getattr(exc, "message", None) or str(exc)
        if msg:
            message = msg[:300]
    return code, message


# ───────────────────────────────────────────
# The single LLM call function
# ───────────────────────────────────────────

def llm_call(
    system_prompt: str,
    user_prompt: str,
    step_label: str,
    job_id: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: int = MAX_OUTPUT_TOKENS,
    json_mode: bool = False,
) -> str:
    """Single LLM call with retries, token tracking, and reasoning-model handling."""
    client = get_client()

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": user_prompt},
    ]

    create_kwargs: dict = {"model": TEXT_MODEL, "messages": messages}
    if is_reasoning_model(TEXT_MODEL):
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

    logger.info("[LLM] -> %s  (model=%s)", step_label, TEXT_MODEL)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = client.chat.completions.create(**create_kwargs)
            break
        except AuthenticationError as e:
            logger.error("[LLM] FAIL %s — auth error (401). Check OPENAI_API_KEY.", step_label)
            raise RuntimeError(f"OpenAI auth failed: {e}") from e
        except APIStatusError as exc:
            code, message = _extract_openai_error(exc)
            if exc.status_code in RETRYABLE_STATUS:
                delay = min(BASE_DELAY * (2 ** (attempt - 1)), MAX_DELAY)
                logger.warning("[LLM] retry %d/%d %s — %d (waiting %.0fs)",
                               attempt, MAX_RETRIES, step_label, exc.status_code, delay)
                last_exc = exc
                time.sleep(delay)
                continue
            # Permanent — fail with a useful message
            if exc.status_code == 403 and code == "model_not_found":
                logger.error("[LLM] FAIL %s — 403 model_not_found for '%s'.", step_label, TEXT_MODEL)
                logger.error("       Cause: %s", message)
                logger.error("       Fix:   set OPENAI_TEXT_MODEL=gpt-4o-mini (or a model you have access to)")
            elif exc.status_code == 403:
                logger.error("[LLM] FAIL %s — 403 (code=%s): %s", step_label, code, message)
            elif exc.status_code == 404:
                logger.error("[LLM] FAIL %s — model '%s' not found.", step_label, TEXT_MODEL)
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
            logger.warning(
                "[LLM] retry %d/%d %s — client timeout (model=%s slower than "
                "LLM_CLIENT_TIMEOUT=%.0fs). Waiting %.0fs.",
                attempt, MAX_RETRIES, step_label, TEXT_MODEL,
                float(os.getenv("LLM_CLIENT_TIMEOUT", "600")), delay,
            )
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
        except Exception as e:
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

    # ── Extract content ──
    choice = r.choices[0]
    raw = choice.message.content

    if raw is None:
        # Defensive — could be a refusal or content_filter or thinking-model quirk
        logger.error("[LLM] %s returned None content. finish_reason=%s",
                     step_label, choice.finish_reason)
        # Try reasoning_content fallback (Gemini-style)
        raw = getattr(choice.message, "reasoning_content", None) or ""
        if not raw:
            raise RuntimeError(f"LLM '{step_label}' returned empty content")

    # ── Token tracking ──
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
            model=TEXT_MODEL,
            reasoning_tokens=reasoning_t,
        )

    logger.info("[LLM] OK %s  (%d chars)", step_label, len(raw))
    return raw.strip()
