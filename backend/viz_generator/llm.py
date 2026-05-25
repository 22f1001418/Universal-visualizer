"""LLM client used by the viz generator (Universal Visualizer subprocess).

Multi-provider (OpenAI + Gemini) via LLM_PROVIDER env var. Different from
backend/llm/client.py — which is OpenAI-only and uses (system_prompt,
user_prompt). This module uses messages: list[dict] and supports two
providers. The two coexist intentionally; unification is a Stage-3+
decision.

Stage-1 deduplicated helpers are pulled in from backend.llm:
  - PRICE_PER_1K (the pricing table)
  - REASONING_PREFIXES + is_reasoning_model (reasoning-model detection)
  - cost_usd (per-call cost computation)
  - extract_openai_error (OpenAI error body parsing)
"""
from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any  # noqa: F401 — available for callers

try:
    from dotenv import load_dotenv
except ImportError:
    print("Missing: pip install python-dotenv")
    sys.exit(1)

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AuthenticationError,
        OpenAI,
        RateLimitError,
    )
except ImportError:
    print("Missing: pip install openai python-dotenv playwright")
    sys.exit(1)

# LangSmith tracing — optional. If installed and LANGSMITH_API_KEY is set,
# every LLM call is logged to your LangSmith dashboard for inspection.
try:
    from langsmith.wrappers import wrap_openai
    from langsmith import traceable
    _LANGSMITH_AVAILABLE = True
except ImportError:
    _LANGSMITH_AVAILABLE = False
    # Stub decorator so @traceable still works when LangSmith isn't installed
    def traceable(*dargs, **dkwargs):  # type: ignore
        def decorator(fn):
            return fn
        # Support both @traceable and @traceable(name="...")
        if dargs and callable(dargs[0]):
            return dargs[0]
        return decorator


# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viz_agent")


# ───────────────────────────────────────────
# Stage-1 deduplicated helpers from backend.llm
# ───────────────────────────────────────────
from backend.llm import is_reasoning_model
from backend.llm import LLMTask  # noqa: F401 — for Stage 2 call sites
from backend.llm import resolve_model
from backend.llm import PRICE_PER_1K as PRICE_PER_1K_TOKENS  # noqa: F401
from backend.llm import REASONING_PREFIXES as _REASONING_MODEL_PREFIXES  # noqa: F401
from backend.llm import extract_openai_error as _extract_openai_error  # noqa: F401


# ─────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────

# LLM provider — choose "openai" or "gemini" via .env LLM_PROVIDER
# Default keeps backward compatibility with previous Gemini setup.
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "openai").lower()

# Model name picked per provider
DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-5",       # cheap + fast for paid use; switch to gpt-4o for quality
    "gemini": "gemini-2.5-flash",
}
MODEL_NAME: str = os.getenv("MODEL_NAME") or DEFAULT_MODELS.get(LLM_PROVIDER, "gpt-5")

# ─── COST GUARD-RAILS ──────────────────────────────────────────
# Hard budget for a single agent run. Once cumulative tokens exceed
# this, the run aborts. Override with TOKEN_BUDGET in .env.
TOKEN_BUDGET: int = int(os.getenv("TOKEN_BUDGET", "500000"))

LLM_DEFAULT_TEMPERATURE: float = 0.2
LLM_DEFAULT_MAX_TOKENS: int = 16000

# Per-call max output tokens. Lowered from 16000 for fix-only calls to
# prevent the model from re-emitting the entire codebase needlessly.
LLM_FIX_MAX_TOKENS: int = 4096

# NOTE: backend.llm.PRICE_PER_1K omits Gemini entries; they are present in
# the merge below for backward-compat token cost reporting on Gemini runs.
# Stage 2 will add a Gemini-aware pricing table to backend.llm.pricing.
_GEMINI_PRICES: dict[str, dict[str, float]] = {
    "gemini-2.5-flash": {"input": 0.0,     "output": 0.0},
    "gemini-1.5-pro":   {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.0,     "output": 0.0},
}
# Merge so PRICE_PER_1K_TOKENS has full coverage (OpenAI + Gemini)
PRICE_PER_1K_TOKENS = {**PRICE_PER_1K_TOKENS, **_GEMINI_PRICES}  # type: ignore[assignment]


