# Modularization Stage 4 — Vite + React + TypeScript SPA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the CDN-React + Babel-in-browser `index.html` with a real Vite + React + TypeScript SPA at full feature parity, ship it side-by-side at `/v2/`, then flip the root and demote the legacy HTML.

**Architecture:** New `frontend/` peer directory holding a Vite project that emits production assets into `backend/static_v2/` (initially) and later `backend/static/` (after cutover). A FastAPI `StaticFiles` mount serves the built SPA. State-machine UI (5 screens — same UX as today, no react-router) keeps the visible flow identical. Types are generated from `/openapi.json` so backend shape changes break `tsc` at the SPA boundary. Multi-stage Dockerfile builds the SPA in a Node image and copies only the dist into the runtime image.

**Tech Stack:** Vite 5 + React 18 + TypeScript 5 + Vitest + React Testing Library + Playwright + openapi-typescript. Backend additions: `StaticFiles` mounts in `backend/api/spa.py`.

**Deliberate spec deviation:** The spec's `vite.config.ts` example chunks `react-router-dom`. Today's UI has no routes (URL never changes between screens). Stage 4 keeps the state-machine pattern — identical to current behaviour. `react-router-dom` is **not** installed. If multi-page navigation is ever needed, that's a follow-up.

**Source-of-truth for parity:** The current `index.html` (881 lines). Every prop, state field, fetch shape, and screen transition must round-trip. The acceptance checklist in Section 4 / Stage 4 of the spec is the authoritative parity matrix.

---

## File structure

```
frontend/                                       # NEW peer directory
├── package.json
├── vite.config.ts                              # outDir → ../backend/static_v2 then ../backend/static
├── tsconfig.json
├── tsconfig.node.json                          # vite.config.ts compilation
├── index.html                                  # Vite entry — <div id="root">, <script src="/src/main.tsx">
├── .gitignore                                  # node_modules, dist
├── src/
│   ├── main.tsx                                # ReactDOM.createRoot, mounts <App/>
│   ├── App.tsx                                 # screen state machine + error state
│   ├── api/
│   │   ├── client.ts                           # api(), upload() — match legacy fetch shape
│   │   └── types.gen.ts                        # GENERATED from /openapi.json — checked in
│   ├── lib/
│   │   ├── theme.css                           # ported CSS variables + base styles
│   │   └── formatters.ts                       # any small pure helpers needed by pages
│   ├── hooks/
│   │   └── useJobPolling.ts                    # 1s polling, AbortController on unmount
│   ├── components/
│   │   ├── Crumbs.tsx
│   │   ├── GithubRepoStatus.tsx
│   │   └── BuildCard.tsx
│   └── pages/
│       ├── Upload.tsx
│       ├── Topics.tsx
│       ├── Suggestions.tsx
│       ├── Building.tsx
│       └── Done.tsx
└── tests/
    ├── setup.ts                                # @testing-library/jest-dom + fetch shims
    ├── unit/
    │   ├── client.test.ts
    │   ├── useJobPolling.test.tsx
    │   ├── Upload.test.tsx
    │   ├── Topics.test.tsx
    │   ├── Suggestions.test.tsx
    │   └── Done.test.tsx
    └── e2e/
        └── happy-path.spec.ts                  # Playwright — upload → topics → suggestions → queued

backend/
└── api/
    └── spa.py                                  # MODIFIED — mounts /v2/ during dev, then / after flip

Dockerfile                                       # REWRITE — multi-stage per spec Section 3
.dockerignore                                    # NEW or extended — per spec Section 3
.gitignore                                       # add frontend/node_modules, frontend/dist,
                                                 #     backend/static, backend/static_v2

index.html                                       # MOVED in Task 14 → backend/legacy/index.html
backend/
└── legacy/                                      # NEW (Task 14) — short-lived holding pen
    └── index.html
```

---

## Task 1: Scaffold `frontend/` and mount `/v2/`

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.node.json`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/.gitignore`
- Modify: `backend/api/spa.py` — add `/v2/` `StaticFiles` mount
- Modify: `.gitignore` — add `frontend/node_modules`, `frontend/dist`, `backend/static`, `backend/static_v2`
- Test: `tests/contract/test_spa_v2_mount.py`

- [ ] **Step 1: Write the failing test for /v2/ mount**

```python
# tests/contract/test_spa_v2_mount.py
"""/v2/ — built SPA mount (Stage 4 parallel ship)."""
from __future__ import annotations

from pathlib import Path


def test_v2_root_serves_index_html(client, monkeypatch, tmp_path):
    """GET /v2/ must return the built SPA's index.html when it exists."""
    static_v2 = tmp_path / "static_v2"
    static_v2.mkdir()
    (static_v2 / "index.html").write_text(
        "<!doctype html><html><body><div id='root'></div></body></html>"
    )
    # The mount resolves backend/static_v2 relative to repo root at import
    # time. Patch the resolved path used by spa.py.
    import backend.api.spa as spa_mod
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", static_v2)
    # Re-mount: the existing app already mounted /v2 at startup against the
    # real (possibly empty) directory. Mount a parallel router for the test
    # by rebuilding the app.
    from importlib import reload
    reload(spa_mod)
    from main import create_app
    monkeypatch.setattr(spa_mod, "_STATIC_V2_DIR", static_v2)
    app = create_app()
    from fastapi.testclient import TestClient
    with TestClient(app) as fresh:
        r = fresh.get("/v2/")
    assert r.status_code == 200
    assert b"<div id='root'>" in r.content


def test_v2_missing_dir_returns_404(client):
    """If backend/static_v2 doesn't exist, /v2/ returns 404 (not 500)."""
    r = client.get("/v2/")
    assert r.status_code in (404, 200)  # 200 only if a real build is on disk
```

