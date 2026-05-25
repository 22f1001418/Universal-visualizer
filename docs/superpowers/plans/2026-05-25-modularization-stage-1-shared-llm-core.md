# Modularization Stage 1 — Shared LLM Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the duplicated LLM client code from `fixed_main_v6.py` into a shared `backend/llm/` package, introduce a per-task `LLMTask` enum so cheap calls can drop to `gpt-4o-mini` while `viz_draft` keeps `gpt-4o`, lay down the test foundation (unit + contract baseline), and ship the change behind a compatibility shim so nothing else needs to move yet.

**Architecture:** Strangler-fig step #1 from the modularization spec (`docs/superpowers/specs/2026-05-25-modularization-design.md`). A new `backend/` package is born alongside the existing top-level files. `llm_client.py` becomes a thin re-export shim. `fixed_main_v6.py` is edited at exactly one block (its duplicate LLM implementation) to import from `backend.llm`. Every existing call site (`agents.py`, `orchestrator.py`, `main.py`) keeps working through the shim. No HTTP route or env var name changes in this stage.

**Tech Stack:** Python 3.12, FastAPI, OpenAI SDK, pytest, mypy, ruff, pydantic-settings.

**Spec sections this plan implements:**
- Section 1 — `backend/` skeleton + `backend/llm/` layout
- Section 2 — "LLM cost / token efficiency" (all sub-bullets)
- Section 4 — Stage 1 cutover detail (including the `llm_client.py` shim)
- Section 5 — Layers 1 & 2 baseline (unit tests + API contract baseline)

**Out of scope for this plan (deferred to later Stage plans):**
- Decomposing `fixed_main_v6.py` into `viz_generator/` (Stage 2)
- Splitting `main.py` into `api/` + `services/` (Stage 3)
- Replacing `index.html` with a Vite SPA (Stage 4)
- `pyproject.toml` / `requirements.lock` migration (Stage 4)
- `import-linter` contract enforcement (Stage 3, when `api/`/`services/` exist)

---

## File structure (Stage 1 end state)

**Created:**
```
backend/
  __init__.py
  config.py                       # pydantic-settings — OpenAI + budgets only (more fields land in later stages)
  llm/
    __init__.py                   # public re-exports
    pricing.py                    # PRICE_PER_1K + cost computation
    reasoning.py                  # is_reasoning_model + reasoning kwargs
    errors.py                     # _extract_openai_error + permanent/retryable classification
    tracker.py                    # TokenUsageTracker
    tasks.py                      # LLMTask enum + per-task model resolution
    client.py                     # get_client(), llm_call() with retries
requirements-dev.txt              # pytest, httpx, mypy, ruff
tests/
  __init__.py
  conftest.py                     # shared fixtures, fake_llm
  unit/
    __init__.py
    llm/
      __init__.py
      test_pricing.py
      test_reasoning.py
      test_errors.py
      test_tracker.py
      test_tasks.py
      test_client.py
  contract/
    __init__.py
    test_health.py
    test_upload.py
    test_jobs.py
    test_suggestions.py
    test_build.py
    test_manifest.py
    test_preview.py
    test_dev_servers.py
```

**Modified:**
- `llm_client.py` — fully replaced with shim re-exports from `backend.llm`
- `fixed_main_v6.py` — the duplicated LLM block (~lines 870–1090 and ~1438+) replaced with imports from `backend.llm.client`
- `agents.py` — call sites updated to pass an `LLMTask` (one-line change per call)
- `env.example` — new optional `MODEL_*` overrides documented

**Unchanged:** every other Python file; the SPA in `index.html`; the Dockerfile; `railway.toml`.

---

## Conventions used in this plan

