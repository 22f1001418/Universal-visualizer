# HackMD Visualization Orchestrator

A FastAPI + React app that:

1. Accepts a HackMD `.md` lecture script
2. Runs **Agent A** (topic extraction) to find 3-7 spots where an interactive viz would help a beginner
3. For each topic, runs **Agent B** (viz suggestion) on demand to produce 5 distinct approaches with beginner/intermediate benefit lines
4. Lets the user pick a suggestion (and optionally add custom notes)
5. Spawns `fixed_main_v6.py` as a subprocess to generate the viz as a single self-contained `index.html` (plus a screenshot) — no build step
6. Publishes each viz to a GitHub repo and returns an embed manifest: `{section, embed_after_sentence, embed_url, screenshot}` per viz so a content creator can drop them into the script

## Architecture

```
┌─────────────┐    1. upload .md      ┌────────────────────┐
│   Browser   │ ─────────────────────>│  FastAPI app       │
│  (React SPA)│ <─────────────────────│  (this codebase)   │
└─────────────┘    poll job state     │                    │
                                       │  ┌──────────────┐  │
                                       │  │ Agent A      │  │ ─OpenAI─>
                                       │  │ topic extract│  │
                                       │  └──────────────┘  │
                                       │  ┌──────────────┐  │
                                       │  │ Agent B      │  │ ─OpenAI─>
                                       │  │ viz suggest  │  │
                                       │  └──────────────┘  │
                                       │  ┌──────────────┐  │
                                       │  │ subprocess   │  │ ─exec─> fixed_main_v6.py
                                       │  │ runner       │  │         (existing CLI)
                                       │  └──────────────┘  │
                                       └────────────────────┘
```

## Setup

### 1. Install Python dependencies

```bash
cd hackmd-viz-orchestrator
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Set up .env

```bash
cp .env.example .env
```

Then edit `.env`:

```bash
OPENAI_API_KEY=sk-proj-...
OPENAI_TEXT_MODEL=gpt-4o-mini    # or gpt-5, gpt-4o, etc.
FIXED_MAIN_PATH=/absolute/path/to/fixed_main_v6.py
```

The `FIXED_MAIN_PATH` is the most important — point it at the viz generator
from the previous build step. The orchestrator runs that file as a subprocess.

### 3. Make sure fixed_main_v6.py works on its own

The orchestrator delegates the actual viz generation to `fixed_main_v6.py`.
Verify it runs standalone first:

```bash
cd /path/to/wherever/fixed_main_v6.py/lives
python fixed_main_v6.py --topic "binary search visualization"
```

If that creates a `binary-search-visualization-viz/` directory and a
screenshot, you're set.

### 4. Run the orchestrator

```bash
python -m backend.main
```

It serves on `http://127.0.0.1:8001`.

Open that URL in your browser.

## How the user flow works

```
1. UPLOAD                      User picks a .md file + selects track.
                               POST /upload  → Agent A runs inline (~5s).

2. TOPICS                      User sees 3-7 cards, one per extracted topic.
                               Each card shows section, anchor sentence,
                               why-visual-helps, and audience difficulty.

3. SUGGESTIONS                 User clicks a topic.
                               POST /jobs/{id}/topics/{tid}/suggestions
                               Agent B returns 5 approaches.
                               User picks one + optionally types custom notes.

4. BUILDING                    POST /jobs/{id}/topics/{tid}/build
                               Orchestrator spawns fixed_main_v6.py as subprocess,
                               which emits a self-contained index.html + screenshot.
                               Build phases: draft → validate → polish → publish → done.
                               Frontend polls /jobs/{id} every 2s and shows
                               progress phase + tail of stdout.

5. PUBLISH                     On a successful build the orchestrator publishes the
                               viz to a GitHub repo (served as a static site) and
                               records the embed URL on the job.

6. MANIFEST                    Once builds complete, user sees:
                               - screenshots
                               - the published embed URL per viz
                               - embed manifest JSON to download
```

## Endpoints