# ─── REASONING MODELS ──────────────────────────────────────────
# OpenAI reasoning models (gpt-5*, o1*, o3*, o4*) have a DIFFERENT API contract:
#   - Do NOT accept `temperature` (only default = 1 supported)
#   - Do NOT accept `max_tokens`. Use `max_completion_tokens` instead.
#   - `completion_tokens` in usage INCLUDES hidden reasoning tokens, which can
#     easily 5-30x token consumption. Budget accordingly.
#   - Accept optional `reasoning_effort`: "low" / "medium" / "high".
#   - Do NOT accept `top_p`, `presence_penalty`, `frequency_penalty`, `logprobs`.

def _is_reasoning_model(model_name: str) -> bool:
    """True for OpenAI reasoning models — delegates to backend.llm.is_reasoning_model."""
    return is_reasoning_model(model_name)

# Reasoning effort: low = cheaper/faster, high = more reasoning (more tokens, slower).
# Read once at startup; default "low" keeps cost reasonable.
REASONING_EFFORT: str = os.getenv("REASONING_EFFORT", "low").lower()
if REASONING_EFFORT not in ("low", "medium", "high"):
    REASONING_EFFORT = "low"


# ─────────────────────────────────────────────────────────────
# SETUP — deferred until main() to avoid import-time side effects
# ─────────────────────────────────────────────────────────────

_client: OpenAI | None = None


# ─────────────────────────────────────────────────────────────
# TOKEN USAGE TRACKER  — global counter for cost monitoring
# ─────────────────────────────────────────────────────────────

class TokenUsageTracker:
    """Aggregates token usage and dollar cost across all LLM calls.

    Prints a per-call cost line (status update) plus a final summary.
    Aborts the run if cumulative tokens exceed TOKEN_BUDGET.
    """
    def __init__(self) -> None:
        self.total_input  = 0
        self.total_output = 0
        self.total_calls  = 0
        self.per_step: dict[str, dict[str, int]] = {}

    def record(self, step_label: str, input_tokens: int, output_tokens: int) -> None:
        self.total_input  += input_tokens
        self.total_output += output_tokens
        self.total_calls  += 1

        bucket = self.per_step.setdefault(step_label, {"input": 0, "output": 0, "calls": 0})
        bucket["input"]  += input_tokens
        bucket["output"] += output_tokens
        bucket["calls"]  += 1

        cost = self._cost_usd(input_tokens, output_tokens)
        cum_total = self.total_input + self.total_output
        log.info(
            "[Tokens] %-30s in=%-6d out=%-5d cost=$%.4f  cum=%d/%d (%.0f%%)",
            step_label, input_tokens, output_tokens, cost,
            cum_total, TOKEN_BUDGET,
            (cum_total / TOKEN_BUDGET) * 100 if TOKEN_BUDGET else 0,
        )

        # Hard stop on budget overrun
        if cum_total > TOKEN_BUDGET:
            log.error(
                "[Tokens] BUDGET EXCEEDED — used %d / %d. Aborting to prevent runaway cost.",
                cum_total, TOKEN_BUDGET,
            )
            log.error("  Increase TOKEN_BUDGET in .env if intentional.")
            self.print_summary()
            sys.exit(3)

    def _cost_usd(self, input_tokens: int, output_tokens: int) -> float:
        prices = PRICE_PER_1K_TOKENS.get(MODEL_NAME)
        if not prices:
            return 0.0
        return (input_tokens / 1000) * prices["input"] + (output_tokens / 1000) * prices["output"]

    def total_cost_usd(self) -> float:
        return self._cost_usd(self.total_input, self.total_output)

    def print_summary(self) -> None:
        log.info("")
        log.info("━━━ TOKEN USAGE SUMMARY ━━━")
        log.info("Model: %s", MODEL_NAME)
        log.info("Total calls : %d", self.total_calls)
        log.info("Total input : %d tokens", self.total_input)
        log.info("Total output: %d tokens", self.total_output)
        log.info("Total       : %d tokens", self.total_input + self.total_output)
        log.info("Total cost  : $%.4f", self.total_cost_usd())
        log.info("")
        log.info("Per-step breakdown:")
        for step, b in self.per_step.items():
            log.info("  %-30s calls=%d  in=%d  out=%d  $%.4f",
                     step, b["calls"], b["input"], b["output"],
                     self._cost_usd(b["input"], b["output"]))
        log.info("━━━━━━━━━━━━━━━━━━━━━━━━━━")