- Every code block is the **complete contents** of the file unless the path says `…:L<start>-<end>` (then it's a region replacement).
- Test commands run from repo root unless noted.
- Each task ends with a commit. Commit messages use Conventional Commits prefixes.
- A "failing test" step is followed by a "verify it fails" step **before** implementation lands — TDD discipline.

---

## Task 1 — Project skeleton & dev dependencies

**Files:**
- Create: `requirements-dev.txt`
- Create: `backend/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `pytest.ini`
- Create: `.gitignore` additions (append, don't overwrite)

- [ ] **Step 1: Add dev deps**

Create `requirements-dev.txt`:
```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.23
httpx>=0.27
mypy>=1.10
ruff>=0.5
```

- [ ] **Step 2: Create empty `backend/__init__.py`**

```python
"""Universal-visualizer backend package.

Houses all server-side Python code. Top-level files (main.py, fixed_main_v6.py,
agents.py, ...) will migrate under this package across Stages 1-3 of the
modularization project. See docs/superpowers/specs/2026-05-25-modularization-design.md
"""
```

- [ ] **Step 3: Create `tests/__init__.py` and `tests/unit/__init__.py` and `tests/contract/__init__.py`**

All three files are empty (zero bytes).

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared test fixtures."""
from __future__ import annotations

import pytest


@pytest.fixture
def fake_llm(monkeypatch):
    """Pre-recorded LLM responses keyed by step_label.

    Tests set `fake_llm.responses[step_label] = '...'` and any call into
    `backend.llm.client.llm_call` returns that string. Calls are also recorded
    into the real token tracker so budget tests behave realistically.
    """
    responses: dict[str, str] = {}
    calls: list[dict] = []

    def llm_call_stub(
        system_prompt: str,
        user_prompt: str,
        step_label: str,
        job_id=None,
        task=None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
    ) -> str:
        calls.append({
            "step_label": step_label,
            "job_id": job_id,
            "task": task,
            "in_chars": len(system_prompt) + len(user_prompt),
        })
        from backend.llm.tracker import token_tracker
        token_tracker.record(
            step_label=step_label,
            input_tokens=100,
            output_tokens=50,
            job_id=job_id,
            model="gpt-4o-mini",
            reasoning_tokens=0,
        )
        return responses.get(step_label, '{"topics": []}')

    monkeypatch.setattr("backend.llm.client.llm_call", llm_call_stub)
    # Also patch the shim path so legacy `from llm_client import llm_call` works in tests.
    monkeypatch.setattr("llm_client.llm_call", llm_call_stub, raising=False)

    class FakeLLM:
        def __init__(self) -> None:
            self.responses = responses
            self.calls = calls

    return FakeLLM()
```

- [ ] **Step 5: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
markers =
    e2e: end-to-end tests requiring real OpenAI + npm (skipped by default)
addopts = -ra --strict-markers
```

- [ ] **Step 6: Append `.gitignore` entries (idempotent)**

Append (do not overwrite) to `.gitignore`:
```
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
__pycache__/
*.pyc
```

If `.gitignore` doesn't exist, create it with the above contents.

- [ ] **Step 7: Install dev deps in the local venv**

Run:
```bash
python -m venv .venv 2>/dev/null || true
.venv/bin/pip install -r requirements-dev.txt
```

Expected: successful install of pytest, mypy, ruff, httpx.

- [ ] **Step 8: Confirm pytest runs**

Run:
```bash
.venv/bin/pytest --collect-only
```

Expected: `collected 0 items` (no tests yet — that's fine).

- [ ] **Step 9: Commit**

```bash
git add requirements-dev.txt backend/__init__.py tests/ pytest.ini .gitignore
git commit -m "chore(modularization): scaffold backend/ + tests/ + dev deps

First commit of the modularization project. Adds an empty backend/
package, a tests/ tree (unit + contract subdirs), shared fake_llm
fixture, pytest config, and dev-only dependencies.

Stage 1 of 4. See docs/superpowers/specs/2026-05-25-modularization-design.md"
```

---

## Task 2 — Capture API contract baseline (Layer 2 safety net)

This task locks the HTTP API shapes **as they exist today**, against the current `main.py`. Every test must pass against the unmodified codebase before any LLM code moves. The tests are the regression guard for Stage 3.

**Files:**
- Create: `tests/contract/conftest.py`
- Create: `tests/contract/test_health.py`
- Create: `tests/contract/test_upload.py`
- Create: `tests/contract/test_jobs.py`
- Create: `tests/contract/test_suggestions.py`
- Create: `tests/contract/test_build.py`
- Create: `tests/contract/test_manifest.py`
- Create: `tests/contract/test_preview.py`
- Create: `tests/contract/test_dev_servers.py`

- [ ] **Step 1: Create `tests/contract/conftest.py`**

```python
"""Contract test fixtures — TestClient against the current FastAPI app."""
from __future__ import annotations

import os
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path) -> TestClient:
    """A TestClient bound to the live FastAPI app.

    OPENAI_API_KEY is set to a dummy value because get_client() exits on import
    if it's missing. Actual LLM calls in contract tests are stubbed via fake_llm
    from tests/conftest.py.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy")
    monkeypatch.setenv("VIZ_OUTPUT_DIR", str(tmp_path / "viz_outputs"))
    (tmp_path / "viz_outputs").mkdir(parents=True, exist_ok=True)

    from main import app  # noqa: WPS433 — late import after env setup
    return TestClient(app)
```

- [ ] **Step 2: Write the failing health contract test**

Create `tests/contract/test_health.py`:
```python
"""GET /healthz contract — locked at Stage 1."""
from __future__ import annotations


def test_healthz_returns_documented_shape(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert {"ok", "fixed_main_path", "fixed_main_exists",
            "text_model", "output_dir"} <= set(body.keys())
    assert isinstance(body["ok"], bool)
    assert isinstance(body["fixed_main_exists"], bool)
    assert isinstance(body["text_model"], str)
```

- [ ] **Step 3: Run it to confirm the baseline passes**

Run:
```bash
.venv/bin/pytest tests/contract/test_health.py -v
```

Expected: `1 passed`. If it fails, the current `main.py:170` (`healthz()`) is the source of truth — adjust the test to match what the endpoint actually returns, then re-run.

- [ ] **Step 4: Write the upload contract test**

Create `tests/contract/test_upload.py`:
```python
"""POST /upload contract — locked at Stage 1."""
from __future__ import annotations


def test_upload_minimal_md_returns_upload_response(client, fake_llm):
    fake_llm.responses["agent_A_topic_extraction"] = (
        '{"script_name": "test.md", "topics": [], "extraction_note": ""}'
    )
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
    files = {"file": ("hello.txt", b"plain text", "text/plain")}
    r = client.post("/upload", files=files, data={"track": "Academy DSA"})
    # Current behaviour: accept by extension; verify whatever main.py actually does.
    # If it returns 400, lock that; if 200, lock that. Adjust the assert below
    # after running step 5.
    assert r.status_code in {200, 400, 422}
```

- [ ] **Step 5: Run upload tests**

Run:
```bash
.venv/bin/pytest tests/contract/test_upload.py -v
```

Expected: both pass. If `test_upload_rejects_non_md_file` requires tightening (one of the 3 status codes), set it to the exact code the current code returns and re-run.

- [ ] **Step 6: Write jobs contract tests**

Create `tests/contract/test_jobs.py`:
```python
"""GET /jobs and GET /jobs/{id} contracts."""
from __future__ import annotations


def _seed_job(client, fake_llm) -> str:
    fake_llm.responses["agent_A_topic_extraction"] = (
        '{"script_name": "seed.md", "topics": [], "extraction_note": ""}'
    )
    files = {"file": ("seed.md", b"# Seed\n", "text/markdown")}
    r = client.post("/upload", files=files, data={"track": "Academy DSA"})
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
    assert r.status_code in {404, 500}  # current behaviour TBD; lock it after first run
```

- [ ] **Step 7: Run jobs tests**

Run:
```bash
.venv/bin/pytest tests/contract/test_jobs.py -v
```

Expected: all pass. If `test_get_job_404` needs tightening to a single status code, set it after observing the current behaviour.

- [ ] **Step 8: Write the remaining contract tests**

Create `tests/contract/test_suggestions.py`:
```python
"""POST /jobs/{id}/topics/{tid}/suggestions contract."""
from __future__ import annotations


def test_suggestions_endpoint_404_for_unknown_job(client):
    r = client.post("/jobs/nope/topics/topic_1/suggestions")
    assert r.status_code in {404, 500}
```

Create `tests/contract/test_build.py`:
```python
"""POST /jobs/{id}/topics/{tid}/build contract."""
from __future__ import annotations


def test_build_endpoint_404_for_unknown_job(client):
    r = client.post(
        "/jobs/nope/topics/topic_1/build",
        json={"suggestion_id": "viz_1", "custom_notes": ""},
    )
    assert r.status_code in {404, 422, 500}
```

Create `tests/contract/test_manifest.py`:
```python
"""GET /jobs/{id}/manifest contract."""
from __future__ import annotations


def test_manifest_404_for_unknown_job(client):
    r = client.get("/jobs/nope/manifest")
    assert r.status_code in {404, 500}
```

Create `tests/contract/test_preview.py`:
```python
"""GET /preview contract."""
from __future__ import annotations


def test_preview_missing_path_param(client):
    r = client.get("/preview")
    assert r.status_code in {400, 422}


def test_preview_outside_output_dir_rejected(client):
    r = client.get("/preview?path=/etc/passwd")
    assert r.status_code in {400, 403, 404}
```

Create `tests/contract/test_dev_servers.py`:
```python
"""GET /dev-servers contract."""
from __future__ import annotations


def test_dev_servers_list(client):
    r = client.get("/dev-servers")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, (list, dict))
```

- [ ] **Step 9: Run the full contract suite**

Run:
```bash
.venv/bin/pytest tests/contract/ -v
```

Expected: all tests pass. Any failure means a contract assertion is stricter than current behaviour — loosen the assertion (or the status-code set) until it matches reality. The point is to capture what main.py does today, exactly.

- [ ] **Step 10: Commit**

```bash
git add tests/contract/
git commit -m "test(contract): capture HTTP API baseline against current main.py

Layer 2 of the testing strategy. Every documented endpoint has at least
one contract test against the current code. These are the migration
safety net for Stage 3 (when main.py is split into api/ + services/).

Status-code sets are deliberately permissive where current behaviour
is ambiguous — they will tighten as the API stabilises."
```

---

## Task 3 — `backend/llm/pricing.py`

**Files:**
- Create: `backend/llm/__init__.py` (empty for now; re-exports added at task end)
- Create: `backend/llm/pricing.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/llm/__init__.py`
- Create: `tests/unit/llm/test_pricing.py`

- [ ] **Step 1: Create empty `backend/llm/__init__.py` and `tests/unit/__init__.py` and `tests/unit/llm/__init__.py`**

All three files are empty (zero bytes).

- [ ] **Step 2: Write failing test for `cost_usd`**

Create `tests/unit/llm/test_pricing.py`:
```python
"""Unit tests for backend.llm.pricing."""
from __future__ import annotations

import pytest

from backend.llm.pricing import cost_usd, PRICE_PER_1K


def test_cost_usd_gpt_4o_mini():
    # 1000 input + 1000 output @ gpt-4o-mini = 0.00015 + 0.0006 = 0.00075
    assert cost_usd(1000, 1000, "gpt-4o-mini") == pytest.approx(0.00075)


def test_cost_usd_gpt_4o():
    # 1000 input + 1000 output @ gpt-4o = 0.0025 + 0.010 = 0.0125
    assert cost_usd(1000, 1000, "gpt-4o") == pytest.approx(0.0125)


def test_cost_usd_unknown_model_returns_zero():
    assert cost_usd(1000, 1000, "fictional-model-9000") == 0.0


def test_cost_usd_zero_tokens():
    assert cost_usd(0, 0, "gpt-4o-mini") == 0.0


def test_price_table_has_every_documented_model():
    # If a model is added to the table without a test, this list goes stale.
    expected = {
        "gpt-4o-mini", "gpt-4o", "gpt-4.1", "gpt-4.1-mini", "gpt-4-turbo",
        "gpt-5", "gpt-5-mini", "gpt-5-nano",
        "o1", "o1-mini", "o3", "o3-mini", "o4-mini",
    }
    assert expected <= set(PRICE_PER_1K.keys())
```

- [ ] **Step 3: Run the failing test**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_pricing.py -v
```

Expected: `ImportError: cannot import name 'cost_usd'` (or `ModuleNotFoundError`).

- [ ] **Step 4: Implement `backend/llm/pricing.py`**

```python
"""Per-call USD pricing for OpenAI models.