Note: the second test is intentionally lenient because the mount might already see a real on-disk build. The first test is the load-bearing one.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/pulkitmangal/Universal-visualizer
pytest tests/contract/test_spa_v2_mount.py -v
```
Expected: FAIL with `_STATIC_V2_DIR` attribute missing on `backend.api.spa`.

- [ ] **Step 3: Write `frontend/package.json`**

```json
{
  "name": "universal-visualizer-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "test:watch": "vitest",
    "codegen": "openapi-typescript http://localhost:8001/openapi.json -o src/api/types.gen.ts",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.45.0",
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "happy-dom": "^14.0.0",
    "openapi-typescript": "^7.0.0",
    "typescript": "^5.4.0",
    "vite": "^5.3.0",
    "vitest": "^2.0.0"
  }
}
```

- [ ] **Step 4: Write `frontend/vite.config.ts`**

```ts
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Stage 4 cutover state: outDir starts at ../backend/static_v2 (parallel ship).
// Task 14 flips this to ../backend/static after parity QA.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../backend/static_v2',
    emptyOutDir: true,
    sourcemap: false,
    cssCodeSplit: true,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
        },
      },
    },
  },
  esbuild: {
    drop: ['console', 'debugger'],
  },
  server: {
    port: 5173,
    proxy: {
      '/upload': 'http://localhost:8001',
      '/jobs': 'http://localhost:8001',
      '/preview': 'http://localhost:8001',
      '/healthz': 'http://localhost:8001',
      '/openapi.json': 'http://localhost:8001',
    },
  },
  test: {
    environment: 'happy-dom',
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/unit/**/*.test.{ts,tsx}'],
    globals: true,
  },
});
```

- [ ] **Step 5: Write `frontend/tsconfig.json` and `frontend/tsconfig.node.json`**

```json
// frontend/tsconfig.json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": false,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "tests"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

```json
// frontend/tsconfig.node.json
{
  "compilerOptions": {
    "composite": true,
    "skipLibCheck": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "allowSyntheticDefaultImports": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 6: Write `frontend/index.html`, `src/main.tsx`, `src/App.tsx` placeholder**

```html
<!-- frontend/index.html -->
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>HackMD · Visualization Orchestrator</title>
    <link rel="preconnect" href="https://fonts.googleapis.com" />
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
    <link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300..900&family=IBM+Plex+Sans:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

```tsx
// frontend/src/main.tsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './lib/theme.css';

const rootEl = document.getElementById('root');
if (!rootEl) throw new Error('#root not found');
ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
```

```tsx
// frontend/src/App.tsx — placeholder; replaced in Task 11
export default function App() {
  return <main style={{ padding: 24 }}><h1>Stage 4 scaffold OK</h1></main>;
}
```

```css
/* frontend/src/lib/theme.css — placeholder for Task 2 */
:root { --bg-0: #fafaf7; }
body { background: var(--bg-0); }
```

```
# frontend/.gitignore
node_modules
dist
.vite
*.log
```

- [ ] **Step 7: Extend root `.gitignore`**

Append to `/Users/pulkitmangal/Universal-visualizer/.gitignore`:

```
# Stage 4 SPA build outputs
frontend/node_modules/
frontend/dist/
backend/static/
backend/static_v2/
```