# Global tracker — reset per run in main()
token_tracker = TokenUsageTracker()


# ─────────────────────────────────────────────────────────────
# STATUS PRINT — visible progress markers throughout the run
# ─────────────────────────────────────────────────────────────

def status(stage: str, detail: str = "") -> None:
    """Print a clearly-marked status line to stdout."""
    bar = "─" * 4
    if detail:
        log.info("\n%s [STATUS] %s — %s %s", bar, stage, detail, bar)
    else:
        log.info("\n%s [STATUS] %s %s", bar, stage, bar)


def _init_client() -> OpenAI:
    """Lazily create the LLM client. Called once from main().

    Supports two providers via .env LLM_PROVIDER:
      - "openai" (default for paid use): standard OpenAI API
      - "gemini": Google AI Studio's OpenAI-compatible endpoint
    """
    global _client
    if _client is not None:
        return _client

    load_dotenv()

    # Silence the OpenAI SDK's chatty INFO-level logs ("Retrying request to ...").
    # We have our own retry loop with proper logging, so the SDK's INFO output
    # is just noise. Keep WARNING and above so genuine SDK warnings still surface.
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if LLM_PROVIDER == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            log.error("[ERROR] Missing OPENAI_API_KEY environment variable.")
            log.error("  Get a key at https://platform.openai.com/api-keys")
            log.error("  Then add to .env:  OPENAI_API_KEY=sk-...")
            sys.exit(1)
        # Reasoning models (gpt-5*, o-series) generate hidden thinking tokens
        # that count against max_completion_tokens. A single call at 16k tokens
        # can take 5-10 minutes — far longer than the 200s default. Override
        # via LLM_CLIENT_TIMEOUT in .env if needed.
        client_timeout = float(os.getenv("LLM_CLIENT_TIMEOUT", "600"))
        client = OpenAI(
            api_key=api_key,
            timeout=client_timeout,
            max_retries=0,   # disable SDK retries — our llm_call() loop is the sole retry layer
        )
        log.info("[LLM] Provider: OpenAI    Model: %s    timeout=%.0fs",
                 MODEL_NAME, client_timeout)
        if _is_reasoning_model(MODEL_NAME):
            log.info("[LLM] Detected REASONING model (%s)", MODEL_NAME)
            log.info("       - temperature, max_tokens, top_p are ignored on this model.")
            log.info("       - reasoning_effort=%s  (override via .env REASONING_EFFORT)", REASONING_EFFORT)
            log.info("       - completion_tokens INCLUDES hidden reasoning tokens.")
            log.info("       - Expect 5-30x more output tokens vs. non-reasoning models.")
            if TOKEN_BUDGET < 1_500_000:
                log.warning(
                    "       - WARNING: TOKEN_BUDGET=%d may be tight for a reasoning model. "
                    "Consider raising it to 1500000+ in .env.", TOKEN_BUDGET,
                )
    elif LLM_PROVIDER == "gemini":
        api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            log.error("[ERROR] Missing GEMINI_API_KEY environment variable.")
            log.error("  Get a key at https://aistudio.google.com/apikey")
            log.error("  Then add to .env:  GEMINI_API_KEY=your_key_here")
            sys.exit(1)
        client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=api_key,
            timeout=float(os.getenv("LLM_CLIENT_TIMEOUT", "600")),
            max_retries=0,   # disable SDK retries — our llm_call() loop is the sole retry layer
        )
        log.info("[LLM] Provider: Gemini    Model: %s", MODEL_NAME)
    else:
        log.error("[ERROR] Unknown LLM_PROVIDER '%s'. Use 'openai' or 'gemini'.", LLM_PROVIDER)
        sys.exit(1)

    # ── LangSmith tracing wrap ────────────────────────────────
    # If langsmith is installed AND LANGSMITH_API_KEY is set, wrap the client
    # so every chat.completions.create call is auto-traced.
    if _LANGSMITH_AVAILABLE and os.getenv("LANGSMITH_API_KEY"):
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_PROJECT", "viz-agent")
        client = wrap_openai(client)
        log.info("[LLM] LangSmith tracing: ENABLED  (project=%s)",
                 os.environ["LANGSMITH_PROJECT"])
    else:
        log.info("[LLM] LangSmith tracing: disabled (set LANGSMITH_API_KEY to enable)")

    _client = client
    return _client