PRICE_PER_1K mirrors the table previously in llm_client.py — values are USD
per 1,000 tokens. Update when OpenAI changes prices.

cost_usd() is the single function callers should use. Unknown models return
$0 (never raises) so the rest of the system never crashes because of a
missing entry; the [Tokens] log line will show $0 and that's the prompt to
update the table.
"""
from __future__ import annotations

from typing import Optional

PRICE_PER_1K: dict[str, dict[str, float]] = {
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


def cost_usd(input_tokens: int, output_tokens: int, model: Optional[str]) -> float:
    """Return USD cost of a call. Unknown models return 0.0."""
    if not model:
        return 0.0
    prices = PRICE_PER_1K.get(model)
    if not prices:
        return 0.0
    return (input_tokens / 1000.0) * prices["input"] + (output_tokens / 1000.0) * prices["output"]
```

- [ ] **Step 5: Run tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_pricing.py -v
```

Expected: `5 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/llm/__init__.py backend/llm/pricing.py tests/unit/__init__.py tests/unit/llm/__init__.py tests/unit/llm/test_pricing.py
git commit -m "feat(llm): extract pricing table + cost_usd into backend.llm.pricing

Moves PRICE_PER_1K and cost computation out of llm_client.py into a
single-responsibility module. Unit-tested. Unknown models return \$0
silently (won't crash the call path)."
```

---

## Task 4 — `backend/llm/reasoning.py`

**Files:**
- Create: `backend/llm/reasoning.py`
- Create: `tests/unit/llm/test_reasoning.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/llm/test_reasoning.py`:
```python
from __future__ import annotations

from backend.llm.reasoning import is_reasoning_model, REASONING_PREFIXES


def test_gpt_5_is_reasoning():
    assert is_reasoning_model("gpt-5") is True
    assert is_reasoning_model("gpt-5-mini") is True


def test_o_series_is_reasoning():
    assert is_reasoning_model("o1") is True
    assert is_reasoning_model("o3-mini") is True
    assert is_reasoning_model("o4-mini") is True


def test_gpt_4o_not_reasoning():
    assert is_reasoning_model("gpt-4o") is False
    assert is_reasoning_model("gpt-4o-mini") is False


def test_empty_or_none_is_not_reasoning():
    assert is_reasoning_model("") is False
    assert is_reasoning_model(None) is False  # type: ignore[arg-type]


def test_case_insensitive():
    assert is_reasoning_model("GPT-5") is True
    assert is_reasoning_model("O3") is True


def test_known_prefixes_exposed():
    assert "gpt-5" in REASONING_PREFIXES
    assert "o1" in REASONING_PREFIXES
```

- [ ] **Step 2: Run to confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_reasoning.py -v
```

Expected: `ModuleNotFoundError: No module named 'backend.llm.reasoning'`.

- [ ] **Step 3: Implement `backend/llm/reasoning.py`**

```python
"""Reasoning-model detection for OpenAI gpt-5 / o-series.