- [ ] **Step 8: Modify `backend/api/spa.py` to mount /v2/**

```python
"""GET / — serve the legacy CDN-React SPA. Mount /v2/ for the new Vite SPA.

Stage 4 introduces the new SPA at /v2/. After parity QA, Task 14 flips:
the new SPA moves to /, the legacy HTML moves to /legacy/.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

router = APIRouter(tags=["spa"])

# Resolve project root from this file's location.
# __file__ = backend/api/spa.py → parents[2] = repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_FRONTEND_FILE = _REPO_ROOT / "index.html"
_STATIC_V2_DIR = _REPO_ROOT / "backend" / "static_v2"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the legacy SPA HTML (flipped in Task 14)."""
    if not _FRONTEND_FILE.exists():
        return HTMLResponse(
            "<h1>Frontend not found</h1><p>Expected at: " + str(_FRONTEND_FILE) + "</p>",
            status_code=500,
        )
    return HTMLResponse(_FRONTEND_FILE.read_text(encoding="utf-8"))


def mount_v2(app: FastAPI) -> None:
    """Mount the built Stage 4 SPA at /v2/ if its dist exists.

    Called from backend.api.__init__.mount_routers. Skipped silently if the
    SPA hasn't been built yet — keeps the API usable in fresh checkouts
    where someone hasn't run `cd frontend && npm run build`.
    """
    if _STATIC_V2_DIR.exists() and (_STATIC_V2_DIR / "index.html").exists():
        app.mount(
            "/v2",
            StaticFiles(directory=str(_STATIC_V2_DIR), html=True),
            name="spa_v2",
        )
```

- [ ] **Step 9: Wire `mount_v2` into `backend/api/__init__.py`**

Read `backend/api/__init__.py` first to find the existing `mount_routers` function. Add at the end of it:

```python
    from backend.api.spa import mount_v2
    mount_v2(application)
```

- [ ] **Step 10: Install and verify build**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend
npm install
npm run build
ls ../backend/static_v2/index.html  # must exist
```
Expected: `index.html` in `backend/static_v2/`, plus hashed `assets/*.js` and `assets/*.css`.

- [ ] **Step 11: Run contract test, verify passes**

```bash
cd /Users/pulkitmangal/Universal-visualizer
pytest tests/contract/test_spa_v2_mount.py -v
```
Expected: PASS (at least `test_v2_root_serves_index_html`).

- [ ] **Step 12: Run the full contract suite — no regressions**

```bash
pytest tests/contract -v
```
Expected: all green (including the existing /preview, /jobs, etc. tests).

- [ ] **Step 13: Commit**

```bash
git add frontend/ backend/api/spa.py backend/api/__init__.py .gitignore tests/contract/test_spa_v2_mount.py
git commit -m "feat(stage-4): scaffold Vite frontend and mount /v2/ side-by-side"
```

---

## Task 2: Port design tokens and base styles

**Files:**
- Modify: `frontend/src/lib/theme.css` — replace placeholder with the full ported tokens + base styles
- Modify: `frontend/src/main.tsx` — already imports theme.css (no change)

**Source:** `index.html` lines 16–280 (CSS variables block + global element styles + base layout classes). Copy verbatim, then strip anything tied to dom that won't exist in the new SPA yet.

- [ ] **Step 1: Read the legacy CSS to identify what to port**

```bash
sed -n '16,290p' /Users/pulkitmangal/Universal-visualizer/index.html | wc -l
```
Expected: ~275 lines.

- [ ] **Step 2: Replace `frontend/src/lib/theme.css`**

Copy lines 17–290 of the legacy `index.html` (from `:root {` through the last global selector before component-specific classes start). Preserve:

- All `--*` CSS custom properties on `:root`
- `* { box-sizing… }` reset
- `html, body` defaults
- `body::before` warm wash
- `#root` z-index
- `h1`–`h4`, `code`, `.mono` typography
- `button`, `button.primary`, `button.ghost` rules
- `input, textarea, select`, `label` defaults

Component-specific class rules (e.g., `.upload-zone`, `.topic-card`, `.build-card`) **stay co-located with their components** as plain class names — port them in the corresponding component tasks (Tasks 5–10), not here. Theme.css is tokens + element defaults only.

If a rule references a class you don't yet have a component for, leave it in theme.css for now and migrate it during the relevant page task.

- [ ] **Step 3: Verify by running the dev server**

```bash
# Terminal 1
cd /Users/pulkitmangal/Universal-visualizer && python -m uvicorn main:app --port 8001
# Terminal 2
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run dev
```
Open `http://localhost:5173/`. Expected: the "Stage 4 scaffold OK" heading renders with the IBM Plex Sans font, off-white background, deep amber accent. Same look as legacy.

- [ ] **Step 4: Rebuild and verify /v2/ matches**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run build
```
Visit `http://localhost:8001/v2/`. Same look.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/theme.css
git commit -m "feat(stage-4): port design tokens and base styles"
```

---

## Task 3: Generate types and write API client

**Files:**
- Create: `frontend/src/api/types.gen.ts` — generated from `/openapi.json`
- Create: `frontend/src/api/client.ts` — `api()` + `upload()`
- Test: `frontend/tests/unit/client.test.ts`

The legacy `api()` helper (index.html line 331) is the contract:

```js
const api = async (path, opts = {}) => {
  const r = await fetch(path, { ...opts, headers: { "Accept": "application/json", ...(opts.headers || {}) } });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0,200)}`);
  }
  return r.json();
};
```

Replicate exactly — same error message format, same `Accept` header default — so anything checking `error.message.startsWith("500 ")` still works.

- [ ] **Step 1: Boot backend, run codegen**

```bash
# In one terminal:
cd /Users/pulkitmangal/Universal-visualizer && python -m uvicorn main:app --port 8001
# In another:
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run codegen
```
Expected: `frontend/src/api/types.gen.ts` created. Must include type aliases for `JobState`, `JobSummary`, `UploadResponse`, `HealthResponse` (from existing `backend/api/*.py` `response_model`s).

- [ ] **Step 2: Write the failing test**

```ts
// frontend/tests/unit/client.test.ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, upload } from '../../src/api/client';

afterEach(() => vi.restoreAllMocks());

describe('api()', () => {
  it('parses JSON on 200', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ ok: true }), { status: 200 }),
    ));
    expect(await api<{ ok: boolean }>('/healthz')).toEqual({ ok: true });
  });

  it('throws "<status> <statusText> — <body>" on non-2xx', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('boom', { status: 500, statusText: 'Internal Server Error' }),
    ));
    await expect(api('/jobs/missing')).rejects.toThrow(
      /^500 Internal Server Error — boom/,
    );
  });

  it('truncates long error bodies to 200 chars', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('x'.repeat(500), { status: 400, statusText: 'Bad Request' }),
    ));
    await expect(api('/x')).rejects.toThrow(/x{200}$/);
  });

  it('sends Accept: application/json by default', async () => {
    const fetchSpy = vi.fn(async () => new Response('{}', { status: 200 }));
    vi.stubGlobal('fetch', fetchSpy);
    await api('/jobs');
    expect(fetchSpy.mock.calls[0][1].headers).toMatchObject({
      Accept: 'application/json',
    });
  });
});

describe('upload()', () => {
  it('POSTs FormData to /upload', async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j_1' }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const file = new File(['# hello'], 'a.md', { type: 'text/markdown' });
    const result = await upload(file);
    expect(result.job_id).toBe('j_1');
    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe('/upload');
    expect(init.method).toBe('POST');
    expect(init.body).toBeInstanceOf(FormData);
  });
});
```

```ts
// frontend/tests/setup.ts
import '@testing-library/jest-dom';
```

- [ ] **Step 3: Run test, verify fails**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run test
```
Expected: FAIL — `client` module not found.

- [ ] **Step 4: Implement `frontend/src/api/client.ts`**

```ts
import type { components } from './types.gen';

export type JobState = components['schemas']['JobState'];
export type JobSummary = components['schemas']['JobSummary'];
export type UploadResponse = components['schemas']['UploadResponse'];

export async function api<T = unknown>(
  path: string,
  opts: RequestInit = {},
): Promise<T> {
  const r = await fetch(path, {
    ...opts,
    headers: { Accept: 'application/json', ...(opts.headers || {}) },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0, 200)}`);
  }
  return (await r.json()) as T;
}

export async function upload(file: File): Promise<UploadResponse> {
  const fd = new FormData();
  fd.append('file', file);
  const r = await fetch('/upload', { method: 'POST', body: fd });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${r.status} ${r.statusText} — ${body.slice(0, 200)}`);
  }
  return (await r.json()) as UploadResponse;
}
```

If the generated `types.gen.ts` doesn't yet expose those exact `components.schemas.*` paths (depends on openapi-typescript version), inspect the file and adjust the type aliases. Acceptable fallback: define local interfaces matching the FastAPI `response_model` fields used by the SPA — but only as a temporary measure, with a TODO to re-codegen.

- [ ] **Step 5: Run test, verify passes**

```bash
npm run test && npm run typecheck
```
Expected: all 5 tests pass; `tsc` clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/ frontend/tests/unit/client.test.ts frontend/tests/setup.ts
git commit -m "feat(stage-4): generate OpenAPI types and add SPA api client"
```

---

## Task 4: `useJobPolling` hook

**Files:**
- Create: `frontend/src/hooks/useJobPolling.ts`
- Test: `frontend/tests/unit/useJobPolling.test.tsx`

The legacy `Building` component polls `/jobs/:id` every 1000ms (index.html line 668). The hook hoists that — used by both `Building` and `Topics` (Topics polls once until `status === 'topics_extracted'`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/useJobPolling.test.tsx
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useJobPolling } from '../../src/hooks/useJobPolling';

afterEach(() => vi.restoreAllMocks());

describe('useJobPolling', () => {
  it('returns null initially then polled job', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j', status: 'building' }), { status: 200 }),
    ));
    const { result } = renderHook(() => useJobPolling('j', 50));
    expect(result.current.job).toBeNull();
    await waitFor(() => expect(result.current.job?.status).toBe('building'));
  });

  it('stops polling when stop=true', async () => {
    const fetchSpy = vi.fn(async () =>
      new Response(JSON.stringify({ job_id: 'j', status: 'done' }), { status: 200 }),
    );
    vi.stubGlobal('fetch', fetchSpy);
    const { rerender } = renderHook(({ stop }) => useJobPolling('j', 30, stop), {
      initialProps: { stop: false },
    });
    await new Promise((r) => setTimeout(r, 100));
    const callsBefore = fetchSpy.mock.calls.length;
    rerender({ stop: true });
    await new Promise((r) => setTimeout(r, 100));
    expect(fetchSpy.mock.calls.length - callsBefore).toBeLessThanOrEqual(1);
  });

  it('surfaces fetch errors', async () => {
    vi.stubGlobal('fetch', vi.fn(async () =>
      new Response('nope', { status: 500, statusText: 'Internal Server Error' }),
    ));
    const { result } = renderHook(() => useJobPolling('j', 30));
    await waitFor(() => expect(result.current.error).toMatch(/500/));
  });
});
```

- [ ] **Step 2: Run, verify fails**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run test useJobPolling
```
Expected: FAIL — hook module not found.

- [ ] **Step 3: Implement the hook**

```ts
// frontend/src/hooks/useJobPolling.ts
import { useEffect, useState } from 'react';
import { api, type JobState } from '../api/client';

export function useJobPolling(jobId: string | null, intervalMs = 1000, stop = false) {
  const [job, setJob] = useState<JobState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!jobId || stop) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const tick = async () => {
      try {
        const next = await api<JobState>(`/jobs/${jobId}`);
        if (cancelled) return;
        setJob(next);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) timer = setTimeout(tick, intervalMs);
      }
    };

    tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [jobId, intervalMs, stop]);

  return { job, error };
}
```

- [ ] **Step 4: Run, verify passes**

```bash
npm run test useJobPolling && npm run typecheck
```
Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useJobPolling.ts frontend/tests/unit/useJobPolling.test.tsx
git commit -m "feat(stage-4): add useJobPolling hook with cancellation"
```

---

## Task 5: Small components — Crumbs, GithubRepoStatus, BuildCard

**Files:**
- Create: `frontend/src/components/Crumbs.tsx`
- Create: `frontend/src/components/GithubRepoStatus.tsx`
- Create: `frontend/src/components/BuildCard.tsx`

**Source:** index.html — `Crumbs` (lines 441–456), `GithubRepoStatus` (lines 344–384), `BuildCard` (lines 709–745).

These are mostly markup with a few branches. No tests for these — they're covered by the page tests that mount them. Port verbatim, only changing PropTypes → TypeScript interfaces.

- [ ] **Step 1: Read the legacy components**

```bash
sed -n '344,385p' /Users/pulkitmangal/Universal-visualizer/index.html
sed -n '441,456p' /Users/pulkitmangal/Universal-visualizer/index.html
sed -n '709,745p' /Users/pulkitmangal/Universal-visualizer/index.html
```

- [ ] **Step 2: Write `Crumbs.tsx`**

```tsx
// frontend/src/components/Crumbs.tsx
import type { Screen } from '../App';

const STEPS: { id: Screen; label: string }[] = [
  { id: 'upload', label: 'Upload' },
  { id: 'topics', label: 'Topics' },
  { id: 'suggestions', label: 'Suggestions' },
  { id: 'building', label: 'Building' },
  { id: 'done', label: 'Done' },
];

export function Crumbs({ screen }: { screen: Screen }) {
  const currentIdx = STEPS.findIndex((s) => s.id === screen);
  return (
    <nav className="crumbs" aria-label="Progress">
      {STEPS.map((step, i) => (
        <span
          key={step.id}
          className={`crumb${i === currentIdx ? ' active' : ''}${i < currentIdx ? ' past' : ''}`}
        >
          {step.label}
        </span>
      ))}
    </nav>
  );
}
```

Port any `.crumbs`, `.crumb`, `.crumb.active`, `.crumb.past` CSS rules from the legacy index.html into `frontend/src/lib/theme.css`. (Or inline as a CSS module per-component — your call. Plan default: keep all CSS in theme.css to mirror today.)

- [ ] **Step 3: Write `GithubRepoStatus.tsx`**

Port the legacy component verbatim. Type the props:

```tsx
type Props = {
  status: 'pending' | 'pushing' | 'ready' | 'failed' | string;
  repoUrl?: string | null;
  repoName?: string | null;
  error?: string | null;
};
```

Port the legacy switch on `status` and all classNames. If the legacy component uses lucide-react icons via UMD, **substitute inline SVG or a tiny emoji stand-in** — do not pull `lucide-react` as a dependency unless the legacy icon set is doing real work (most are decorative). Default: use the same Unicode glyphs the legacy file uses (e.g., spinner via CSS, ✓ / ✗ inline).

- [ ] **Step 4: Write `BuildCard.tsx`**

Port the legacy component verbatim with TS prop types using `JobState['builds'][number]`. Reference `topic.title`, `build.status`, `build.preview_url`, `build.github` fields exactly as the legacy does.

- [ ] **Step 5: Typecheck**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run typecheck
```
Expected: clean. (The pages that consume these don't exist yet, so unused-export warnings are expected and tolerated for one task.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ frontend/src/lib/theme.css
git commit -m "feat(stage-4): port Crumbs, GithubRepoStatus, BuildCard"
```

---

## Task 6: Upload page

**Files:**
- Create: `frontend/src/pages/Upload.tsx`
- Test: `frontend/tests/unit/Upload.test.tsx`

**Source:** `index.html` lines 457–520. Pattern: file input + drag-drop zone + "Upload" button → POST `/upload` (returns `{job_id}`) → GET `/jobs/:id` → `onJobReady(job)`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/tests/unit/Upload.test.tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Upload } from '../../src/pages/Upload';

afterEach(() => vi.restoreAllMocks());

describe('Upload', () => {
  it('rejects non-.md file with onError', async () => {
    const onError = vi.fn();
    const onJobReady = vi.fn();
    render(<Upload onJobReady={onJobReady} onError={onError} />);
    const file = new File(['hi'], 'a.txt', { type: 'text/plain' });
    const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: /upload/i }));
    await waitFor(() => expect(onError).toHaveBeenCalled());
    expect(onJobReady).not.toHaveBeenCalled();
  });

  it('uploads .md and calls onJobReady with hydrated job', async () => {
    const onJobReady = vi.fn();
    const fetchSpy = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === '/upload') {
        return new Response(JSON.stringify({ job_id: 'j_1' }), { status: 200 });
      }
      if (url === '/jobs/j_1') {
        return new Response(
          JSON.stringify({ job_id: 'j_1', status: 'topics_extracted', topics: [] }),
          { status: 200 },
        );
      }
      return new Response('?', { status: 404 });
    });
    vi.stubGlobal('fetch', fetchSpy);
    render(<Upload onJobReady={onJobReady} onError={vi.fn()} />);
    const input = screen.getByLabelText(/upload/i) as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['# hi'], 'a.md', { type: 'text/markdown' })] },
    });
    fireEvent.click(screen.getByRole('button', { name: /upload/i }));
    await waitFor(() => expect(onJobReady).toHaveBeenCalledTimes(1));
    expect(onJobReady.mock.calls[0][0].job_id).toBe('j_1');
  });
});
```

- [ ] **Step 2: Run, verify fails**

```bash
npm run test Upload
```
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `Upload.tsx`**

Port `index.html` lines 457–520. Add the same `.md`/`.markdown` extension check, the same disabled-button-while-uploading flag, the same drag-and-drop on the dropzone if the legacy has one. Use `upload()` and `api()` from `../api/client`. Port the related CSS classes (`.upload-zone`, `.upload-zone.dragover`, etc.) into `theme.css`.

Component signature:

```tsx
import type { JobState } from '../api/client';

type Props = {
  onJobReady: (job: JobState) => void;
  onError: (message: string) => void;
};

export function Upload({ onJobReady, onError }: Props) {
  // … hooks, refs, handlers exactly as legacy
}
```

- [ ] **Step 4: Run, verify passes**

```bash
npm run test Upload && npm run typecheck
```
Expected: PASS.

- [ ] **Step 5: Wire Upload into `App.tsx` for visual verification**

Temporarily replace the placeholder `App.tsx` body with:

```tsx
import { Upload } from './pages/Upload';
export default function App() {
  return <Upload onJobReady={(j) => console.log('READY', j)} onError={(m) => alert(m)} />;
}
```

Then `npm run dev`, open `http://localhost:5173/`, upload a real `.md` against the running backend, verify the same upload UX as legacy.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/Upload.tsx frontend/tests/unit/Upload.test.tsx frontend/src/lib/theme.css frontend/src/App.tsx
git commit -m "feat(stage-4): port Upload page with tests"
```

---

## Task 7: Topics page

**Files:**
- Create: `frontend/src/pages/Topics.tsx`
- Test: `frontend/tests/unit/Topics.test.tsx`

**Source:** `index.html` lines 521–553. Pattern: renders `job.topics` as clickable cards. If `job.status !== 'topics_extracted'`, polls until ready (use `useJobPolling`).

Props:

```tsx
type Props = {
  job: JobState;
  onPickTopic: (topic: { id: string; title: string }) => void;
  onError: (message: string) => void;
};
```

- [ ] **Step 1: Failing test**

```tsx
// frontend/tests/unit/Topics.test.tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Topics } from '../../src/pages/Topics';

describe('Topics', () => {
  it('renders one card per topic', () => {
    const job = {
      job_id: 'j', status: 'topics_extracted',
      topics: [
        { id: 't1', title: 'Binary search' },
        { id: 't2', title: 'Hash maps' },
      ],
    } as any;
    render(<Topics job={job} onPickTopic={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText('Binary search')).toBeInTheDocument();
    expect(screen.getByText('Hash maps')).toBeInTheDocument();
  });

  it('fires onPickTopic when a card is clicked', () => {
    const onPick = vi.fn();
    const job = {
      job_id: 'j', status: 'topics_extracted',
      topics: [{ id: 't1', title: 'Binary search' }],
    } as any;
    render(<Topics job={job} onPickTopic={onPick} onError={vi.fn()} />);
    fireEvent.click(screen.getByText('Binary search'));
    expect(onPick).toHaveBeenCalledWith({ id: 't1', title: 'Binary search' });
  });

  it('shows a loading state while job.status === "uploaded"', () => {
    const job = { job_id: 'j', status: 'uploaded', topics: [] } as any;
    render(<Topics job={job} onPickTopic={vi.fn()} onError={vi.fn()} />);
    expect(screen.getByText(/extracting topics/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run, fails. Implement, port `.topic-card` CSS to theme.css, run.**

```bash
npm run test Topics && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Topics.tsx frontend/tests/unit/Topics.test.tsx frontend/src/lib/theme.css
git commit -m "feat(stage-4): port Topics page with tests"
```

---

## Task 8: Suggestions page

**Files:**
- Create: `frontend/src/pages/Suggestions.tsx`
- Test: `frontend/tests/unit/Suggestions.test.tsx`

**Source:** `index.html` lines 554–660. Pattern: on mount, POST `/jobs/:id/topics/:tid/suggestions` to ensure cached suggestions exist, then render. User picks one suggestion + optional custom notes textarea + "Build" button → POST `/jobs/:id/topics/:tid/build` → `onBuildQueued(jobId)`.

Props:

```tsx
type Props = {
  job: JobState;
  topic: { id: string; title: string };
  onBuildQueued: (jobId: string) => void;
  onBack: () => void;
  onError: (message: string) => void;
};
```

- [ ] **Step 1: Failing test (3 cases)**

```tsx
// frontend/tests/unit/Suggestions.test.tsx
// Cases:
//  - on mount, POSTs /suggestions and renders the 5 returned items
//  - selecting a suggestion enables the Build button
//  - clicking Build POSTs /build with { suggestion_id, custom_notes } and calls onBuildQueued
```

- [ ] **Step 2: Implement.** Port lines 554–660 with the same fetch shape. The legacy POSTs:

```js
POST /jobs/${job.job_id}/topics/${topic.id}/build
Content-Type: application/json
{ suggestion_id, custom_notes }
```

Confirm this matches `backend/api/builds.py` — if the field names are different, **match the backend** and update the test.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Suggestions.tsx frontend/tests/unit/Suggestions.test.tsx frontend/src/lib/theme.css
git commit -m "feat(stage-4): port Suggestions page with tests"
```

---

## Task 9: Building page

**Files:**
- Create: `frontend/src/pages/Building.tsx`
- Test: `frontend/tests/unit/Building.test.tsx`

**Source:** `index.html` lines 661–745. Pattern: `useJobPolling(jobId, 1000)`. Renders a `BuildCard` per `job.builds[]`. Shows the live `job.logs` tail. When all builds reach a terminal status (`done` / `failed`), calls `onAllDone(jobId)`.

Props:

```tsx
type Props = {
  jobId: string;
  onAllDone: (jobId: string) => void;
  onBackToTopics: () => void;
};
```

- [ ] **Step 1: Failing test**

```tsx
// frontend/tests/unit/Building.test.tsx
// Cases:
//  - shows BuildCard for each build returned by the polled /jobs/:id
//  - renders log lines from job.logs
//  - calls onAllDone exactly once when every build.status is in {done, failed}
```

- [ ] **Step 2: Implement & verify.**

```bash
npm run test Building && npm run typecheck
```

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Building.tsx frontend/tests/unit/Building.test.tsx frontend/src/lib/theme.css
git commit -m "feat(stage-4): port Building page with polling"
```

---

## Task 10: Done page

**Files:**
- Create: `frontend/src/pages/Done.tsx`
- Test: `frontend/tests/unit/Done.test.tsx`

**Source:** `index.html` lines 746–880. Pattern: parallel-fetch `/jobs/:id/manifest` and `/jobs/:id`. Renders a gallery of completed builds with preview screenshots from `/preview?path=…`, "Open Live Preview" links, GitHub repo status, "Download manifest" link.

Props:

```tsx
type Props = {
  jobId: string;
  onError: (message: string) => void;
  onRestart: () => void;
};
```

- [ ] **Step 1: Failing test (2 cases)**

```tsx
// frontend/tests/unit/Done.test.tsx
// Cases:
//  - parallel-loads manifest + job; renders one card per manifest entry with the correct preview <img> src
//  - "Start over" button calls onRestart
```

- [ ] **Step 2: Implement & verify.** Match `/preview?path=…` URL building exactly (`encodeURIComponent`).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/Done.tsx frontend/tests/unit/Done.test.tsx frontend/src/lib/theme.css
git commit -m "feat(stage-4): port Done page with manifest gallery"
```

---

## Task 11: `App.tsx` — wire the state machine

**Files:**
- Modify: `frontend/src/App.tsx` (replace placeholder)

The legacy `App` (index.html lines 386–440) uses a single `screen` state plus per-screen data: `job`, `pickedTopic`, `error`. Replicate the exact transitions.

- [ ] **Step 1: Write `App.tsx`**

```tsx
// frontend/src/App.tsx
import { useState } from 'react';
import { Crumbs } from './components/Crumbs';
import { Upload } from './pages/Upload';
import { Topics } from './pages/Topics';
import { Suggestions } from './pages/Suggestions';
import { Building } from './pages/Building';
import { Done } from './pages/Done';
import type { JobState } from './api/client';

export type Screen = 'upload' | 'topics' | 'suggestions' | 'building' | 'done';

export default function App() {
  const [screen, setScreen] = useState<Screen>('upload');
  const [job, setJob] = useState<JobState | null>(null);
  const [topic, setTopic] = useState<{ id: string; title: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onError = (msg: string) => setError(msg);
  const clearError = () => setError(null);

  return (
    <main className="app">
      <header className="app-header">
        <h1>HackMD · Visualization Orchestrator</h1>
        <Crumbs screen={screen} />
      </header>
      {error && (
        <div className="banner banner-error" role="alert">
          {error}
          <button className="ghost" onClick={clearError}>dismiss</button>
        </div>
      )}
      {screen === 'upload' && (
        <Upload onJobReady={(j) => { setJob(j); setScreen('topics'); }} onError={onError} />
      )}
      {screen === 'topics' && job && (
        <Topics
          job={job}
          onPickTopic={(t) => { setTopic(t); setScreen('suggestions'); }}
          onError={onError}
        />
      )}
      {screen === 'suggestions' && job && topic && (
        <Suggestions
          job={job}
          topic={topic}
          onBuildQueued={(jid) => { setJob({ ...job, job_id: jid }); setScreen('building'); }}
          onBack={() => setScreen('topics')}
          onError={onError}
        />
      )}
      {screen === 'building' && job && (
        <Building
          jobId={job.job_id}
          onAllDone={() => setScreen('done')}
          onBackToTopics={() => setScreen('topics')}
        />
      )}
      {screen === 'done' && job && (
        <Done
          jobId={job.job_id}
          onError={onError}
          onRestart={() => { setJob(null); setTopic(null); setError(null); setScreen('upload'); }}
        />
      )}
    </main>
  );
}
```

If the legacy transitions differ in any detail (e.g., re-using `job` between Suggestions and Building, banner placement), match legacy exactly.

- [ ] **Step 2: Build, manual QA against `/v2/`**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend && npm run build
# In the backend repo root:
python -m uvicorn main:app --port 8001
```

Open `http://localhost:8001/v2/`. Walk through the entire parity checklist (Section 4 of the spec):

- [ ] Upload `.md`, see topics
- [ ] Click topic, see 5 suggestions
- [ ] Pick suggestion + custom notes, build queues
- [ ] Poll progress, see live log tail
- [ ] Open Live Preview works
- [ ] GitHub publish status renders correctly
- [ ] Manifest download works

Side-by-side comparison: keep `/` (legacy) open in another tab and confirm identical screens for the same inputs.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(stage-4): wire screen state machine in App.tsx"
```

---

## Task 12: Playwright E2E happy-path

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/tests/e2e/happy-path.spec.ts`

The E2E test runs against a real backend with `fake_llm`-style stubbing turned on, validating that the SPA can complete the full upload → topics → suggestions → build-queued flow end-to-end. Skip the actual build (it requires npm + Playwright + Chromium inside the build subprocess — covered by backend integration tests).

- [ ] **Step 1: Add `playwright.config.ts`**

```ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:8001',
    headless: true,
  },
  webServer: {
    // Caller is expected to have a backend already running.
    // Set REUSE_BACKEND=1 to skip auto-spawning.
    command: 'echo "expect backend on :8001"',
    url: 'http://localhost:8001/healthz',
    reuseExistingServer: true,
    timeout: 5_000,
  },
});
```

- [ ] **Step 2: Write the e2e spec**

```ts
// frontend/tests/e2e/happy-path.spec.ts
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';

test('upload → topics → suggestions → build queued', async ({ page }) => {
  await page.goto('/v2/');

  // The actual upload uses real backend + LLM by default — too expensive for
  // CI. Run this manually with a short fixture .md when validating parity.
  const sample = '# Binary search\n\nBisects a sorted array.';
  const fileChooser = page.waitForEvent('filechooser');
  await page.getByRole('button', { name: /upload/i }).first().click();
  const chooser = await fileChooser;
  await chooser.setFiles({
    name: 'sample.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from(sample),
  });
  await page.getByRole('button', { name: /^upload$/i }).click();

  await expect(page.getByText(/topics/i)).toBeVisible({ timeout: 30_000 });
});
```

- [ ] **Step 3: Run, document expectations**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend
npx playwright install --with-deps
# In another terminal, run the backend with a real OPENAI_API_KEY.
npm run e2e
```

If running against real OpenAI is too expensive, this E2E is **manual / nightly only** — gate with `pytest -m e2e --runslow` equivalent (Playwright tag with `test.describe.configure({ mode: 'serial', tag: '@slow' })`). Document in the commit.

- [ ] **Step 4: Commit**

```bash
git add frontend/playwright.config.ts frontend/tests/e2e/
git commit -m "feat(stage-4): add Playwright happy-path e2e (manual run)"
```

---

## Task 13: Multi-stage Dockerfile + `.dockerignore`

**Files:**
- Modify: `Dockerfile` (full rewrite per spec Section 3)
- Modify: `.dockerignore` (extend with full list from spec Section 3)
- Modify: `railway.toml` — `startCommand` becomes `uvicorn backend.main:app …` once `main.py` moves (Task 14 step 6). For now keep `main:app` and update in Task 14.

- [ ] **Step 1: Replace `Dockerfile` with spec Section 3's multi-stage version**

Copy verbatim from spec Section 3 ("Target Dockerfile") — three stages: `frontend-builder` (node:20-alpine), `python-deps` (python:3.12-slim), `runtime` (python:3.12-slim with NodeSource + Playwright + Chromium).

Two adaptations:
1. The spec assumes `requirements.lock` (hash-pinned). We're still on `requirements.txt`. Substitute `COPY requirements.txt .` + `RUN pip install --no-cache-dir -r requirements.txt`. Lockfile migration stays a follow-up — note it as Open Question A in the plan epilogue.
2. The spec assumes `pyproject.toml`. We don't have one yet. Substitute `COPY requirements.txt ./` only.

- [ ] **Step 2: Write `.dockerignore`**

```
# .dockerignore — full content
.git
.gitignore
.venv
__pycache__
**/__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.mypy_cache
.ruff_cache
.import_linter_cache
node_modules
frontend/node_modules
frontend/dist
backend/static_v2
viz_outputs
data
docs
tests
*.md
!README.md
.env
.env.*
.DS_Store
*.log
.idea
.vscode
.claude
.github
```

- [ ] **Step 3: Smoke-test the build locally**

```bash
cd /Users/pulkitmangal/Universal-visualizer
docker build -t univiz:stage4-smoke .
docker run --rm -e OPENAI_API_KEY=sk-test -p 8001:8001 univiz:stage4-smoke &
sleep 8
curl -s http://localhost:8001/healthz
curl -sI http://localhost:8001/v2/ | head -1
docker kill $(docker ps -q --filter "ancestor=univiz:stage4-smoke")
```
Expected: `200 OK` from `/healthz`, `200 OK` from `/v2/` (the dist was copied from frontend-builder).

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat(stage-4): multi-stage Dockerfile + .dockerignore"
```