def _get_client() -> OpenAI:
    """Return the already-initialised client (raises if _init_client wasn't called)."""
    if _client is None:
        raise RuntimeError("_init_client() must be called before _get_client()")
    return _client


@traceable(run_type="llm", name="viz_agent.llm_call")
def llm_call(
    messages: list[dict[str, str]],
    temperature: float = LLM_DEFAULT_TEMPERATURE,
    max_tokens: int = LLM_DEFAULT_MAX_TOKENS,
    step_label: str = "uncategorised",
    task: "LLMTask | None" = None,
    job_id=None,  # optional job_id for per-job token tracking
) -> str:
    """Single LLM call with cost tracking, error handling, and optional LangSmith tracing.

    The step_label is used to attribute token usage to a stage (e.g. "step1_generate",
    "step2_build_fix:1") so the per-step summary at the end is meaningful.

    Stage 2: `task` (optional) — if set AND LLM_PROVIDER is openai, overrides the
    model via resolve_model(task). For LLM_PROVIDER=gemini, task is ignored (Gemini
    selects its model via MODEL_NAME env var).

    job_id is accepted for forward-compat but not yet used.
    """
    # Retryable HTTP status codes — transient server-side failures
    _RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
    _MAX_RETRIES = 5
    _BASE_DELAY  = 5.0   # seconds before first retry
    _MAX_DELAY   = 90.0  # cap on exponential backoff

    client = _get_client()
    last_exc: Exception | None = None

    # ── Resolve model name ─────────────────────────────────────────────────────
    # For OpenAI: if a task is declared, use resolve_model(task) so cheap calls
    # drop to gpt-4o-mini while VIZ_DRAFT keeps gpt-4o. Fall back to MODULE_NAME
    # (the global default) when task is None or provider is Gemini.
    if task is not None and LLM_PROVIDER == "openai":
        openai_model = resolve_model(task)
    else:
        openai_model = MODEL_NAME  # existing behaviour

    # ── Build kwargs based on model family ────────────────────────────
    # Reasoning models (gpt-5*, o1*, o3*, o4*) reject `temperature` and `max_tokens`
    # and require `max_completion_tokens` instead. They also accept `reasoning_effort`.
    create_kwargs: dict = {
        "model":    openai_model,
        "messages": messages,
    }
    if _is_reasoning_model(openai_model):
        create_kwargs["max_completion_tokens"] = max_tokens
        create_kwargs["reasoning_effort"] = REASONING_EFFORT
        # Note: temperature is intentionally OMITTED — reasoning models reject it.
    else:
        create_kwargs["temperature"] = temperature
        create_kwargs["max_tokens"]  = max_tokens

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            r = client.chat.completions.create(**create_kwargs)
            break  # success — exit retry loop

        except AuthenticationError:
            if LLM_PROVIDER == "openai":
                log.error("[ERROR] OpenAI API key rejected (401). Check OPENAI_API_KEY in .env.")
                log.error("  Get a key at https://platform.openai.com/api-keys")
            else:
                log.error("[ERROR] Gemini API key rejected (401). Check GEMINI_API_KEY in .env.")
                log.error("  Get a key at https://aistudio.google.com/apikey")
            sys.exit(2)  # auth errors are permanent — don't retry

        except RateLimitError as exc:
            # 429 — rate limit or quota. Retry with longer backoff.
            delay = min(_BASE_DELAY * (3 ** (attempt - 1)), _MAX_DELAY)
            log.warning("[Retry %d/%d] Rate limited (429) — waiting %.0fs before retry...",
                        attempt, _MAX_RETRIES, delay)
            time.sleep(delay)
            last_exc = exc
            continue

        except APIStatusError as exc:
            if exc.status_code in _RETRYABLE_STATUS:
                # 503 / 502 / 500 — transient server error — retry with backoff
                delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
                log.warning(
                    "[Retry %d/%d] API returned %d (%s) — waiting %.0fs before retry...",
                    attempt, _MAX_RETRIES, exc.status_code,
                    "service overloaded" if exc.status_code == 503 else "server error",
                    delay,
                )
                time.sleep(delay)
                last_exc = exc
                continue
            # Non-retryable status codes — fail immediately, but tailor the
            # message to the actual sub-cause (the `code` field in the error body).
            err_code, err_message = _extract_openai_error(exc)

            if exc.status_code == 402 or err_code == "insufficient_quota":
                log.error("[ERROR] %d — quota / billing issue.", exc.status_code)
                log.error("  Cause: %s", err_message or "insufficient_quota")
                if LLM_PROVIDER == "openai":
                    log.error("  Fix: add credits at https://platform.openai.com/settings/billing")
                else:
                    log.error("  Fix: check your usage at https://aistudio.google.com/apikey")

            elif exc.status_code == 403 and err_code == "model_not_found":
                # This is the case the user hit earlier with gpt-4o.
                log.error("[ERROR] 403 — your project has no access to model '%s'.", openai_model)
                log.error("  Cause: %s", err_message)
                log.error("  Fix options:")
                log.error("    1. Use a model your project does have access to:")
                log.error("       MODEL_NAME=gpt-4o-mini  (default-enabled on all paid accounts)")
                log.error("    2. Enable %s in your project:", openai_model)
                log.error("       https://platform.openai.com/settings/organization/projects")

            elif exc.status_code == 403 and err_code == "account_deactivated":
                log.error("[ERROR] 403 — your OpenAI account is deactivated.")
                log.error("  Cause: %s", err_message)
                log.error("  Fix: contact OpenAI support to reactivate the account.")

            elif exc.status_code == 403:
                # Generic 403 — DO NOT default to "no billing", that's misleading.
                log.error("[ERROR] 403 — permission denied.")
                if err_message:
                    log.error("  Cause: %s  (code=%s)", err_message, err_code)
                log.error("  This usually means one of:")
                log.error("    - The API key is correct but lacks the required scope")
                log.error("    - The project does not have access to the requested model/endpoint")
                log.error("    - Geographic / policy restriction on this endpoint")
                log.error("  It is NOT necessarily a billing issue.")

            elif exc.status_code == 404 or err_code == "model_not_found":
                log.error("[ERROR] %d — model '%s' not found.", exc.status_code, openai_model)
                if err_message:
                    log.error("  Detail: %s", err_message)
                if LLM_PROVIDER == "gemini":
                    log.error("  Try MODEL_NAME=gemini-1.5-flash in .env")
                else:
                    log.error("  Try MODEL_NAME=gpt-4o-mini in .env")

            else:
                log.error("[ERROR] API returned %d: %s", exc.status_code,
                          err_message or str(exc))
            sys.exit(2)

        except APITimeoutError as exc:
            # Client-side timeout — usually means the model is slower than the
            # configured client timeout (common with reasoning models on big
            # generations). Retrying without raising the timeout is futile.
            delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
            log.warning(
                "[Retry %d/%d] Client timeout after %.0fs — the model is taking "
                "longer than LLM_CLIENT_TIMEOUT. Raise it in .env if this keeps "
                "happening on '%s'. Waiting %.0fs before retry.",
                attempt, _MAX_RETRIES,
                float(os.getenv("LLM_CLIENT_TIMEOUT", "600")),
                openai_model, delay,
            )
            time.sleep(delay)
            last_exc = exc
            continue

        except APIConnectionError as exc:
            # True network error — retry with backoff
            delay = min(_BASE_DELAY * (2 ** (attempt - 1)), _MAX_DELAY)
            log.warning("[Retry %d/%d] Network error — waiting %.0fs: %s",
                        attempt, _MAX_RETRIES, delay, str(exc)[:120])
            time.sleep(delay)
            last_exc = exc
            continue

    else:
        # All retries exhausted
        log.error("[ERROR] All %d retries failed for step '%s'.", _MAX_RETRIES, step_label)
        if last_exc is not None:
            log.error("  Last error: %s", last_exc)
        log.error("  The %s API appears to be down or overloaded.", LLM_PROVIDER)
        log.error("  Wait a few minutes and re-run. If using Gemini free tier,")
        log.error("  consider switching: LLM_PROVIDER=openai in .env")
        sys.exit(2)

    if not r.choices:
        log.error("[ERROR] LLM returned an empty choices list.")
        log.error("  Full response: %s", r)
        sys.exit(2)

    choice = r.choices[0]
    finish_reason = choice.finish_reason or "unknown"
    raw_content = choice.message.content

    # ── Gemini 2.5 Flash "thinking" model fix ────────────────────────────────
    # Gemini 2.5 Flash is a reasoning model. When accessed via the OpenAI-
    # compatible endpoint it sometimes returns content=None and puts the actual
    # text in alternate fields. We try each fallback before treating it as a failure.
    if raw_content is None:
        msg = choice.message

        # Fallback 1: reasoning_content (Gemini thinking chain)
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning:
            log.debug("  [Gemini thinking] Using reasoning_content (%d chars)", len(reasoning))
            raw_content = reasoning

        # Fallback 2: parts attribute (Gemini native format)
        if raw_content is None:
            parts = getattr(msg, "parts", None)
            if parts:
                text_parts = [
                    getattr(p, "text", None) or (p if isinstance(p, str) else None)
                    for p in parts
                ]
                joined = "".join(t for t in text_parts if t)
                if joined:
                    log.debug("  [Gemini thinking] Assembled from parts (%d chars)", len(joined))
                    raw_content = joined

        # Fallback 3: model_dump introspection — last resort
        if raw_content is None:
            try:
                msg_dict = msg.model_dump() if hasattr(msg, "model_dump") else vars(msg)
                for key in ("text", "output", "response", "answer"):
                    val = msg_dict.get(key)
                    if isinstance(val, str) and val.strip():
                        log.debug("  [Gemini thinking] Found text in message field '%s'", key)
                        raw_content = val
                        break
            except Exception:
                pass

    # ── Diagnose truly None content ───────────────────────────────────────────
    if raw_content is None:
        log.error("[ERROR] LLM returned None content for step '%s'.", step_label)
        log.error("  Model         : %s", openai_model)
        log.error("  Provider      : %s", LLM_PROVIDER)
        log.error("  finish_reason : %s", finish_reason)
        log.error("  Message obj   : %s", choice.message)

        if finish_reason == "content_filter":
            log.error("  Cause: content filter blocked the response.")
            sys.exit(2)

        if getattr(choice.message, "tool_calls", None):
            log.error("  Cause: model returned a tool_call instead of text.")
            sys.exit(2)

        if LLM_PROVIDER == "gemini":
            log.error("")
            log.error("  Gemini 2.5 Flash is a thinking model — known fixes:")
            log.error("    1. Downgrade: MODEL_NAME=gemini-1.5-flash in .env")
            log.error("    2. Switch provider: LLM_PROVIDER=openai OPENAI_API_KEY=sk-...")
            log.error("    3. The thinking model may need a different API call path.")
        else:
            log.error("  Cause: model stopped without generating content.")
            log.error("  Fix  : try MODEL_NAME=gpt-4o or reduce prompt size.")
        sys.exit(2)

    # ── Warn on truncation ────────────────────────────────────────────────────
    if finish_reason == "length":
        log.warning("[WARN] Response truncated — model hit max_tokens limit (%d).", max_tokens)
        log.warning("  Output is incomplete (missing closing braces / files).")
        log.warning("  Fix: increase LLM_DEFAULT_MAX_TOKENS in backend/viz_generator/llm.py.")

    # ── Token tracking ────────────────────────────────────────────────────────
    if r.usage is not None:
        in_t  = r.usage.prompt_tokens or 0
        out_t = r.usage.completion_tokens or 0   # for reasoning models, includes reasoning tokens

        # Reasoning models expose details under completion_tokens_details.
        # Surface them so the user knows where their tokens are going.
        details = getattr(r.usage, "completion_tokens_details", None)
        reasoning_t = 0
        if details is not None:
            # Pydantic SDK object — tolerate both attr access and dict access
            reasoning_t = (
                getattr(details, "reasoning_tokens", None)
                or (details.get("reasoning_tokens") if isinstance(details, dict) else 0)
                or 0
            )

        token_tracker.record(
            step_label=step_label,
            input_tokens=in_t,
            output_tokens=out_t,
        )

        if reasoning_t and reasoning_t > 0:
            visible = max(out_t - reasoning_t, 0)
            log.info(
                "  [Reasoning] hidden=%d  visible=%d  ratio=%.0f%% reasoning  (model=%s, effort=%s)",
                reasoning_t, visible,
                (reasoning_t / out_t * 100) if out_t else 0,
                openai_model, REASONING_EFFORT,
            )
    else:
        log.debug("  [Tokens] usage info missing from response — not recorded")

    return raw_content.strip()