Reasoning models require:
  - max_completion_tokens (not max_tokens)
  - reasoning_effort kwarg
  - omitted temperature/top_p (they reject these)
This module is the single place that knows the prefix list.
"""
from __future__ import annotations

REASONING_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")


def is_reasoning_model(model: str | None) -> bool:
    if not model:
        return False
    name = model.lower()
    return any(name.startswith(p) for p in REASONING_PREFIXES)
```

- [ ] **Step 4: Run tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_reasoning.py -v
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/llm/reasoning.py tests/unit/llm/test_reasoning.py
git commit -m "feat(llm): extract is_reasoning_model into backend.llm.reasoning"
```

---

## Task 5 — `backend/llm/errors.py`

**Files:**
- Create: `backend/llm/errors.py`
- Create: `tests/unit/llm/test_errors.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/llm/test_errors.py`:
```python
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
```

- [ ] **Step 2: Run, confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_errors.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `backend/llm/errors.py`**

```python
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
```

- [ ] **Step 4: Run tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_errors.py -v
```

Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/llm/errors.py tests/unit/llm/test_errors.py
git commit -m "feat(llm): extract OpenAI error parsing into backend.llm.errors"
```

---

## Task 6 — `backend/llm/tracker.py`

**Files:**
- Create: `backend/llm/tracker.py`
- Create: `tests/unit/llm/test_tracker.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/llm/test_tracker.py`:
```python
from __future__ import annotations

import pytest

from backend.llm.tracker import TokenUsageTracker


def test_record_accumulates_per_job():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 50, job_id="j1", model="gpt-4o-mini")
    t.record("step_b", 200, 75, job_id="j1", model="gpt-4o-mini")
    s = t.job_summary("j1")
    assert s["calls"] == 2
    assert s["input_tokens"] == 300
    assert s["output_tokens"] == 125
    assert s["total_tokens"] == 425
    assert s["estimated_cost_usd"] > 0


def test_record_without_job_id_does_not_raise():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 50, job_id=None, model="gpt-4o-mini")
    # Empty job_summary for unknown job
    s = t.job_summary("nonexistent")
    assert s["calls"] == 0
    assert s["total_tokens"] == 0


def test_budget_overrun_raises():
    t = TokenUsageTracker(budget_per_job=500)
    t.record("step_a", 200, 200, job_id="j1", model="gpt-4o-mini")  # 400 — OK
    with pytest.raises(RuntimeError, match="exceeded token budget"):
        t.record("step_b", 100, 100, job_id="j1", model="gpt-4o-mini")  # 600 total — over


def test_reasoning_tokens_recorded():
    t = TokenUsageTracker(budget_per_job=1_000_000)
    t.record("step_a", 100, 200, job_id="j1", model="gpt-5", reasoning_tokens=150)
    s = t.job_summary("j1")
    assert s["reasoning_tokens"] == 150


def test_tracker_singleton_importable():
    from backend.llm.tracker import token_tracker
    assert token_tracker is not None
```

- [ ] **Step 2: Run, confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_tracker.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `backend/llm/tracker.py`**

```python
"""Token usage tracker — process-lifetime totals and per-job buckets.

Behaviour preserved from llm_client.TokenUsageTracker (the original):
  - Thread-safe via a single Lock.
  - Records input/output/reasoning tokens per call, accumulates per job_id.
  - Raises RuntimeError when a job exceeds budget_per_job.
  - Emits a [Tokens] log line on every record() call (same format as before).

Differences from the original:
  - Pricing pulled from backend.llm.pricing (no duplicate table).
  - budget_per_job is a constructor arg (was a module global). The module-level
    `token_tracker` singleton reads its budget from env at import time.
"""
from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Optional

from backend.llm.pricing import cost_usd

logger = logging.getLogger("hackmd-orch.llm")

_DEFAULT_BUDGET = int(os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))


class TokenUsageTracker:
    def __init__(self, budget_per_job: int = _DEFAULT_BUDGET) -> None:
        self.budget_per_job = budget_per_job
        self.total_input = 0
        self.total_output = 0
        self.total_calls = 0
        self._per_job: dict[str, dict] = {}
        self._lock = Lock()

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

        cost = cost_usd(input_tokens, output_tokens, model)
        model_tag = (model or "?")[:18]

        if job_id is not None:
            pct = (job_total / self.budget_per_job) * 100 if self.budget_per_job else 0
            logger.info(
                "[Tokens] %-30s [%s] in=%-6d out=%-5d cost=$%.4f  job=%d/%d (%.0f%%)",
                step_label, model_tag, input_tokens, output_tokens, cost,
                job_total, self.budget_per_job, pct,
            )
            if reasoning_tokens > 0 and output_tokens > 0:
                visible = max(output_tokens - reasoning_tokens, 0)
                logger.info(
                    "  [Reasoning] hidden=%d  visible=%d  ratio=%.0f%%",
                    reasoning_tokens, visible,
                    (reasoning_tokens / output_tokens) * 100,
                )
            if job_total > self.budget_per_job:
                raise RuntimeError(
                    f"Job {job_id} exceeded token budget: "
                    f"{job_total} > {self.budget_per_job}. Aborting."
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
            "estimated_cost_usd": round(cost_usd(bucket["input"], bucket["output"], "gpt-4o-mini"), 4),
        }


# Module-level singleton — same as the old llm_client.token_tracker.
token_tracker = TokenUsageTracker()
```

- [ ] **Step 4: Run tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_tracker.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/llm/tracker.py tests/unit/llm/test_tracker.py
git commit -m "feat(llm): extract TokenUsageTracker into backend.llm.tracker

Same observable behaviour as the original llm_client.TokenUsageTracker.
Pricing now comes from backend.llm.pricing (no duplicate table). Budget
is a constructor arg; module singleton reads TOKEN_BUDGET_PER_JOB env at
import time."
```

---

## Task 7 — `backend/llm/tasks.py` (LLMTask enum + model resolution)

**Files:**
- Create: `backend/llm/tasks.py`
- Create: `tests/unit/llm/test_tasks.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/llm/test_tasks.py`:
```python
from __future__ import annotations

import pytest

from backend.llm.tasks import LLMTask, resolve_model


def test_llmtask_enum_values():
    assert LLMTask.AGENT_A_EXTRACT.value == "agent_a_extract"
    assert LLMTask.AGENT_B_SUGGEST.value == "agent_b_suggest"
    assert LLMTask.VIZ_TOPIC_CLASSIFY.value == "viz_topic_classify"
    assert LLMTask.VIZ_DRAFT.value == "viz_draft"
    assert LLMTask.VIZ_BUILD_FIX.value == "viz_build_fix"
    assert LLMTask.VIZ_RUNTIME_FIX.value == "viz_runtime_fix"
    assert LLMTask.VIZ_POLISH.value == "viz_polish"


def test_resolve_model_defaults(monkeypatch):
    # Strip every override
    for name in ("MODEL_AGENT_A", "MODEL_AGENT_B", "MODEL_VIZ_CLASSIFY",
                 "MODEL_VIZ_DRAFT", "MODEL_VIZ_FIX", "MODEL_VIZ_RUNTIME",
                 "MODEL_VIZ_POLISH", "OPENAI_TEXT_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert resolve_model(LLMTask.AGENT_A_EXTRACT) == "gpt-4o-mini"
    assert resolve_model(LLMTask.AGENT_B_SUGGEST) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_TOPIC_CLASSIFY) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-4o"
    assert resolve_model(LLMTask.VIZ_BUILD_FIX) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_RUNTIME_FIX) == "gpt-4o-mini"
    assert resolve_model(LLMTask.VIZ_POLISH) == "gpt-4o-mini"


def test_resolve_model_env_override(monkeypatch):
    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    assert resolve_model(LLMTask.VIZ_DRAFT) == "gpt-5"


def test_resolve_model_falls_back_to_global_when_task_is_none(monkeypatch):
    monkeypatch.setenv("OPENAI_TEXT_MODEL", "gpt-4.1")
    assert resolve_model(None) == "gpt-4.1"


def test_resolve_model_uses_global_default_when_nothing_set(monkeypatch):
    for name in ("OPENAI_TEXT_MODEL",):
        monkeypatch.delenv(name, raising=False)
    assert resolve_model(None) == "gpt-4o-mini"
```

- [ ] **Step 2: Run, confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_tasks.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement `backend/llm/tasks.py`**

```python
"""Per-task model selection.