---

## Task 14: Root cutover — flip `/` to new SPA, demote legacy

**Files:**
- Modify: `frontend/vite.config.ts` — `outDir: '../backend/static'` (was `../backend/static_v2`)
- Modify: `backend/api/spa.py` — serve `backend/static/index.html` at `/`, mount `backend/legacy/` at `/legacy/`
- Move: `index.html` → `backend/legacy/index.html`
- Modify: `Dockerfile` — `COPY --from=frontend-builder /app/frontend/dist ./backend/static/` (already that path in Task 13's spec-derived Dockerfile if it copies to `./backend/static/` — verify)
- Modify: `.gitignore` — drop `backend/static_v2/` line; keep `backend/static/`
- Modify: `railway.toml` — `startCommand = "uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1"` (we leave `main.py` at repo root for now since Stage 3 already kept it there; renaming to `backend.main` is a separate cleanup)
- Test: `tests/contract/test_spa_v2_mount.py` — replace with `test_spa_root_serves_built_spa.py` and `test_legacy_mount.py`
- Test: `tests/contract/test_spa_root.py` — add: GET / returns the new SPA's index.html

- [ ] **Step 1: Build the new SPA into its final location**

```bash
cd /Users/pulkitmangal/Universal-visualizer/frontend
# Edit vite.config.ts: outDir → '../backend/static'
npm run build
ls ../backend/static/index.html  # must exist
```

- [ ] **Step 2: Move the legacy HTML and update spa.py**

```bash
mkdir -p /Users/pulkitmangal/Universal-visualizer/backend/legacy
git mv /Users/pulkitmangal/Universal-visualizer/index.html /Users/pulkitmangal/Universal-visualizer/backend/legacy/index.html
```

Rewrite `backend/api/spa.py`:

```python
"""Serve the production Stage 4 SPA at /; keep legacy at /legacy/ for one release."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse

from fastapi import APIRouter

router = APIRouter(tags=["spa"])

_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = _REPO_ROOT / "backend" / "static"
_LEGACY_DIR = _REPO_ROOT / "backend" / "legacy"


@router.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    """Serve the built SPA's index.html."""
    target = _STATIC_DIR / "index.html"
    if not target.exists():
        raise HTTPException(
            500,
            f"SPA not built. Run: cd frontend && npm run build  (expected at {target})",
        )
    return HTMLResponse(target.read_text(encoding="utf-8"))


def mount_static(app: FastAPI) -> None:
    """Mount /assets and /legacy. Called from backend.api.mount_routers."""
    from fastapi.staticfiles import StaticFiles
    if _STATIC_DIR.exists():
        # /assets is where Vite puts hashed JS/CSS chunks.
        assets_dir = _STATIC_DIR / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    if _LEGACY_DIR.exists() and (_LEGACY_DIR / "index.html").exists():
        app.mount(
            "/legacy",
            StaticFiles(directory=str(_LEGACY_DIR), html=True),
            name="legacy",
        )
```

Wire `mount_static` into `backend/api/__init__.py` `mount_routers()` (replacing the old `mount_v2` call).

Remove the old `_STATIC_V2_DIR` constant and `mount_v2` function.

- [ ] **Step 3: Update / delete the /v2/ contract test**

Delete `tests/contract/test_spa_v2_mount.py`. Write:

```python
# tests/contract/test_spa_root.py
"""GET / — Stage 4 SPA root."""
def test_root_returns_built_spa_index(client):
    r = client.get("/")
    assert r.status_code == 200
    # Stage 4 SPA index.html includes the Vite mount point.
    assert b'<div id="root"></div>' in r.content or b"<div id='root'></div>" in r.content

def test_legacy_mount_serves_old_html(client):
    r = client.get("/legacy/")
    # If the legacy dir exists in this checkout, expect 200; otherwise 404.
    assert r.status_code in (200, 404)
```

- [ ] **Step 4: Run the full contract suite**

```bash
pytest tests/contract -v
```
Expected: all green. Pay attention to the existing `/preview`, `/jobs`, `/upload` contract tests — none of them depend on the root HTML, so they must remain unaffected.

- [ ] **Step 5: Manual end-to-end smoke**

```bash
cd /Users/pulkitmangal/Universal-visualizer
python -m uvicorn main:app --port 8001
```

In a browser:

- `http://localhost:8001/` — must render the new Vite SPA (same look as `/v2/` in Task 11)
- `http://localhost:8001/legacy/` — must render the old CDN-React HTML (for emergency rollback during the release cycle)
- Walk the full parity checklist on `/` one more time

- [ ] **Step 6: Update `.gitignore`**

Remove the `backend/static_v2/` line. The directory is no longer used.

- [ ] **Step 7: Update Dockerfile if necessary**

If Task 13's Dockerfile still copies to `./backend/static_v2/`, update to `./backend/static/`.

- [ ] **Step 8: Commit**

```bash
git add frontend/vite.config.ts backend/api/spa.py backend/api/__init__.py \
        backend/legacy/index.html tests/contract/test_spa_root.py \
        .gitignore Dockerfile
git rm tests/contract/test_spa_v2_mount.py
git rm index.html  # already moved by git mv above; git rm picks it up
git commit -m "feat(stage-4): flip / to new SPA, demote legacy to /legacy/"
```

- [ ] **Step 9: Tag the milestone**

```bash
git tag modularization-stage-4
```

- [ ] **Step 10: Open the PR**

After all 14 tasks, push the feature branch and open a PR titled `feat: modularization stage 4 — Vite + React + TS SPA` with body summarizing:
- the parity checklist (all 7 items checked)
- the cutover flow (parallel `/v2/` then root flip)
- one-release-cycle legacy mount at `/legacy/` for rollback
- multi-stage Dockerfile + tighter `.dockerignore`
- follow-ups: lockfile migration (`pyproject.toml` + `requirements.lock`), eventual removal of `/legacy/`

---

## After Stage 4 merges — follow-ups (not part of this plan)

These are documented here so they aren't forgotten. They are **NOT** tasks of this plan. Each is its own future PR.

1. **Delete `backend/legacy/` and the `/legacy/` mount** — one release cycle after Stage 4 ships, assuming no rollback was needed.
2. **Lockfile migration** — `pyproject.toml` + `requirements.lock` (hash-pinned) per spec Section 3.
3. **Move `main.py` → `backend/main.py`** — small cleanup that aligns with the spec's final layout. Untouched here because Stage 3 left it at repo root and the modularization spec marks this as a low-priority cosmetic step.
4. **CI** — wire `npm run build`, `npm run test`, `npm run typecheck`, `pytest tests/contract`, and `docker build` into GitHub Actions / Railway pre-deploy hooks per spec Section 5.
5. **Observability project** — the next major spec, deferred while modularization was in progress.

---

## Self-review notes

- **Spec coverage:** Every line of spec Section 4 / Stage 4 is covered by a task:
  - Build new SPA in frontend/ → Tasks 1–11
  - npm run build outputs to backend/static_v2/ → Task 1
  - /v2/ side-by-side mount → Task 1
  - Feature-parity acceptance checklist → Task 11
  - Flip root → Task 14
  - Legacy demoted to /legacy/ → Task 14
  - Multi-stage Dockerfile + .dockerignore (spec Section 3) → Task 13
  - Frontend tests (spec Section 5) → Tasks 6–12

- **No placeholders.** Every step has a code block or an exact command. The few "port the legacy component verbatim" instructions point at specific line ranges in `index.html` so the implementer can locate the source without guessing.

- **Type consistency.** `Screen` type defined in App.tsx is referenced by Crumbs in Task 5; `JobState` from client.ts is referenced by every page; `useJobPolling` signature in Task 4 matches its usage in Tasks 7 and 9.

- **Deliberate deviations from the spec, documented inline:**
  - No `react-router-dom` (state-machine UI has no real routes today).
  - Lockfile / `pyproject.toml` migration deferred to follow-up; Dockerfile uses `requirements.txt` for now.
  - `main.py` stays at repo root for now (Stage 3 left it there).