| Method | Path                                              | Purpose |
|--------|---------------------------------------------------|---------|
| GET    | `/`                                               | Serve the React SPA |
| GET    | `/healthz`                                        | Health check + show whether `fixed_main_v6.py` is reachable |
| POST   | `/upload`                                         | Upload .md, run Agent A inline |
| GET    | `/jobs`                                           | List all jobs |
| GET    | `/jobs/{job_id}`                                  | Full job state (poll for progress) |
| GET    | `/jobs/{job_id}/topics`                           | Just the extracted topics |
| POST   | `/jobs/{job_id}/topics/{topic_id}/suggestions`    | Run Agent B (cached after first call) |
| POST   | `/jobs/{job_id}/topics/{topic_id}/build`          | Pick + queue viz build (builds + publishes) |
| GET    | `/jobs/{job_id}/manifest`                         | Final embed manifest |
| GET    | `/preview?path=…`                                 | Serve a screenshot from VIZ_OUTPUT_DIR |

## Token monitoring

Every LLM call streams its cost to the terminal:

```
[Tokens] agent_A_topic_extraction       [gpt-4o-mini] in=4823   out=2104  cost=$0.0020  job=6927/500000 (1%)
[Tokens] agent_B_viz_suggest:topic_1    [gpt-4o-mini] in=1284   out=1102  cost=$0.0009  job=8029/500000 (2%)
```

Each job has a hard budget (`TOKEN_BUDGET_PER_JOB`, default 500K). If exceeded, the job aborts.

The final manifest endpoint returns total tokens + USD cost for the whole job.

## Reasoning models (gpt-5, o-series)

The LLM client auto-detects reasoning models and uses the right kwargs:

- `max_completion_tokens` instead of `max_tokens`
- `reasoning_effort` (default `low`, override via `.env`)
- Drops `temperature`, `top_p`, etc. (reasoning models reject them)

Reasoning tokens are surfaced in logs:

```
[Reasoning] hidden=10502  visible=1602  ratio=87%
```

So you know how much of your output budget is going into hidden CoT vs visible answer.

## Embed manifest format

```json
{
  "job_id": "abc123",
  "ready": true,
  "manifest": [
    {
      "section": "## How CNNs Work",
      "embed_after_sentence": "The kernel slides across the input matrix, computing dot products at each position.",
      "topic": "Convolutional Layer Operation",
      "why_visual_helps": "Beginners struggle to mentally simulate the kernel sweep…",
      "viz_title": "Matrix slide animation",
      "viz_brief": "Convolutional Layer Operation (Matrix slide animation) — Show a 5x5 input grid…",
      "embed_url": "https://viz-aiml.vercel.app/convolutional-neural-nets/convolutional-layer-operation/",
      "repo_edit_url": "https://github.com/<owner>/viz-aiml/tree/main/convolutional-neural-nets/convolutional-layer-operation",
      "screenshot_path": "/app/viz_outputs/convolutional-layer-operation-viz/screenshot.png",
      "status": "ok"
    }
  ],
  "token_usage": { "total_tokens": 47832, "estimated_cost_usd": 0.0123 }
}
```

The `embed_after_sentence` field is verbatim from the source script, so a
later assembly step can simply do a string match + insertion to drop the
viz reference into the right place.

## Deployment

- **Tool** → Render (Docker Web Service via `Dockerfile`, disk at `/app/viz_outputs`).
  See `render.yaml`. Set `OPENAI_API_KEY`, `GITHUB_TOKEN`, `GITHUB_OWNER`,
  `PROGRAM_REPOS` in the Render dashboard.
- **Animations** → one GitHub repo per program (`PROGRAM_REPOS`), each imported
  once into Vercel (framework "Other", no build command). On each successful
  build the tool pushes `<module>/<viz>/{index.html,screenshot.png}` to `main`;
  Vercel auto-deploys and serves `<vercel_base>/<module>/<viz>/`.

## Limitations

- **Single concurrent build per topic.** A second build for the same topic overwrites the first.
- **In-memory job store.** Jobs evict after 24 hours. For multi-process deployment swap `backend/store.py` for Redis/SQLite.
- **No SSE streaming yet** — frontend polls every 2 seconds. Cheap and simple.
- **No authentication** — built for local / trusted-network use.