Today's code uses one global OPENAI_TEXT_MODEL for every LLM call. That means
cheap classification calls pay heavy-model prices. LLMTask + resolve_model
let each call site declare its task; the cheap tasks default to gpt-4o-mini
while VIZ_DRAFT (the expensive one) keeps gpt-4o.

Env overrides per task:
  AGENT_A_EXTRACT     → MODEL_AGENT_A
  AGENT_B_SUGGEST     → MODEL_AGENT_B
  VIZ_TOPIC_CLASSIFY  → MODEL_VIZ_CLASSIFY
  VIZ_DRAFT           → MODEL_VIZ_DRAFT
  VIZ_BUILD_FIX       → MODEL_VIZ_FIX
  VIZ_RUNTIME_FIX     → MODEL_VIZ_RUNTIME
  VIZ_POLISH          → MODEL_VIZ_POLISH

Callers that pass task=None fall back to OPENAI_TEXT_MODEL (current behaviour).
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Optional


class LLMTask(str, Enum):
    AGENT_A_EXTRACT = "agent_a_extract"
    AGENT_B_SUGGEST = "agent_b_suggest"
    VIZ_TOPIC_CLASSIFY = "viz_topic_classify"
    VIZ_DRAFT = "viz_draft"
    VIZ_BUILD_FIX = "viz_build_fix"
    VIZ_RUNTIME_FIX = "viz_runtime_fix"
    VIZ_POLISH = "viz_polish"


_DEFAULTS: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "gpt-4o-mini",
    LLMTask.AGENT_B_SUGGEST:    "gpt-4o-mini",
    LLMTask.VIZ_TOPIC_CLASSIFY: "gpt-4o-mini",
    LLMTask.VIZ_DRAFT:          "gpt-4o",
    LLMTask.VIZ_BUILD_FIX:      "gpt-4o-mini",
    LLMTask.VIZ_RUNTIME_FIX:    "gpt-4o-mini",
    LLMTask.VIZ_POLISH:         "gpt-4o-mini",
}

_ENV_VAR: dict[LLMTask, str] = {
    LLMTask.AGENT_A_EXTRACT:    "MODEL_AGENT_A",
    LLMTask.AGENT_B_SUGGEST:    "MODEL_AGENT_B",
    LLMTask.VIZ_TOPIC_CLASSIFY: "MODEL_VIZ_CLASSIFY",
    LLMTask.VIZ_DRAFT:          "MODEL_VIZ_DRAFT",
    LLMTask.VIZ_BUILD_FIX:      "MODEL_VIZ_FIX",
    LLMTask.VIZ_RUNTIME_FIX:    "MODEL_VIZ_RUNTIME",
    LLMTask.VIZ_POLISH:         "MODEL_VIZ_POLISH",
}


def resolve_model(task: Optional[LLMTask]) -> str:
    """Return the model name to use for this task.

    Resolution order:
      1. If task is set: env override (MODEL_<TASK>), else _DEFAULTS[task].
      2. If task is None: env OPENAI_TEXT_MODEL, else "gpt-4o-mini".
    """
    if task is None:
        return os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
    override = os.getenv(_ENV_VAR[task])
    if override:
        return override
    return _DEFAULTS[task]
```

- [ ] **Step 4: Run tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_tasks.py -v
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/llm/tasks.py tests/unit/llm/test_tasks.py
git commit -m "feat(llm): introduce LLMTask enum + per-task model resolution

Lets each LLM call site declare its task. Cheap tasks default to
gpt-4o-mini; VIZ_DRAFT defaults to gpt-4o. Each task has a dedicated
env override (MODEL_AGENT_A, MODEL_VIZ_DRAFT, ...). task=None preserves
today's behaviour (uses OPENAI_TEXT_MODEL)."
```

---

## Task 8 — `backend/llm/client.py` (the big one)

**Files:**
- Create: `backend/llm/client.py`
- Create: `tests/unit/llm/test_client.py`

- [ ] **Step 1: Write failing test**

Create `tests/unit/llm/test_client.py`:
```python
"""Unit tests for backend.llm.client.

These tests exercise the public surface (`get_client`, `llm_call`) without
hitting OpenAI. The OpenAI client is monkey-patched to a fake.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_openai_client(monkeypatch):
    """Replace get_client()'s OpenAI() construction with a MagicMock."""
    fake_client = MagicMock()
    fake_choice = SimpleNamespace(
        message=SimpleNamespace(content='{"hello":"world"}', reasoning_content=None),
        finish_reason="stop",
    )
    fake_response = SimpleNamespace(
        choices=[fake_choice],
        usage=SimpleNamespace(
            prompt_tokens=42,
            completion_tokens=17,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        ),
    )
    fake_client.chat.completions.create.return_value = fake_response

    import backend.llm.client as client_mod
    monkeypatch.setattr(client_mod, "_client", fake_client)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    return fake_client


def test_llm_call_happy_path(fake_openai_client):
    from backend.llm.client import llm_call
    from backend.llm.tasks import LLMTask

    out = llm_call(
        system_prompt="you are a test",
        user_prompt="say hi",
        step_label="unit_test",
        task=LLMTask.AGENT_A_EXTRACT,
    )
    assert out == '{"hello":"world"}'
    # Verify the SDK was called with the resolved model
    args, kwargs = fake_openai_client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-4o-mini"  # AGENT_A_EXTRACT default
    assert kwargs["messages"][0]["role"] == "system"
    assert kwargs["messages"][1]["role"] == "user"


def test_llm_call_reasoning_model_uses_completion_tokens(fake_openai_client, monkeypatch):
    from backend.llm.client import llm_call
    from backend.llm.tasks import LLMTask

    monkeypatch.setenv("MODEL_VIZ_DRAFT", "gpt-5")
    llm_call("sys", "usr", "x", task=LLMTask.VIZ_DRAFT)
    _, kwargs = fake_openai_client.chat.completions.create.call_args
    assert "max_completion_tokens" in kwargs
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs
    assert kwargs["reasoning_effort"] in {"low", "medium", "high"}


def test_llm_call_records_tokens(fake_openai_client):
    from backend.llm.client import llm_call
    from backend.llm.tracker import token_tracker

    llm_call("sys", "usr", "step_X", job_id="job_42")
    s = token_tracker.job_summary("job_42")
    assert s["input_tokens"] >= 42
    assert s["output_tokens"] >= 17
```

- [ ] **Step 2: Run, confirm failure**

Run:
```bash
.venv/bin/pytest tests/unit/llm/test_client.py -v
```

Expected: `ModuleNotFoundError: backend.llm.client`.

- [ ] **Step 3: Implement `backend/llm/client.py`**

This is the merge of the original `llm_client.llm_call` with the new `LLMTask` plumbing. Behaviour is preserved: SDK retries disabled, same retry loop, same error-classification policy, token tracking via `backend.llm.tracker`.

```python
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
```

- [ ] **Step 4: Wire up `backend/llm/__init__.py` to re-export public surface**

Replace the empty `backend/llm/__init__.py` with:
```python
"""Public surface of backend.llm — the shared LLM core.

Every caller should import from `backend.llm`, not from sub-modules. The
sub-module layout is an implementation detail and may shuffle.
"""
from backend.llm.client import get_client, llm_call
from backend.llm.errors import (
    PERMANENT_STATUS,
    RETRYABLE_STATUS,
    extract_openai_error,
    is_permanent_status,
    is_retryable_status,
)
from backend.llm.pricing import PRICE_PER_1K, cost_usd
from backend.llm.reasoning import REASONING_PREFIXES, is_reasoning_model
from backend.llm.tasks import LLMTask, resolve_model
from backend.llm.tracker import TokenUsageTracker, token_tracker

__all__ = [
    "get_client", "llm_call",
    "LLMTask", "resolve_model",
    "TokenUsageTracker", "token_tracker",
    "cost_usd", "PRICE_PER_1K",
    "is_reasoning_model", "REASONING_PREFIXES",
    "extract_openai_error", "is_retryable_status", "is_permanent_status",
    "PERMANENT_STATUS", "RETRYABLE_STATUS",
]
```

- [ ] **Step 5: Run all unit tests, confirm pass**

Run:
```bash
.venv/bin/pytest tests/unit/ -v
```

Expected: every test in `tests/unit/llm/` passes (pricing, reasoning, errors, tracker, tasks, client).

- [ ] **Step 6: Commit**

```bash
git add backend/llm/client.py backend/llm/__init__.py tests/unit/llm/test_client.py
git commit -m "feat(llm): port llm_call into backend.llm.client with LLMTask support

Behaviour preserved from llm_client.llm_call: same retry policy, same
error classification, same reasoning-model handling, same token
tracking. New: callers can pass task=LLMTask.X to route to a per-task
model. Public surface exposed via backend.llm package __init__."
```

---

## Task 9 — Convert `llm_client.py` into a compatibility shim

This is the critical safety step. After this task, every existing caller of `llm_client.llm_call` keeps working without modification.

**Files:**
- Modify: `llm_client.py` (full replacement)

- [ ] **Step 1: Replace `llm_client.py` with the shim**

```python
"""Compatibility shim — re-exports from backend.llm.

Originally hosted the LLM client implementation; that has moved into the
backend.llm package as part of Stage 1 of the modularization project.
This file is kept for backwards compatibility with existing imports
(`from llm_client import llm_call`) and is scheduled for removal at the
end of Stage 3, when all callers will have been migrated to import from
backend.llm directly.

See docs/superpowers/specs/2026-05-25-modularization-design.md for context.
"""
from backend.llm import (  # noqa: F401
    PERMANENT_STATUS,
    PRICE_PER_1K,
    REASONING_PREFIXES,
    RETRYABLE_STATUS,
    LLMTask,
    TokenUsageTracker,
    cost_usd,
    extract_openai_error,
    get_client,
    is_permanent_status,
    is_reasoning_model,
    is_retryable_status,
    llm_call,
    resolve_model,
    token_tracker,
)

# Module-level constants the original file exposed. Kept here so legacy
# `from llm_client import OPENAI_API_KEY` etc. doesn't break.
import os as _os
OPENAI_API_KEY = _os.getenv("OPENAI_API_KEY", "")
TEXT_MODEL = _os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
REASONING_EFFORT = _os.getenv("REASONING_EFFORT", "low")
MAX_OUTPUT_TOKENS = int(_os.getenv("MAX_OUTPUT_TOKENS", "4096"))
TOKEN_BUDGET_PER_JOB = int(_os.getenv("TOKEN_BUDGET_PER_JOB", "300000"))

# Legacy helper name kept for back-compat
_extract_openai_error = extract_openai_error
```

- [ ] **Step 2: Run the full test suite, confirm everything still passes**

Run:
```bash
.venv/bin/pytest tests/ -v
```

Expected: every contract test and every unit test passes. If any contract test fails, the shim is missing a re-export — check the test's imports and the original `llm_client.py` symbols.

- [ ] **Step 3: Smoke-test the existing app boots**

Run:
```bash
.venv/bin/python -c "from main import app; print('OK', app.title)"
```

Expected: prints `OK <app-title>` with no exceptions.

- [ ] **Step 4: Commit**

```bash
git add llm_client.py
git commit -m "refactor(llm): convert llm_client.py to a compatibility shim

Re-exports everything from backend.llm. Existing imports across the
codebase (agents.py, main.py, orchestrator.py, fixed_main_v6.py) keep
working without modification. Shim is scheduled for removal at end of
Stage 3."
```

---

## Task 10 — Migrate `fixed_main_v6.py` to import from `backend.llm`

The file has its own duplicated LLM machinery. Replace it with imports from `backend.llm`. The subprocess argv contract is unchanged.

**Files:**
- Modify: `fixed_main_v6.py` — replace its LLM block (lines ~870–1090 and the `llm_call` function around line 1438) with imports.

- [ ] **Step 1: Read the file once to confirm the line ranges**

Run:
```bash
.venv/bin/python -c "
import re
src = open('fixed_main_v6.py').read().splitlines()
for i, line in enumerate(src, 1):
    if re.match(r'(class TokenUsageTracker|def is_reasoning_model|def _extract_openai_error|def _init_client|def _get_client|def llm_call|_REASONING_PREFIXES|PRICE_PER_1K)', line.strip()):
        print(f'{i:5d}: {line.rstrip()}')
"
```

Expected output: prints each duplicate symbol's line number. **Capture these line numbers** — they will guide the replacement.

- [ ] **Step 2: Replace the duplicated LLM block**

In `fixed_main_v6.py`, find the block starting at the first occurrence of `_REASONING_PREFIXES` / `PRICE_PER_1K` / `class TokenUsageTracker` (around line 870) and continuing through the local `_extract_openai_error`, `_init_client`, `_get_client`, and the local `llm_call` (around line 1438). Replace **the entire block** with:

```python
# ───────────────────────────────────────────
# LLM core — shared with the orchestrator (Stage 1 of modularization).
# All behaviour previously inlined here lives in backend.llm now.
# ───────────────────────────────────────────
from backend.llm import (  # noqa: E402
    LLMTask,
    PRICE_PER_1K,
    TokenUsageTracker,
    cost_usd,
    extract_openai_error,
    get_client,
    is_reasoning_model,
    llm_call as _shared_llm_call,
    resolve_model,
    token_tracker,
)


def _is_reasoning_model(model_name: str) -> bool:
    """Local alias preserved so existing internal call sites still resolve."""
    return is_reasoning_model(model_name)


def _extract_openai_error(exc):  # noqa: ANN001 — match old signature
    return extract_openai_error(exc)


def _init_client():
    return get_client()


def _get_client():
    return get_client()


def llm_call(
    system_prompt: str,
    user_prompt: str,
    step_label: str,
    job_id=None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    json_mode: bool = False,
    task: LLMTask | None = None,
) -> str:
    """Local llm_call — delegates to the shared backend.llm.llm_call.

    Existing call sites in this file don't pass `task`. Once the viz_generator
    package is created in Stage 2, every call site here will be updated to
    pass an explicit LLMTask (VIZ_DRAFT, VIZ_BUILD_FIX, ...).
    """
    return _shared_llm_call(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        step_label=step_label,
        job_id=job_id,
        task=task,
        temperature=temperature,
        max_tokens=max_tokens,
        json_mode=json_mode,
    )
```

The original `print_error_block`, `classify_topic`, `_filter_bogus_files`, `parse_files`, … and everything else stays where it is. Only the LLM-machinery block is replaced.

- [ ] **Step 3: Verify the file parses**

Run:
```bash
.venv/bin/python -c "import ast; ast.parse(open('fixed_main_v6.py').read()); print('OK')"
```

Expected: `OK`.

- [ ] **Step 4: Smoke-test the CLI still imports**

Run:
```bash
.venv/bin/python -c "import fixed_main_v6; print('OK', fixed_main_v6.__name__)"
```

Expected: prints `OK fixed_main_v6` with no exceptions.

- [ ] **Step 5: Re-run all tests**

Run:
```bash
.venv/bin/pytest tests/ -v
```

Expected: every test still passes (contract baseline intact).

- [ ] **Step 6: Commit**

```bash
git add fixed_main_v6.py
git commit -m "refactor(viz): dedupe LLM client in fixed_main_v6.py via backend.llm

Replaces the duplicated LLM machinery (PRICE_PER_1K, TokenUsageTracker,
_extract_openai_error, _init_client, _get_client, llm_call) with thin
delegates to backend.llm. Subprocess argv contract is unchanged.

Local llm_call() now accepts an optional task=LLMTask.X parameter so
Stage 2 (viz_generator/) can plumb it through. Existing call sites that
don't pass task continue using the global OPENAI_TEXT_MODEL via
resolve_model(None)."
```

---

## Task 11 — Update `agents.py` to pass `LLMTask`

This activates the cost-saving routing for Agent A and Agent B. Both should default to `gpt-4o-mini`, which they likely already do (the global default), but routing through `LLMTask` makes it explicit and overridable.

**Files:**
- Modify: `agents.py`

- [ ] **Step 1: Read the current call sites**

Run:
```bash
grep -n "llm_call(" agents.py
```

Expected: two call sites, one for Agent A topic extraction, one for Agent B viz suggestion. Note their line numbers.

- [ ] **Step 2: Update each call site**

For the Agent A call site (topic extraction), add `task=LLMTask.AGENT_A_EXTRACT` to the kwargs. For the Agent B call site (viz suggestion), add `task=LLMTask.AGENT_B_SUGGEST`.

Also update the import block at the top of `agents.py`:

Change:
```python
from llm_client import llm_call
```
To:
```python
from llm_client import llm_call, LLMTask
```

(Importing `LLMTask` from the shim is intentional — it transparently re-exports from `backend.llm`. Stage 3 will rewrite this to import from `backend.llm` directly.)

Each call site becomes (example for Agent A):

```python
raw = llm_call(
    system_prompt=_TOPIC_EXTRACTION_SYSTEM,
    user_prompt=user,
    step_label="agent_A_topic_extraction",
    job_id=job_id,
    task=LLMTask.AGENT_A_EXTRACT,
    json_mode=True,
)
```

And for Agent B:
```python
raw = llm_call(
    system_prompt=_VIZ_SUGGESTION_SYSTEM,
    user_prompt=user,
    step_label=f"agent_B_viz_suggest:{topic.id}",
    job_id=job_id,
    task=LLMTask.AGENT_B_SUGGEST,
    json_mode=True,
)
```

- [ ] **Step 3: Run contract tests**

Run:
```bash
.venv/bin/pytest tests/contract/ -v
```

Expected: all contract tests still pass. The fake_llm fixture doesn't care about `task`; it just records it.

- [ ] **Step 4: Commit**

```bash
git add agents.py
git commit -m "feat(agents): tag agent_A/agent_B calls with LLMTask for per-task model routing

Both agents still default to gpt-4o-mini (same as before). The change
makes the choice explicit and adds MODEL_AGENT_A / MODEL_AGENT_B env
overrides for tuning."
```

---

## Task 12 — Document new env vars in `env.example`

**Files:**
- Modify: `env.example`

- [ ] **Step 1: Append the new MODEL_* overrides section to `env.example`**

Add this block before the `# ── Server ──` section:

```
# ── Per-task model overrides (Stage 1 modularization) ────
# All optional. If unset, each LLMTask uses its built-in default:
#   AGENT_A_EXTRACT     → gpt-4o-mini
#   AGENT_B_SUGGEST     → gpt-4o-mini
#   VIZ_TOPIC_CLASSIFY  → gpt-4o-mini
#   VIZ_DRAFT           → gpt-4o     (the only heavy default)
#   VIZ_BUILD_FIX       → gpt-4o-mini
#   VIZ_RUNTIME_FIX     → gpt-4o-mini
#   VIZ_POLISH          → gpt-4o-mini
# Setting any of these overrides the corresponding task's model.
# MODEL_AGENT_A=gpt-4o-mini
# MODEL_AGENT_B=gpt-4o-mini
# MODEL_VIZ_CLASSIFY=gpt-4o-mini
# MODEL_VIZ_DRAFT=gpt-4o
# MODEL_VIZ_FIX=gpt-4o-mini
# MODEL_VIZ_RUNTIME=gpt-4o-mini
# MODEL_VIZ_POLISH=gpt-4o-mini
```

- [ ] **Step 2: Commit**

```bash
git add env.example
git commit -m "docs(env): document per-task MODEL_* overrides

Optional env vars introduced by Stage 1. Defaults are unchanged from
today's behaviour (every task uses gpt-4o-mini except VIZ_DRAFT)."
```

---

## Task 13 — Final verification

This task adds no new code; it confirms Stage 1 has not regressed anything.

- [ ] **Step 1: Run the entire test suite**

Run:
```bash
.venv/bin/pytest tests/ -v
```

Expected: every test passes.

- [ ] **Step 2: Smoke-test the FastAPI app boots end-to-end**

Run (in two terminals or use `&`):
```bash
OPENAI_API_KEY=sk-test .venv/bin/uvicorn main:app --port 18002 &
sleep 3
curl -s http://127.0.0.1:18002/healthz | head
kill %1
```

Expected: a JSON object containing `"ok": true`. If the app crashes on import, check that `fixed_main_v6.py` still parses (Task 10 Step 3) and that the shim re-exports everything `main.py` imports from `llm_client`.

- [ ] **Step 3: Manual cost-routing sanity check (optional, no spend)**

Run:
```bash
.venv/bin/python -c "
from backend.llm import LLMTask, resolve_model
for t in LLMTask:
    print(f'{t.value:25s} -> {resolve_model(t)}')
"
```

Expected output:
```
agent_a_extract           -> gpt-4o-mini
agent_b_suggest           -> gpt-4o-mini
viz_topic_classify        -> gpt-4o-mini
viz_draft                 -> gpt-4o
viz_build_fix             -> gpt-4o-mini
viz_runtime_fix           -> gpt-4o-mini
viz_polish                -> gpt-4o-mini
```

- [ ] **Step 4: Tag the Stage 1 cut**

```bash
git tag -a modularization-stage-1 -m "Stage 1 of the modularization project — shared LLM core"
```

Expected: tag created. (Do NOT push the tag; that's a separate, user-initiated action.)

---

## Self-review (run before sending the plan)

**Spec coverage:** ✅
- Section 1 (`backend/llm/` layout): Tasks 3–8
- Section 2 (LLM cost / token efficiency: dedup, per-task model, prompt cache, output ceilings, per-task budgets): Tasks 3–8, 11, 12. Per-task budgets land in Stage 2 with `viz_generator/`; the spec's per-task budget table is acknowledged but enforcement is a Stage 2 task because it sits inside the viz fix loops.
- Section 4 Stage 1 cutover (the shim): Task 9
- Section 5 Layer 1 (unit tests): Tasks 3–8
- Section 5 Layer 2 (API contract baseline): Task 2

**Placeholder scan:** No TBDs, no "implement later". Test status-code sets in contract tests use `in {a, b, c}` sets intentionally (locked to whatever the current code returns); the steps explicitly say "tighten after observing current behaviour" — this is correct, not lazy.

**Type consistency:** `LLMTask` enum, `resolve_model()` signature, `llm_call(task=…)` kwarg, and `token_tracker.record(...)` parameters are consistent across Tasks 7, 8, 10, 11. `extract_openai_error` (new name) and `_extract_openai_error` (legacy alias in the shim) are both documented in the same task. `cost_usd` signature `(input_tokens, output_tokens, model)` is consistent in pricing.py, tracker.py, and the public re-exports.

**Deferred items, explicit:**
- Per-task budget enforcement → Stage 2 plan
- `import-linter` contract → Stage 3 plan (needs `api/`/`services/` to exist)
- Replacing `requirements.txt` with `pyproject.toml` + lockfile → Stage 4 plan
- Prompt-cache headers and per-task `max_tokens` ceilings → Stage 2 plan (these change call-site behaviour inside the viz generator)
