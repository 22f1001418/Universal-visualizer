"""
Universal Visualization Generator — v4.3 (Topic-Agnostic)
========================================================

Works for ANY topic: DSA, ML, CS concepts, math, networks, etc.
Examples:
  python main.py --topic "binary search tree insertion"
  python main.py --topic "gradient descent"
  python main.py --topic "dijkstra shortest path"
  python main.py --topic "neural network forward pass"
  python main.py --topic "fourier transform"
  python main.py --topic "convolutional neural network"
  python main.py --topic "PageRank algorithm"
  python main.py --topic "TCP handshake"

Key design:
  - Topic classifier decides the VISUALIZATION PATTERN best suited to the topic.
  - Each pattern has its own system prompt additions and semantic test suite.
  - All patterns share a universal state-machine contract (steps[], stepIdx).
  - Playwright tests are assembled dynamically from universal + pattern-specific checks.

Prerequisites:
    pip install openai python-dotenv playwright
    playwright install chromium

Fixes applied (v4.1):
  CRITICAL-1  : _run_npm_install return value now checked; fatal on failure.
  CRITICAL-2  : Preview port randomised per-run to avoid EADDRINUSE conflicts.
  CRITICAL-3  : Popen stdout/stderr redirected to DEVNULL (pipe buffer deadlock fix).
  CRITICAL-4  : test variable shadow fixed; failed_test appended explicitly.
  LOGICAL-5   : build_error_loop propagates timeout/failure flag; main skips runtime.
  LOGICAL-6   : runtime_fix_loop feeds build errors back to LLM instead of breaking.
  LOGICAL-7   : classify_topic strips quotes/punctuation/prose from LLM response.
  LOGICAL-8   : enforce_pinned_deps handles >=, >, <=, *, workspace: ranges.
  LOGICAL-9   : Preview startup wait raised to 25 s; configurable constant added.
  LOGICAL-10  : Screenshot named {topic_slug}_screenshot.png to prevent overwrite.
  MINOR-1     : llm_call has a 120 s HTTP timeout.
  MINOR-2     : LLM output dump written to project_dir (or /tmp fallback).
  MINOR-3     : format_files_for_prompt warns when prompt may exceed context.
  MINOR-4     : Closing fence regex accepts optional trailing language tag.
  MINOR-5     : INTERACTION_SETTLE raised to 1.2 s for slow framer-motion.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

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

# (LangSmith tracing setup moved to backend/viz_generator/llm.py in Stage 2 Task 2)


# ─────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("viz_agent")


# ─────────────────────────────────────────────────────────────
# CONFIGURATION CONSTANTS
# ─────────────────────────────────────────────────────────────

# ── LLM machinery moved to backend/viz_generator/llm.py (Task 2, Stage 2) ──
# LLM_PROVIDER, DEFAULT_MODELS, MODEL_NAME, TOKEN_BUDGET,
# LLM_DEFAULT_TEMPERATURE, LLM_DEFAULT_MAX_TOKENS, LLM_FIX_MAX_TOKENS,
# PRICE_PER_1K_TOKENS, _GEMINI_PRICES, REASONING_EFFORT,
# _is_reasoning_model, _REASONING_MODEL_PREFIXES,
# TokenUsageTracker, token_tracker, status,
# _init_client, _get_client, llm_call
# are all re-imported below for back-compat while Stage 2 progresses.
from backend.llm import LLMTask  # noqa: F401 — for Stage 2 call sites
from backend.llm import is_reasoning_model  # noqa: F401 — used indirectly via _is_reasoning_model
from backend.viz_generator.llm import (
    LLM_PROVIDER,
    DEFAULT_MODELS,
    MODEL_NAME,
    TOKEN_BUDGET,
    LLM_DEFAULT_TEMPERATURE,
    LLM_DEFAULT_MAX_TOKENS,
    LLM_FIX_MAX_TOKENS,
    PRICE_PER_1K_TOKENS,
    _GEMINI_PRICES,
    _REASONING_MODEL_PREFIXES,
    REASONING_EFFORT,
    _is_reasoning_model,
    TokenUsageTracker,
    token_tracker,
    status,
    _init_client,
    _get_client,
    llm_call,
)

BUILD_RETRIES: int = 7
RUNTIME_RETRIES: int = 3
SUBPROCESS_TIMEOUT: int = 180           # seconds — npm install/build hard cap

PREVIEW_STARTUP_WAIT: float = 25.0     # seconds — max wait for vite preview port
PREVIEW_PAGE_TIMEOUT: int = 12_000     # ms — Playwright page.goto timeout
PREVIEW_IDLE_TIMEOUT: int = 10_000     # ms — Playwright networkidle timeout
INTERACTION_SETTLE: float = 1.2        # seconds — pause after click for re-render

ERROR_DISPLAY_MAX_LINES: int = 40

PROMPT_SIZE_WARN_CHARS: int = 200_000

# Allowed file extensions that the LLM may produce
ALLOWED_FILE_EXTENSIONS: set[str] = {
    ".tsx", ".ts", ".jsx", ".js", ".cjs", ".mjs",
    ".css", ".html", ".json",
}


# ─────────────────────────────────────────────────────────────
# TOPIC TAXONOMY
# ─────────────────────────────────────────────────────────────

PATTERN_PRIORITY: list[str] = [
    "stepped_algorithm",
    "optimization",
    "tree_graph",
    "neural_network",
    "mathematical",
    "protocol_flow",
    "generic",
]

TOPIC_PATTERNS: dict[str, dict[str, Any]] = {

    "stepped_algorithm": {
        "keywords": [
            "sort", "search", "traversal", "bfs", "dfs", "dijkstra", "bellman",
            "prim", "kruskal", "topological", "flood fill", "a*", "astar",
            "binary search", "linear search", "heap", "quickselect",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Stepped Algorithm
- Pre-compute ALL steps into steps[] before animation.
- Each step: { state (full data snapshot), highlights, phase, message }
- Minimum steps for non-trivial input: sort/search >= 20, graph >= 10.
- stepIdx=0 is the initial unsorted/unvisited state.
- 'Complete'/'Done' only when stepIdx === steps.length - 1.
- Show step counter: "STEP N / TOTAL".
- Colors: unvisited=gray, active/comparing=amber, done/sorted=emerald.
""",
        "tests": [
            {
                "check": """(() => {
                    const t = document.body.innerText;
                    const m = t.match(/step[\\s\\S]*?(\\d+)\\s*[\\/of]+\\s*(\\d+)/i);
                    if (!m) return true;
                    return parseInt(m[2]) > 1;
                })()""",
                "description": "Step counter total > 1",
                "fix_hint": "Pre-compute all algorithm steps — steps[] must have more than 1 entry for a non-trivial input.",
            },
            {
                "check": """(() => {
                    const t = document.body.innerText.toLowerCase();
                    const atStart = t.includes('step 0') || t.includes('step: 0');
                    const complete = t.includes('complete') || t.includes('finished') || t.includes('done');
                    return !(atStart && complete);
                })()""",
                "description": "No 'complete/done' at step 0",
                "fix_hint": "Set done/complete state only when stepIdx reaches the last step, never on initial render.",
            },
        ],
    },

    "optimization": {
        "keywords": [
            "gradient descent", "sgd", "adam", "loss", "training", "backprop",
            "learning rate", "convergence", "cost function", "optimization",
            "momentum", "rmsprop", "weight update",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Optimization / Training Loop
- Show a loss curve that updates each epoch/iteration.
- Render the parameter space (2D contour or 1D curve) with a moving point.
- Controls: Play (auto-run epochs), Pause, Step (one epoch), Reset.
- Show current epoch, current loss value, and learning rate.
- The loss must visibly decrease over time (use a convex function like MSE).
- Use SVG or Canvas — NOT a third-party chart library.
- Animate the gradient step: draw an arrow from current point to next point.
""",
        "tests": [
            {
                "check": """(() => {
                    const t = document.body.innerText;
                    return /loss|epoch|iteration|error/i.test(t);
                })()""",
                "description": "Loss/epoch information is displayed",
                "fix_hint": "Show the current loss value and epoch/iteration number prominently.",
            },
            {
                "check": """(() => {
                    return document.querySelector('svg, canvas') !== null;
                })()""",
                "description": "SVG or Canvas element exists for the plot",
                "fix_hint": "Render the loss curve or parameter space using an SVG or Canvas element.",
            },
        ],
    },

    "tree_graph": {
        "keywords": [
            "tree", "bst", "avl", "red-black", "trie", "segment tree", "fenwick",
            "graph", "network", "node", "edge", "linked list", "skip list",
            "heap structure", "b-tree",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Tree / Graph Structure
- Render nodes as circles with SVG, positioned using a proper layout algorithm.
  For trees: use a recursive x/y layout (Reingold-Tilford style or simple level-order).
  For graphs: use a force-directed or fixed grid layout.
- Edges are SVG <line> or <path> elements with arrow markers for directed graphs.
- Animation shows insertions/deletions/rotations step by step.
- Highlight the currently active node (being inserted, compared, rotated) in amber.
- Visited/finalized nodes in emerald. Unvisited in slate.
- Node values must be visible inside each circle.
- Controls: Play, Pause, Step Forward, Step Back, Reset, plus an input to insert a custom value.
""",
        "tests": [
            {
                "check": """(() => {
                    const circles = document.querySelectorAll('circle, [class*="node"], [class*="circle"]');
                    return circles.length > 0;
                })()""",
                "description": "Node elements (circles) rendered in SVG",
                "fix_hint": "Render tree/graph nodes as SVG <circle> elements with values inside.",
            },
            {
                "check": """(() => {
                    const svg = document.querySelector('svg');
                    return svg !== null;
                })()""",
                "description": "SVG canvas exists for tree/graph",
                "fix_hint": "Use an SVG element to render the tree or graph structure.",
            },
        ],
    },

    "neural_network": {
        "keywords": [
            "neural network", "deep learning", "cnn", "rnn", "lstm", "transformer",
            "attention", "forward pass", "backpropagation", "perceptron",
            "activation", "relu", "softmax", "layer", "neuron", "weight",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Neural Network Architecture
- Draw each layer as a column of circles (neurons) connected by lines to the next layer.
- Use SVG. Layer columns evenly spaced; neurons evenly spaced vertically.
- Forward pass animation: highlight active neurons and edges layer by layer.
- Show activation values inside/beside each neuron (0.00-1.00).
- Color: inactive=slate, active=indigo, high-activation=emerald, low=red.
- Controls: Play (auto forward pass), Step (one layer at a time), Reset.
- Show input values on the left and output probabilities on the right.
- Keep to <= 5 layers and <= 8 neurons per layer for readability.
""",
        "tests": [
            {
                "check": "document.querySelectorAll('circle').length >= 4",
                "description": "At least 4 neuron circles rendered",
                "fix_hint": "Render neurons as SVG <circle> elements. A minimal network needs at least input + hidden + output layers.",
            },
            {
                "check": "document.querySelector('line, path') !== null",
                "description": "Edge lines between neurons exist",
                "fix_hint": "Draw SVG <line> or <path> elements connecting neurons between layers.",
            },
        ],
    },

    "mathematical": {
        "keywords": [
            "fourier", "fft", "wavelet", "convolution", "probability", "bayes",
            "normal distribution", "markov", "monte carlo", "prime", "fibonacci",
            "dynamic programming", "memoization", "recursion tree",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Mathematical Concept
- Use SVG or Canvas to render the mathematical structure (waveform, distribution, etc.).
- Animate the concept building up step-by-step (e.g. adding harmonics for Fourier,
  filling the DP table cell by cell, growing the recursion tree node by node).
- Label axes, values, and key mathematical quantities clearly.
- Controls: Play, Pause, Step, Reset. Optionally add sliders for parameters.
- Show the mathematical formula or recurrence relation as text on screen.
- Use smooth transitions between states (CSS transition or requestAnimationFrame).
""",
        "tests": [
            {
                "check": "document.querySelector('svg, canvas') !== null",
                "description": "SVG or Canvas element for mathematical rendering",
                "fix_hint": "Use SVG or Canvas to render the mathematical visualization.",
            },
        ],
    },

    "protocol_flow": {
        "keywords": [
            "tcp", "http", "protocol", "handshake", "packet", "dns", "osi",
            "cache", "memory", "cpu", "pipeline", "process", "thread", "mutex",
            "deadlock", "pagerank", "map reduce", "distributed",
        ],
        "prompt_extra": """
VISUALIZATION PATTERN: Protocol / System Flow
- Show entities (client, server, nodes) as labeled boxes arranged spatially.
- Animate messages/packets as moving arrows between entities.
- Each step advances the protocol by one message or state transition.
- Show the current state label inside each entity box.
- Highlight the active message/packet in flight in amber.
- Timeline or sequence diagram style works well here.
- Controls: Play, Pause, Step Forward, Step Back, Reset.
- Show a log panel listing completed steps.
""",
        "tests": [
            {
                "check": """(() => {
                    const t = document.body.innerText;
                    return document.querySelectorAll('svg rect, div[class*="box"], div[class*="node"], div[class*="entity"]').length > 0
                        || /client|server|node|host/i.test(t);
                })()""",
                "description": "Protocol entities (boxes/nodes) are rendered",
                "fix_hint": "Render protocol participants (client, server, nodes) as visible labeled boxes or SVG rectangles.",
            },
        ],
    },

    "generic": {
        "keywords": [],
        "prompt_extra": """
VISUALIZATION PATTERN: Generic Interactive Visualization
- Choose the most appropriate visual representation for the topic.
- The visualization must animate or progress through states step by step.
- Use SVG, Canvas, or styled divs — whichever best suits the concept.
- Controls: Play, Pause, Step Forward, Step Back, Reset.
- Show a step counter or progress indicator.
- Label what is happening at each step with a short message.
""",
        "tests": [],
    },
}

# ─────────────────────────────────────────────────────────────
# UNIVERSAL SYSTEM PROMPT (shared across all patterns)
# ─────────────────────────────────────────────────────────────

UNIVERSAL_SYSTEM_PROMPT = """You are a senior React developer building interactive educational visualisations.

━━━ STACK ━━━
React 18 + TypeScript 5 + Vite 6 + Tailwind 3.4 + framer-motion 11 + zustand 5 + lucide-react + prism-react-renderer.
- Pin ALL package.json versions to exact semver (no ^ or ~).
- Since package.json has "type":"module", postcss.config.js, tailwind.config.js, AND vite.config.ts MUST use ESM (`export default`), NOT module.exports.
- TypeScript strict: variables that can be null need explicit `T | null`; string-literal unions need explicit type, never inferred as string.

━━━ TYPESCRIPT BROWSER-ONLY RULES (CRITICAL — failing these breaks the build) ━━━
This project does NOT include @types/node and tsconfig has `lib: ["ES2020","DOM","DOM.Iterable"]` only.
Therefore:
- NEVER use `NodeJS.Timeout` or any `NodeJS.*` namespace type. It will fail with TS2503.
- For setInterval/setTimeout return types use: `ReturnType<typeof setInterval>` or `number`.
  Example: `const id = useRef<ReturnType<typeof setInterval> | null>(null);`
- NEVER use `process.env.*`. Use `import.meta.env.*` (Vite's env API).
- NEVER use `require(...)`. Always use `import`.
- NEVER use Node-only globals like `Buffer`, `__dirname`, `__filename`, `global`.
- For browser-only timer IDs, you can also just use `let id: number;` since DOM `setInterval` returns a number.

━━━ BUILD REQUIREMENTS — must compile cleanly on first try ━━━
package.json:
  "type":"module"; scripts: dev="vite", build="tsc -b && vite build", preview="vite preview".
  IMPORTANT: tsc -b is type-check ONLY (tsconfig has noEmit:true). It produces NO .js output.
  vite build does the actual compilation. NEVER use bare "tsc" without -b — it will fail with
  TS5055 (allowImportingTsExtensions requires noEmit). Always keep "tsc -b && vite build".
  EVERY library imported in code MUST be in dependencies. EXACT pinned versions, no ^ or ~.
  dependencies: react 18.3.1, react-dom 18.3.1, framer-motion 11.15.0, zustand 5.0.3, lucide-react 0.468.0. Include prism-react-renderer 2.4.0 ONLY if the visualization includes a CodePanel that renders syntax-highlighted code. For pure visual/algorithm topics, omit it.
  devDependencies: @types/react 18.3.18, @types/react-dom 18.3.5, @vitejs/plugin-react 4.3.4, autoprefixer 10.4.20, postcss 8.4.49, tailwindcss 3.4.17, typescript 5.7.2, vite 6.0.11.

vite.config.ts (ESM): `import react from '@vitejs/plugin-react'; import { defineConfig } from 'vite'; export default defineConfig({ plugins:[react()], server:{ port:3000 }, preview:{ port:4173 } });`
Note: the test runner overrides preview port via CLI --port flag; the config value is just a safe fallback.

tsconfig.json: { compilerOptions: { target:"ES2020", useDefineForClassFields:true, lib:["ES2020","DOM","DOM.Iterable"], module:"ESNext", skipLibCheck:true, moduleResolution:"bundler", allowImportingTsExtensions:true, resolveJsonModule:true, isolatedModules:true, noEmit:true, jsx:"react-jsx", strict:true }, include:["src"], references:[{ path:"./tsconfig.node.json" }] }
tsconfig.node.json (REQUIRED — must be present or tsc -b fails with TS6310):
{ compilerOptions: { composite:true, skipLibCheck:true, module:"ESNext", moduleResolution:"bundler", allowSyntheticDefaultImports:true, strict:true }, include:["vite.config.ts"] }

postcss.config.js (ESM): `export default { plugins: { tailwindcss: {}, autoprefixer: {} } };`

src/main.tsx MUST import createRoot from 'react-dom/client' (NOT 'react-dom'):
  `import { StrictMode } from 'react'; import { createRoot } from 'react-dom/client'; import App from './App'; import './index.css'; createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);`

index.html MUST include `<script type="module" src="/src/main.tsx"></script>` in body. <html lang="en" class="dark">. Google Fonts preconnect + css2 link in head.

src/index.css MUST start with: `@tailwind base; @tailwind components; @tailwind utilities;` BEFORE any custom CSS.

━━━ VISUAL DESIGN LANGUAGE — apply to EVERY topic ━━━
Output must FEEL cinematic — glass-morphism panels over a mesh gradient with role-coloured glows. Reproduce this design system exactly.

Fonts: Space Grotesk (UI), JetBrains Mono (numbers/code). index.html includes Google Fonts preconnect + the css2 link for both families (weights 400/500/600/700 sans, 400/500 mono). Append &display=swap to the Fonts URL for FOUT prevention. In tailwind.config.js fontFamily, ALWAYS include system fallbacks after the Google font: sans: ['Space Grotesk', 'ui-sans-serif', 'system-ui', 'sans-serif'], mono: ['JetBrains Mono', 'ui-monospace', 'monospace']. This ensures the UI is legible even if the CDN is unreachable.

src/index.css MUST define theme vars verbatim:
:root, .dark {
  --s0:#0f1117; --s1:#171921; --s2:#1f222d; --s3:#2b2f3d; --s4:#383d4e;
  --t1:#eceef5; --t2:#9ea3b8; --t3:#656a82; --t-inv:#0f1117;
  --border:#262a38; --border-strong:#363b50;
  --glow:rgba(20,184,166,0.35); --panel-bg:rgba(23,25,33,0.8); --panel-border:rgba(255,255,255,0.07);
  --cell-base:#1a1d28; --cell-border:#2b2f3d;
  --accent:#14B8A6; --accent-muted:rgba(20,184,166,0.12);
  color-scheme:dark;
}
.light {
  --s0:#f4f5f8; --s1:#ffffff; --s2:#eceef3; --s3:#dcdee6; --s4:#c5c8d4;
  --t1:#0f1117; --t2:#4a4e66; --t3:#7f839a; --t-inv:#f4f5f8;
  --border:#dcdee6; --border-strong:#c0c3d0;
  --glow:rgba(13,148,136,0.25); --panel-bg:rgba(255,255,255,0.85); --panel-border:rgba(0,0,0,0.06);
  --cell-base:#ffffff; --cell-border:#dcdee6;
  --accent:#0D9488; --accent-muted:rgba(13,148,136,0.08);
  color-scheme:light;
}
body: font 'Space Grotesk', bg var(--s0), color var(--t1), overflow hidden. #root is 100dvh flex.

Accents (5):
  Dark  teal #14B8A6, coral #F97316, amber #fbbf24, emerald #34d399, violet #8B5CF6
  Light teal #0D9488, coral #EA580C, amber #d97706, emerald #059669, violet #6D28D9
Each page picks ONE primary accent. Dual-input pages override: A=teal, B=coral, output=amber.

Required CSS in src/index.css:
.glass-panel { background:var(--panel-bg); backdrop-filter:blur(16px); -webkit-backdrop-filter:blur(16px); border:1px solid var(--panel-border); border-radius:16px; }
.dark .glass-panel  { box-shadow:0 4px 32px -4px rgba(0,0,0,0.4); }
.light .glass-panel { box-shadow:0 4px 24px -4px rgba(0,0,0,0.06); }

.mesh-bg::before, .mesh-bg::after { content:''; position:fixed; inset:0; z-index:0; pointer-events:none; }
.mesh-bg::before { background:
  radial-gradient(ellipse 70% 50% at 20% 10%, rgba(20,184,166,.06), transparent 60%),
  radial-gradient(ellipse 50% 40% at 80% 90%, rgba(249,115,22,.04), transparent 60%),
  radial-gradient(ellipse 40% 30% at 50% 50%, rgba(139,92,246,.03), transparent 50%); }
.mesh-bg::after { background-image:
  linear-gradient(rgba(255,255,255,.018) 1px, transparent 1px),
  linear-gradient(90deg, rgba(255,255,255,.018) 1px, transparent 1px);
  background-size:40px 40px; }

For each accent define:
  .cell-glow-{a}: box-shadow:0 0 14px 3px rgba(<rgb>,.5), inset 0 0 6px rgba(<rgb>,.1); border-color:<hex>!important.
  .cell-heat-{a}-{0..4}: 5-step ramp from var(--cell-base) to saturated <hex> (dark teal: #171921->#122a28->#0e4540->#0a6058->#068070).
  .accent-{a} (color), .accent-bg-{a} (rgba .12), .accent-border-{a} (rgba .3).

tailwind.config.js (ESM `export default`): darkMode:'class'; extend colors with surface/txt/edge as CSS-var refs + d.{teal,coral,amber,emerald,violet} hex; fontFamily sans=Space Grotesk, mono=JetBrains Mono; boxShadow.glow='0 0 20px 4px var(--glow)'.
Keyframes and animation MUST be specified verbatim:
  keyframes: { 'glow-pulse': { '0%,100%': { boxShadow: '0 0 8px 2px var(--glow)' }, '50%': { boxShadow: '0 0 24px 6px var(--glow)' } } }
  animation: { 'glow-pulse': 'glow-pulse 2.5s ease-in-out infinite' }

App.tsx skeleton:
<div className="flex h-full w-full"><Sidebar /><main className="flex-1 overflow-y-auto mesh-bg relative"><div className="relative z-10 p-6"><Page /></div></main></div>

src/components/Sidebar.tsx: A collapsible left nav panel. Width w-56 when open, w-14 collapsed.
Glass panel background. Contains: app title at top, nav links for any sub-pages (if multi-page),
theme toggle button at bottom (lucide Sun/Moon, calls toggleTheme from useStore).
Uses sidebarOpen from useStore; toggleSidebar on a hamburger icon (lucide Menu).
If the visualization is single-page with no sub-routes, Sidebar renders only the theme toggle
and a collapse button — no nav links needed.

Reusable components in src/components/:
- PageShell({title,icon,accent,description,children}): motion.div entrance opacity 0->1 y 12->0 0.3s; lucide icon + h1 accent-{accent}; optional info callout glass-panel border-l-[3px] accent-border-{accent}.
- Panel({title?,accent?,children}): glass-panel p-4 lg:p-5, optional uppercase mono title text-[10px] tracking-wider.
- AnimControls({step,totalSteps,playing,toggle,reset}): glass-panel; row1=h-2 bg-surface-2 progress, inner motion.div linear-gradient(90deg,var(--accent),rgba(167,139,250,.8)) width via spring 300/30; row2=Play/Stop btn (lucide Play/Square filled, accent-bg-teal accent-teal, aria-label='Play' or 'Pause') + Step Forward btn (aria-label='Step Forward') + Reset btn (aria-label='Reset') + mono "n / N" counter (current step bold accent). ALL buttons MUST have aria-label for test detection.
- StepExplainer({text,accent}): ArrowRight icon + paragraph in glass-panel with 3px left accent-border-{accent}.
- FormulaBar, ShapeBadge, OpSymbol, BigResult, Slider (thumb: [&::-webkit-slider-thumb]:appearance-none w-4 h-4 rounded-full bg-[var(--accent)] shadow-lg shadow-[var(--glow)]), Select, Divider, ControlsRow.
- CodePanel: prism-react-renderer themes.nightOwl/nightOwlLight, 2px linear-gradient(90deg,var(--accent),transparent) on top, Copy->Check button.

src/hooks/useAnimation.ts — implement EXACTLY as below (full TypeScript signature):

```ts
import { useEffect, useRef, useState } from 'react';

interface UseAnimationOptions {
  totalSteps: number;
  baseMs?: number;
}

interface UseAnimationReturn {
  step: number;
  playing: boolean;
  progress: number;
  totalSteps: number;
  toggle: () => void;
  reset: () => void;
  setStep: (n: number) => void;
}

export function useAnimation(
  { totalSteps, baseMs = 800 }: UseAnimationOptions
): UseAnimationReturn {
  const [step, setStepState] = useState(0);
  const [playing, setPlaying] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => { setStepState(0); setPlaying(false); }, [totalSteps]);

  useEffect(() => {
    if (!playing) return;
    intervalRef.current = setInterval(() => {
      setStepState(s => {
        if (s >= totalSteps - 1) { setPlaying(false); return s; }
        return s + 1;
      });
    }, baseMs);                                    // <-- baseMs IS in scope here
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [playing, totalSteps, baseMs]);

  const toggle = () => setPlaying(p => !p);
  const reset  = () => { setPlaying(false); setStepState(0); };
  const setStep = (n: number) =>
    setStepState(Math.max(0, Math.min(totalSteps - 1, n)));

  const progress = totalSteps > 1 ? step / (totalSteps - 1) : 0;
  return { step, playing, progress, totalSteps, toggle, reset, setStep };
}
```

Notes:
- baseMs MUST be destructured from the options object — never reference it as a free identifier.
- Use `ReturnType<typeof setInterval>` for the timer ref — NOT `NodeJS.Timeout`.
- Add the EXACT JSX entry/control logs from the RUNTIME LOGGING section.

src/store/useStore.ts — zustand 5+ store. CRITICAL import rules:

ZUSTAND 5 USES NAMED EXPORTS. Use this import EXACTLY:
```ts
import { create } from 'zustand';     //  CORRECT for zustand 5
```
NEVER `import create from 'zustand'`. That worked in zustand 3-4 only and will fail with TS1192 ("Module has no default export").

Implement EXACTLY as below (full typed store):
```ts
import { create } from 'zustand';

type Theme = 'dark' | 'light';

interface StoreState {
  theme: Theme;
  sidebarOpen: boolean;
  toggleTheme: () => void;
  toggleSidebar: () => void;
}

export const useStore = create<StoreState>()((set) => ({
  theme: 'dark',
  sidebarOpen: true,
  toggleTheme: () =>
    set((state) => {
      const next: Theme = state.theme === 'dark' ? 'light' : 'dark';
      const root = document.documentElement;
      root.classList.remove('dark', 'light');
      root.classList.add(next);
      console.log('[Theme] ->', next);
      return { theme: next };
    }),
  toggleSidebar: () =>
    set((state) => ({ sidebarOpen: !state.sidebarOpen })),
}));
```

Notes:
- The `create<StoreState>()(...)` curried form is REQUIRED so `set` and `state` are typed (no implicit-any errors under strict mode).
- Always type the parameter to set() as `(state)` — TypeScript infers from the generic.

Animation patterns:
  Page entrance -- motion opacity+y in PageShell, 0.3s.
  Active element -- apply cell-glow-{accent} class.
  Building output -- values past anim.step are null/NaN, render as "." faded.
  Value tumble -- motion.span keyed by val, y -6->0 exit y 6, spring 400/25.
  Row/cell enter -- row x -12->0 delay i*0.025; cell scale .85->1 delay (i*cols+j)*0.008, spring 500/28.
  Sidebar nav indicator -- motion.div layoutId="nav-dot".
  Glow pulse -- Tailwind animate-glow-pulse 8px<->24px 2.5s.

THE PULSE -- non-negotiables:
1. Glass panels everywhere; mesh background on every page.
2. Active-element glow with role-based accent (A=teal, B=coral, output=amber). Dual-input pages override page accent.
3. 5-level heatmap cells on numeric grids; build-up renders cells past anim.step as faded ".".
4. Transport bar (gradient progress + Play/Stop + "n / N") on every animated section.
5. StepExplainer narrating each step in prose.
6. Tumble transitions for value changes -- never instant.

For non-grid topics (trees, graphs, protocols), keep glass panels + mesh + glow + transport bar + accents + animations identical; replace ArrayGrid with SVG circles/boxes/etc.

━━━ RUNTIME LOGGING (browser DevTools console) ━━━
The generated app MUST log key lifecycle and state events so a developer can debug by opening DevTools (Cmd+Opt+I) while running `npm run dev`. Use the prefix convention `[Tag]` so logs are filterable.
- App mount: in App's `useEffect(() => { ... }, [])`, log `console.log('[App] mounted', { topic, totalSteps })`.
- Steps generation: after building steps[], log `console.log('[steps] generated', steps.length, 'steps')`. If steps.length <= 1 for a non-trivial input, also `console.warn('[steps] suspiciously few steps --', steps.length)`.
- Step changes: in useAnimation when step advances, `console.log('[Anim] step', step + 1, '/', totalSteps)`.
- Control clicks: `console.log('[Controls] play')`, `'[Controls] pause'`, `'[Controls] reset'`, `'[Controls] step-forward']`, `'[Controls] step-back']` on respective handlers.
- Errors: wrap risky paths (parseMatrix-like utilities, step generation, JSON parsing) in try/catch and on catch, `console.error('[<ComponentName>] <what failed>:', err)`. Never swallow errors silently.
- Theme toggle: `console.log('[Theme] ->', newTheme)`.
Keep logs concise and structured; never log entire matrices/arrays inside hot loops.

━━━ USER INPUT PANEL — REQUIRED on every generated viz ━━━
The app MUST include a glass-panel titled "Custom Input" (collapsible -- chevron toggle, animate height 0->auto). Default dataset renders on first load; user input is optional. Adapt the input UI to the topic's data shape:
- Numeric array (sort, search, cumulative ops): single <textarea>, placeholder "5, 2, 8, 1, 9, 3" -- comma or space separated.
- Tree insertion (BST/AVL/heap): <textarea> with placeholder "50, 30, 70, 20, 40, 60, 80" -- values inserted in order.
- Graphs (BFS/DFS/Dijkstra/Prim): two inputs -- node count (number), edges as one "from,to,weight" per line.
- Neural network input: <textarea> for input vector, comma-separated floats 0-1, placeholder "0.7, 0.2, 0.5".
- Matrix ops: two textareas with placeholder "1, 2, 3\n4, 5, 6" -- rows on separate lines.
- Protocol/scenario flows (TCP, PageRank): scenario <select> with 3-5 preset options.

Behavior:
- "Apply" button (accent-bg-{accent}) parses input, regenerates steps[], resets stepIdx=0, pauses animation.
- Parse failure: inline error pill (rose-colored glass-panel, AlertCircle icon) showing the specific reason ("Expected numbers, found 'abc'" / "Row lengths differ: 3 vs 2"). NEVER crash the app -- wrap parsing in try/catch.
- One-line help text above textarea (text-txt-muted text-xs), e.g. "Numbers separated by commas or spaces. Press Apply to run."
- On successful apply: console.log('[Input] applied', parsedValue).
- Pre-fill the textarea with the current default values so user can see the format.

━━━ STATE-MACHINE CONTRACT ━━━
1. PRE-COMPUTE all steps into steps[] before animation.
2. stepIdx starts at 0.
3. Controls: Play (setInterval, clean on unmount), Pause, StepFwd (min(idx+1, len-1)), StepBack (max(idx-1, 0)), Reset (idx=0, paused).
4. Render derives from steps[stepIdx] -- never from separate state.
5. "Complete" message ONLY when stepIdx === steps.length-1.
6. Display "Step N / TOTAL".

━━━ ANTI-PATTERNS ━━━
x useState(true) for done/complete on init
x steps computed inside JSX render (infinite loop)
x setInterval without clearInterval cleanup
x Rendering JS object directly in JSX: {step} -- use step.message
x Array index as key when items reorder
x All elements "done/sorted/visited" on first render
x steps.length <= 1 for non-trivial input

━━━ OUTPUT FORMAT ━━━
==== FILE: filename.ext ====
[full content]
==== END FILE ====

Output ONLY file blocks. NO prose, NO explanations.

Required files (MUST ALL be present):
  src/App.tsx
  src/main.tsx
  src/index.css
  index.html
  vite.config.ts
  tsconfig.json
  tsconfig.node.json
  tailwind.config.js
  postcss.config.js
  package.json
  src/hooks/useAnimation.ts
  src/store/useStore.ts

Optional (output only if the topic needs them):
  src/components/PageShell.tsx
  src/components/Panel.tsx
  src/components/AnimControls.tsx
  src/components/StepExplainer.tsx
  src/components/Sidebar.tsx
  src/components/CodePanel.tsx

FILE PLACEMENT RULES:
- index.html MUST be at project root. NEVER output it as src/index.html.
- vite.config.ts, tsconfig.json, tsconfig.node.json, tailwind.config.js,
  postcss.config.js, package.json ALL go at project root.
- All source code goes under src/.
"""

# ─────────────────────────────────────────────────────────────
# UNIVERSAL RUNTIME TESTS
# ─────────────────────────────────────────────────────────────

UNIVERSAL_TESTS: list[dict[str, str]] = [
    {
        "check": "document.body.innerText.trim().length > 20",
        "description": "Page renders visible content",
        "fix_hint": "The page is blank. Check createRoot mounts App correctly.",
    },
    {
        "check": """(() => {
            // D7 fix: also check aria-label and title for icon-only buttons
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /play|start|resume|begin|replay/.test(text);
            });
        })()""",
        "description": "Play/Start button present",
        "fix_hint": "Add a Play or Start button. If icon-only, add aria-label='Play' or title='Play' so tests can detect it.",
    },
    {
        "check": """(() => {
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /step|next|forward|advance/.test(text);
            });
        })()""",
        "description": "Step Forward button present",
        "fix_hint": "Add a Step or Next button. If icon-only, add aria-label='Step Forward' so tests can detect it.",
    },
    {
        "check": """(() => {
            const btns = [...document.querySelectorAll('button')];
            return btns.some(b => {
                const text = (b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '')).toLowerCase();
                return /reset|restart|new|clear/.test(text);
            });
        })()""",
        "description": "Reset button present",
        "fix_hint": "Add a Reset button. If icon-only, add aria-label='Reset' so tests can detect it.",
    },
    {
        "check": """(() => {
            const text = document.body.innerText.toLowerCase();
            const isAtStart = /step[:\\s]*0|0\\s*\\/|epoch[:\\s]*0/i.test(text);
            if (!isAtStart) return true;
            const isDone = /complete|finished|converged|all done/.test(text);
            return !isDone;
        })()""",
        "description": "No 'complete/done' message on initial state",
        "fix_hint": (
            "Completion message appears at step 0. "
            "Only show completion when the user reaches the last step (stepIdx === steps.length - 1)."
        ),
    },
]

# D5 fix: validate Tailwind custom tokens resolve correctly
TAILWIND_TOKEN_TEST: dict[str, str] = {
    "check": (
        "(() => {"
        "  const el = document.querySelector('.glass-panel');"
        "  if (!el) return true;"
        "  const bg = window.getComputedStyle(el).background;"
        "  return bg !== '' && bg !== 'none' && bg !== 'rgba(0, 0, 0, 0)';"
        "})()"
    ),
    "description": "glass-panel CSS class resolves to a visible background",
    "fix_hint": (
        "glass-panel background is transparent or missing. "
        "Ensure src/index.css defines --panel-bg and .glass-panel uses it. "
        "Verify tailwind.config.js uses ESM export default with correct content paths."
    ),
}

UNIVERSAL_INTERACTION_TESTS: list[dict[str, str]] = [
    {
        "description": "Step Forward changes visible content",
        "fix_hint": (
            "Clicking Step Forward does not update the display. "
            "Ensure the render reads from steps[stepIdx] and stepIdx state is updated."
        ),
        "js_before": "document.body.innerText",
        "action": """(() => {
            // D7 fix: find by textContent OR aria-label/title for icon-only buttons
            const btn = [...document.querySelectorAll('button')].find(b => {
                const text = b.textContent + ' ' + (b.getAttribute('aria-label') || '') + ' ' + (b.getAttribute('title') || '');
                return /step|next|forward|advance/i.test(text);
            });
            if (btn) btn.click();
        })()""",
        "js_after": "document.body.innerText",
        "assert": "before !== after",
    },
]


# ─────────────────────────────────────────────────────────────
# POLISH SYSTEM PROMPT
# ─────────────────────────────────────────────────────────────

POLISH_SYSTEM_PROMPT = """You are a senior UI designer who refines React visualisations.
You must NOT change any algorithm logic, state machine, or step generation code.
Only improve: colors, typography, spacing, transitions, layout, labels, and aesthetic polish.
"""


# ─────────────────────────────────────────────────────────────
# SETUP — deferred until main() to avoid import-time side effects
# _client, TokenUsageTracker, token_tracker, status, _init_client,
# _get_client are all imported from backend.viz_generator.llm above.
# ─────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# TOPIC CLASSIFIER
# ─────────────────────────────────────────────────────────────

def classify_topic(topic: str) -> tuple[str, dict[str, Any]]:
    """
    Returns (pattern_name, pattern_dict) for the best-matching pattern.
    Uses word-boundary keyword matching first; falls back to LLM classification
    for ambiguous cases. Ties are broken by PATTERN_PRIORITY order.
    """
    topic_lower = topic.lower()

    scores: dict[str, int] = {}
    for name, pattern in TOPIC_PATTERNS.items():
        if name == "generic":
            continue
        score = sum(
            1 for kw in pattern["keywords"]
            if re.search(rf"\b{re.escape(kw)}\b", topic_lower)
        )
        scores[name] = score

    best_name = max(
        scores,
        key=lambda n: (scores[n], -PATTERN_PRIORITY.index(n)),
    )
    best_score = scores[best_name]

    if best_score == 0:
        log.info("  No keyword match for '%s'. Asking LLM to classify...", topic)
        classify_prompt = f"""Classify this visualization topic into exactly one category:
Topic: "{topic}"

Categories (pick the BEST fit):
- stepped_algorithm: sorting, searching, graph traversal, step-by-step algorithms
- optimization: gradient descent, loss minimization, ML training loops
- tree_graph: BST, AVL, tries, linked lists, general graphs, network topology
- neural_network: neural nets, deep learning architectures, forward/backprop pass
- mathematical: Fourier, probability distributions, dynamic programming tables, recursion trees
- protocol_flow: network protocols, OS processes, distributed systems, sequence diagrams
- generic: anything that doesn't fit the above

Reply with ONLY the category name, nothing else."""

        try:
            raw_name = llm_call(
                [{"role": "user", "content": classify_prompt}],
                temperature=1,
                max_tokens=1000,
                step_label="step0_classify",
            ).strip().lower()
        except SystemExit:
            raise
        except Exception:
            log.warning("  LLM classification failed — defaulting to 'generic'.")
            raw_name = "generic"

        if not raw_name:
            log.warning("  LLM returned empty classification — defaulting to 'generic'.")
            best_name = "generic"
        else:

            # LOGICAL-7: strip quotes, punctuation, and any leading prose the LLM may add
            raw_name = re.sub(r"[\"'.,;:]", "", raw_name).strip()
            # If LLM returned a sentence like "The answer is: stepped_algorithm", take last word
            tokens = raw_name.split()
            raw_name = tokens[-1] if tokens else "generic"

            if raw_name not in TOPIC_PATTERNS:
                log.warning(
                    "  LLM returned unrecognised category '%s' — defaulting to 'generic'.",
                    raw_name,
                )
                raw_name = "generic"
            best_name = raw_name

    log.info("  Topic pattern: '%s'", best_name)
    return best_name, TOPIC_PATTERNS[best_name]


# ─────────────────────────────────────────────────────────────
# UTILITIES
# ─────────────────────────────────────────────────────────────

_FILENAME_HINT = re.compile(
    r"(?:[\w\-./]+/)?[\w\-]+\.(?:tsx?|jsx?|css|html|json|js|cjs|mjs)\b"
)


# Filenames that are almost always the result of an LLM splitting a composite
# filename (e.g. "tsconfig.node.json" gets split into "tsconfig.json" + "node.json").
# When seen as standalone filenames they're fake and must be discarded.
_BOGUS_STANDALONE = frozenset({
    "node.json",          # split from tsconfig.node.json
    "config.js",          # split from vite.config.js / tailwind.config.js
    "config.ts",          # split from vite.config.ts
    "vite.ts",            # split from vite.config.ts
    "tailwind.js",        # split from tailwind.config.js
    "postcss.js",         # split from postcss.config.js
})


def _filter_bogus_files(files: dict[str, str]) -> dict[str, str]:
    """Remove obviously-fake filenames produced by LLMs splitting composites."""
    cleaned: dict[str, str] = {}
    for name, body in files.items():
        if Path(name).name in _BOGUS_STANDALONE:
            log.warning(
                "  ⚠️  Rejecting suspicious filename '%s' — likely an LLM split "
                "of a composite name. Skipping.", name
            )
            continue
        cleaned[name] = body
    return cleaned


def parse_files(text: str) -> dict[str, str]:
    """
    Parse files from LLM output. Tolerates several formats:
      1. ==== FILE: name ====  ...  ==== END FILE ====   (preferred)
      2. ```lang src/foo.tsx \n ...  ```                  (markdown code block w/ filename)
      3. **File: name** \n ```lang \n ... \n ```          (bold header + code block)
      4. ### name \n ```lang \n ... \n ```                (markdown heading + code block)
      5. // File: name \n ...                             (inline comment header)
    Returns {} if nothing parseable found.
    """
    files = _parse_marker_format(text)
    if files:
        return _filter_bogus_files(files)
    return _filter_bogus_files(_parse_codeblock_format(text))


def _parse_marker_format(text: str) -> dict[str, str]:
    files: dict[str, str] = {}
    lines = text.split("\n")
    current_file: str | None = None
    current_content: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("==== FILE:"):
            if current_file:
                files[current_file] = _clean_file_content(current_content)
            current_file = stripped.replace("==== FILE:", "").replace("====", "").strip()
            current_content = []
        elif stripped.startswith("==== END FILE"):
            if current_file:
                files[current_file] = _clean_file_content(current_content)
            current_file = None
            current_content = []
        else:
            if current_file is not None:
                if line.startswith("```") and not current_content:
                    continue
                current_content.append(line)
    if current_file and current_content:
        files[current_file] = _clean_file_content(current_content)
    return files


def _clean_file_content(lines: list[str]) -> str:
    """Join content lines and strip only leading/trailing backtick fences and whitespace."""
    content = "\n".join(lines).strip()
    if content.startswith("```"):
        first_newline = content.find("\n")
        if first_newline != -1:
            content = content[first_newline + 1:]
    if content.endswith("```"):
        content = content[:-3]
    return content.strip()


def _parse_codeblock_format(text: str) -> dict[str, str]:
    """
    Look for fenced code blocks where the filename appears either:
      - in the fence info string:   ```tsx src/App.tsx
      - in the line just above the fence (heading, bold, or comment style)
    """
    files: dict[str, str] = {}
    lines = text.split("\n")
    i = 0
    pending_filename: str | None = None
    pending_line_idx: int = -1
    while i < len(lines):
        line = lines[i]
        fence_match = re.match(r"^\s*```([\w+\-]*)\s*(.*)$", line)
        if fence_match:
            rest = fence_match.group(2).strip()
            fn_in_fence = _extract_filename(rest)
            filename = fn_in_fence or pending_filename
            pending_filename = None
            content_lines: list[str] = []
            i += 1
            # MINOR-4: closing fence accepts optional trailing language tag or whitespace
            while i < len(lines) and not re.match(r"^\s*```\w*\s*$", lines[i]):
                content_lines.append(lines[i])
                i += 1
            i += 1  # skip closing fence
            if filename:
                files[filename] = "\n".join(content_lines).rstrip()
            continue

        fn_hint = _extract_filename(line)
        if fn_hint:
            pending_filename = fn_hint
            pending_line_idx = i
        elif line.strip():
            if pending_filename and (i - pending_line_idx) > 2:
                pending_filename = None
        i += 1
    return files


def _extract_filename(s: str) -> str | None:
    """Pull a path-looking token out of a line (or None if none looks plausible)."""
    s = s.strip().strip("*#<>:").strip()
    s = re.sub(r"^(file|filename|path)\s*[:=]\s*", "", s, flags=re.IGNORECASE)
    s = s.rstrip(":")
    m = _FILENAME_HINT.search(s)
    if not m:
        return None
    candidate = m.group(0)
    if len(candidate) > 80 or candidate.count(" ") > 0:
        return None
    return candidate


def format_files_for_prompt(files: dict[str, str]) -> str:
    """Serialize files dict into the ==== FILE ==== format for LLM prompts.

    MINOR-3: Warns when the serialized codebase is large enough to risk
    overflowing smaller model context windows.
    """
    result = "\n\n".join(
        f"==== FILE: {n} ====\n{c}\n==== END FILE ===="
        for n, c in files.items()
    )
    if len(result) > PROMPT_SIZE_WARN_CHARS:
        log.warning(
            "  ⚠️  Codebase prompt is %d chars — may approach context window limits "
            "on smaller models. Consider reducing file count.",
            len(result),
        )
    return result


def _validate_filepath(project_dir: Path, filename: str) -> Path:
    """
    Validate that the filename resolves inside project_dir,
    and only has an allowed extension.
    Raises ValueError on path traversal or disallowed extension.
    """
    resolved = (project_dir / filename).resolve()
    project_resolved = project_dir.resolve()
    if not str(resolved).startswith(str(project_resolved) + os.sep) and resolved != project_resolved:
        raise ValueError(
            f"Path traversal blocked — '{filename}' resolves outside project dir"
        )
    ext = resolved.suffix.lower()
    if ext and ext not in ALLOWED_FILE_EXTENSIONS:
        raise ValueError(
            f"Disallowed file extension '{ext}' for '{filename}'. "
            f"Allowed: {sorted(ALLOWED_FILE_EXTENSIONS)}"
        )
    return resolved


# D10 fix: root files that must NOT end up inside src/
_ROOT_ONLY_FILES = frozenset({
    "index.html", "vite.config.ts", "vite.config.js",
    "tsconfig.json", "tsconfig.node.json",
    "tailwind.config.js", "postcss.config.js", "package.json",
    "package-lock.json", ".eslintrc.cjs", ".eslintrc.js",
})

def write_to_disk(project_dir: Path, files: dict[str, str]) -> None:
    (project_dir / "src").mkdir(exist_ok=True, parents=True)
    for filename, content in files.items():
        # D10: redirect misplaced root files (e.g. LLM outputs "src/index.html")
        basename = Path(filename).name
        if basename in _ROOT_ONLY_FILES and filename != basename:
            log.warning(
                "  D10: LLM placed '%s' inside a subdirectory — "
                "redirecting to project root as '%s'.", filename, basename
            )
            filename = basename
        try:
            fp = _validate_filepath(project_dir, filename)
        except ValueError as e:
            log.warning("  Skipping file '%s': %s", filename, e)
            continue
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")


def enforce_pinned_deps(files: dict[str, str]) -> dict[str, str]:
    """Strip version range prefixes from package.json dependencies.

    LOGICAL-8: Handles ^, ~, >=, >, <=, <, *, x, workspace: ranges in addition
    to the previously handled ^ and ~ cases.
    """
    if "package.json" not in files:
        return files
    try:
        pkg = json.loads(files["package.json"])
    except json.JSONDecodeError:
        return files

    _range_prefix = re.compile(
        r"^(workspace:[~^]?|[><=~^]+\s*)"
    )

    for section in ("dependencies", "devDependencies", "peerDependencies"):
        deps = pkg.get(section)
        if isinstance(deps, dict):
            pinned: dict[str, Any] = {}
            for k, v in deps.items():
                if not isinstance(v, str):
                    pinned[k] = v
                    continue
                stripped = _range_prefix.sub("", v).strip()
                # Treat bare "*" or "x" as unpinnable — leave as-is and warn
                if stripped in ("*", "x", ""):
                    log.warning(
                        "  ⚠️  Cannot pin '%s': '%s' has no concrete version.", k, v
                    )
                    pinned[k] = v
                else:
                    pinned[k] = stripped
            pkg[section] = pinned

    files["package.json"] = json.dumps(pkg, indent=2)
    return files


def print_error_block(label: str, text: str, max_lines: int = ERROR_DISPLAY_MAX_LINES) -> None:
    """Print an error/output block to the terminal in a visible format."""
    text = (text or "").strip()
    if not text:
        log.info("  ⚠️  %s: (no output captured)", label)
        return
    lines = text.splitlines()
    truncated = len(lines) > max_lines
    shown = lines[-max_lines:] if truncated else lines
    log.info("\n  ━━━ %s ━━━", label)
    if truncated:
        log.info("  (showing last %d of %d lines)", max_lines, len(lines))
    for line in shown:
        log.info("  | %s", line)
    log.info("  ━━━ end %s ━━━\n", label)


# llm_call is imported from backend.viz_generator.llm above.


# ─────────────────────────────────────────────────────────────
# TARGETED-PATCH HELPERS  —  minimise tokens on retry loops
# ─────────────────────────────────────────────────────────────
#
# The original loops sent the FULL codebase back to the LLM every retry.
# For a 9-file project this is 30-60K input tokens × N retries — runaway cost.
#
# These helpers select ONLY the files relevant to the current error and ask
# the LLM to return MINIMAL surgical patches, not the entire codebase.

# Map common error keywords → likely culprit files. Used to narrow the
# context window before sending a fix prompt.
_ERROR_FILE_HINTS: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"cannot find module ['\"]([^'\"]+)['\"]", re.I), []),
    (re.compile(r"tailwind|postcss|@tailwind", re.I),                    ["tailwind.config.js", "postcss.config.js", "src/index.css"]),
    (re.compile(r"vite|defineConfig|plugin-react", re.I),                ["vite.config.ts"]),
    (re.compile(r"tsconfig|TS\d{4}|TS6310|TS5055", re.I),              ["tsconfig.json", "tsconfig.node.json"]),
    (re.compile(r"package\.json|npm err|peer dep|ERESOLVE", re.I),      ["package.json"]),
    (re.compile(r"index\.html|<script|<head>|module entry", re.I),      ["index.html", "src/main.tsx"]),
    (re.compile(r"createRoot|ReactDOM|StrictMode", re.I),                ["src/main.tsx"]),
    (re.compile(r"useAnimation|setInterval|stepIdx", re.I),              ["src/hooks/useAnimation.ts", "src/App.tsx"]),
    (re.compile(r"useStore|zustand", re.I),                              ["src/store/useStore.ts"]),
    (re.compile(r"\.css|@tailwind|--accent|glass-panel|mesh-bg", re.I), ["src/index.css"]),
]


def select_relevant_files(
    files: dict[str, str],
    error_log: str,
    always_include: tuple[str, ...] = ("package.json",),
    max_files: int = 5,
) -> dict[str, str]:
    """Pick the subset of files most likely to contain the bug for the given error.

    Strategy:
      1. Always include `always_include` (package.json so the LLM sees deps).
      2. Walk error keyword regex table; add any matched file that exists.
      3. Walk explicit "Cannot find module 'X'" mentions; add X if it's in the codebase.
      4. Walk filenames mentioned literally in the error (e.g. "src/App.tsx:42").
      5. Cap at max_files. If nothing matched, fall back to App.tsx + main.tsx + package.json.
    """
    selected: dict[str, str] = {}

    # 1. Always include
    for name in always_include:
        if name in files:
            selected[name] = files[name]

    # 2. Keyword regex hints
    for pattern, candidates in _ERROR_FILE_HINTS:
        if pattern.search(error_log):
            for c in candidates:
                if c in files and c not in selected:
                    selected[c] = files[c]
                    if len(selected) >= max_files:
                        return selected

    # 3. "Cannot find module 'X'" — try to resolve X
    for m in re.finditer(r"cannot find module ['\"]([^'\"]+)['\"]", error_log, re.I):
        modspec = m.group(1).lstrip("./")
        for fname in files:
            if fname.endswith(modspec) or fname.endswith(modspec + ".tsx") or fname.endswith(modspec + ".ts"):
                if fname not in selected:
                    selected[fname] = files[fname]

    # 4. Literal filename mentions in the error log.
    # IMPORTANT: use word-boundary regex anchored to either the path or the basename,
    # NOT a plain substring match — otherwise "node.json" matches inside
    # "tsconfig.node.json" and produces phantom selections.
    for fname in files:
        if fname in selected:
            continue
        # Build a regex that matches the full filename (or just its basename) at a
        # path boundary: start of string, whitespace, slash, quote, or colon.
        basename = Path(fname).name
        boundary = r"(?:^|[\s/\\\"':])"
        # Escape both forms — try full path first, then basename
        full_pat = boundary + re.escape(fname) + r"(?:[\s:,\"']|$)"
        base_pat = boundary + re.escape(basename) + r"(?:[\s:,\"']|$)"
        if re.search(full_pat, error_log) or re.search(base_pat, error_log):
            selected[fname] = files[fname]
            if len(selected) >= max_files:
                break

    # 5. Sensible fallback
    if not selected or "src/App.tsx" not in selected:
        for fallback in ("src/App.tsx", "src/main.tsx", "package.json"):
            if fallback in files and fallback not in selected:
                selected[fallback] = files[fallback]

    # Cap
    if len(selected) > max_files:
        selected = dict(list(selected.items())[:max_files])

    return selected


def format_files_compact(files: dict[str, str]) -> str:
    """Same delimiter format but used for SUBSET file lists in patch prompts."""
    return "\n\n".join(
        f"==== FILE: {n} ====\n{c}\n==== END FILE ===="
        for n, c in files.items()
    )


def merge_patches(original: dict[str, str], patches: dict[str, str]) -> dict[str, str]:
    """Merge LLM-returned partial files back into the full codebase."""
    merged = dict(original)
    for fname, content in patches.items():
        if fname in merged:
            log.info("  [Patch] Updating %s (%d -> %d chars)",
                     fname, len(merged[fname]), len(content))
        else:
            log.info("  [Patch] Adding new file %s (%d chars)", fname, len(content))
        merged[fname] = content
    return merged


# ─────────────────────────────────────────────────────────────
# STEP 1 — GENERATION
# ─────────────────────────────────────────────────────────────

def generate_draft_code(
    topic: str,
    pattern_name: str,
    pattern: dict[str, Any],
    project_dir: Path,
) -> dict[str, str]:
    log.info("\n[Step 1] Generating '%s' as pattern '%s'...", topic, pattern_name)

    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    user_prompt = f"""Generate a complete, working Vite + React visualisation for:

TOPIC: "{topic}"

Requirements:
- Follow the Universal State-Machine Contract from the system prompt.
- Follow the '{pattern_name}' visualization pattern guidelines.
- The visualization must be educational: a student should understand the concept
  by watching it animate step by step.
- Default to a non-trivial example (e.g. 8-12 elements for algorithms,
  3-4 layers for neural nets, 5-6 nodes for graphs).
- Include a brief text explanation of what is happening at the current step.

Required files to produce (ALL must be present):
  src/App.tsx  src/main.tsx  src/index.css
  index.html  (at project root — NOT src/index.html)
  vite.config.ts  tsconfig.json  tsconfig.node.json
  tailwind.config.js  postcss.config.js  package.json
  src/hooks/useAnimation.ts  src/store/useStore.ts

Also produce these component files (src/components/):
  Sidebar.tsx  PageShell.tsx  Panel.tsx  AnimControls.tsx  StepExplainer.tsx
"""

    raw = llm_call(
        [
            {"role": "system", "content": system},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=1,
        max_tokens=LLM_DEFAULT_MAX_TOKENS,
        step_label="step1_generate",
    )

    files = parse_files(raw)
    if not files:
        # MINOR-2: dump to project_dir (which exists) with /tmp fallback
        dump_path: Path
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            dump_path = project_dir / "last_llm_output.txt"
        except OSError:
            dump_path = Path("/tmp/last_llm_output.txt")
        dump_path.write_text(raw, encoding="utf-8")
        log.error("[ERROR] No parseable files from LLM.")
        log.error("  Raw output saved to: %s", dump_path)
        log.error("  Output length: %d chars", len(raw))
        log.error("  First 500 chars of response:")
        log.error("  %s", raw[:500].replace("\n", "\n  "))
        sys.exit(1)

    return enforce_pinned_deps(files)


# ─────────────────────────────────────────────────────────────
# STEP 2 — BUILD ERROR LOOP
# ─────────────────────────────────────────────────────────────

def _run_npm_install(project_dir: Path) -> bool:
    """Run npm install with timeout. Returns True on success."""
    try:
        install_r = subprocess.run(
            ["npm", "install"], cwd=project_dir,
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("  ❌ npm install timed out after %ds.", SUBPROCESS_TIMEOUT)
        return False

    if install_r.returncode != 0:
        log.info("  ❌ npm install failed.")
        print_error_block("npm install error", install_r.stderr or install_r.stdout)
        return False

    warn_text = (install_r.stderr or "").strip()
    if warn_text and ("warn" in warn_text.lower() or "vulnerab" in warn_text.lower()):
        log.info("  ⚠️  npm install completed with warnings (first lines):")
        for line in warn_text.splitlines()[:5]:
            log.info("    %s", line)
    return True


def _run_npm_build(project_dir: Path) -> subprocess.CompletedProcess[str] | None:
    """Run npm build with timeout. Returns CompletedProcess or None on timeout."""
    try:
        return subprocess.run(
            ["npm", "run", "build"], cwd=project_dir,
            capture_output=True, text=True,
            timeout=SUBPROCESS_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("  ❌ npm run build timed out after %ds.", SUBPROCESS_TIMEOUT)
        return None


def build_error_loop(
    topic: str,
    pattern: dict[str, Any],
    project_dir: Path,
    files: dict[str, str],
) -> tuple[dict[str, str], bool]:
    """Run npm install then iteratively fix build errors using TARGETED PATCHES.

    Token-efficiency strategy:
      - On each retry, send only the files most likely to contain the bug
        (selected by error-keyword regex matching), not the full codebase.
      - Ask the LLM to output ONLY the changed files, not all files.
      - Memory of previous attempts is maintained via a running summary
        of "what was tried and failed" so the LLM doesn't repeat itself.
    """
    status("STEP 2 / 4", "BUILD ERROR LOOP")
    log.info("[Step 2] npm install...")

    if not _run_npm_install(project_dir):
        log.error("  ❌ npm install failed — skipping build loop.")
        return files, False

    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    # Memory: a short summary of past failed attempts so the LLM doesn't repeat itself
    attempt_history: list[str] = []

    for attempt in range(1, BUILD_RETRIES + 1):
        log.info("  [Build %d/%d]...", attempt, BUILD_RETRIES)
        r = _run_npm_build(project_dir)
        if r is None:
            return files, False
        if r.returncode == 0:
            log.info("  ✅ Build OK")
            return files, True

        error_log = (r.stderr or r.stdout).strip()
        log.info("  ❌ Build failed.")
        print_error_block(f"Build attempt {attempt} error", error_log)

        # ── Select only the files relevant to THIS error ──
        relevant = select_relevant_files(files, error_log, max_files=5)
        log.info("  → Sending %d relevant file(s) to LLM (out of %d total): %s",
                 len(relevant), len(files), ", ".join(relevant.keys()))

        # ── Build prompt with attempt memory but no full codebase ──
        history_block = ""
        if attempt_history:
            history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat these — they did not fix the issue):\n" + \
                            "\n".join(f"  - Attempt {i+1}: {h}" for i, h in enumerate(attempt_history))

        fix_prompt = f"""Vite build failed for "{topic}".

<current_build_error>
{error_log[:3000]}
</current_build_error>
{history_block}

You are seeing only the files most likely to contain the bug.
Apply the MINIMUM change needed to fix the error.
Do NOT rewrite working code. Do NOT touch files you do not change.

Output ONLY the files you actually modify, in this format:
==== FILE: path/to/changed.ts ====
[full new content of THAT FILE ONLY]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed and why>

<relevant_files>
{format_files_compact(relevant)}
</relevant_files>
"""
        raw = llm_call(
            [
                {"role": "system", "content": system},
                {"role": "user",   "content": fix_prompt},
            ],
            temperature=1,
            max_tokens=LLM_FIX_MAX_TOKENS,
            step_label=f"step2_build_fix:attempt_{attempt}",
        )

        # Capture the LLM's own one-line summary for the next attempt's memory
        summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
        if summary_match:
            attempt_history.append(summary_match.group(1).strip())
        else:
            # Synthesize a short summary from the error itself
            attempt_history.append(error_log.splitlines()[0][:140] if error_log else "no summary")

        patched = parse_files(raw)
        if not patched:
            log.info("  ⚠️  LLM returned no parseable file blocks.")
            break

        # ── Merge and write only the patched files ──
        patched = enforce_pinned_deps(patched)
        files = merge_patches(files, patched)
        write_to_disk(project_dir, patched)

        if "package.json" in patched:
            log.info("  package.json changed — re-running npm install...")
            if not _run_npm_install(project_dir):
                log.error("  ❌ npm install failed after package.json update.")
                return files, False

    log.info("  ⚠️  Build loop exhausted.")
    return files, False


# ─────────────────────────────────────────────────────────────
# STEP 3 — SEMANTIC RUNTIME VALIDATION
# ─────────────────────────────────────────────────────────────

def _playwright_available() -> bool:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "playwright", "--version"],
            capture_output=True, text=True,
            timeout=15,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pick_free_port(start: int = 5100, end: int = 5900) -> int:
    """CRITICAL-2: Pick a random free port to avoid EADDRINUSE conflicts."""
    for _ in range(20):
        port = random.randint(start, end)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("Could not find a free port in range %d-%d" % (start, end))


def _wait_for_server(port: int, timeout: float = PREVIEW_STARTUP_WAIT) -> bool:
    """Poll until the preview server responds, up to `timeout` seconds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def run_semantic_checks(
    project_dir: Path,
    pattern_tests: list[dict[str, str]],
    topic_slug: str = "viz",
) -> list[dict[str, Any]]:
    """
    Runs universal + pattern-specific tests.
    Returns list of failure dicts: [{ description, fix_hint }]

    CRITICAL-2: port is randomised per call.
    CRITICAL-3: Popen stdout/stderr use DEVNULL to prevent pipe buffer deadlock.
    LOGICAL-10: screenshot named {topic_slug}_screenshot.png.
    """
    if not _playwright_available():
        log.info("  Playwright not found — skipping runtime checks.")
        log.info("  Install: pip install playwright && playwright install chromium")
        return [{
            "description": "Playwright not available",
            "fix_hint": "Install Playwright: pip install playwright && playwright install chromium",
            "infrastructure": True,
        }]

    # CRITICAL-2: randomise port
    try:
        port = _pick_free_port()
    except RuntimeError as e:
        return [{"description": str(e), "fix_hint": "Free a port in range 5100-5900.", "infrastructure": True}]

    log.info("  Starting vite preview on port %d...", port)

    # CRITICAL-3: DEVNULL prevents pipe buffer deadlock from accumulated vite output
    proc = subprocess.Popen(
        ["npm", "run", "preview", "--", "--port", str(port)],
        cwd=project_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if not _wait_for_server(port, timeout=PREVIEW_STARTUP_WAIT):
        if proc.poll() is not None:
            log.info("  ❌ Vite preview exited with code %d during startup.", proc.returncode)
        else:
            proc.kill()
            proc.wait()
            log.info("  ❌ Vite preview did not respond within %ds.", PREVIEW_STARTUP_WAIT)
        return [{
            "description": "Preview server failed to start",
            "fix_hint": "Vite preview process did not respond in time. Check the build output.",
            "infrastructure": True,
        }]

    failures: list[dict[str, Any]] = []

    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1024, "height": 768})

            console_errors: list[str] = []
            page.on("console", lambda m: console_errors.append(m.text)
                    if m.type == "error" else None)

            page.goto(f"http://localhost:{port}", timeout=PREVIEW_PAGE_TIMEOUT)
            page.wait_for_load_state("networkidle", timeout=PREVIEW_IDLE_TIMEOUT)
            time.sleep(1)

            load_console_errors = list(console_errors)

            if load_console_errors:
                failures.append({
                    "description": "JS console errors on load",
                    "fix_hint": "Console errors:\n" +
                                "\n".join(f"  - {e}" for e in load_console_errors[:15]),
                })

            all_tests = UNIVERSAL_TESTS + [TAILWIND_TOKEN_TEST] + pattern_tests
            for test in all_tests:
                ok: bool
                try:
                    ok = page.evaluate(test["check"])
                except Exception as exc:
                    ok = False
                    # CRITICAL-4: use a separate dict; do NOT rebind `test`
                    augmented = dict(test)
                    augmented["fix_hint"] = augmented["fix_hint"] + f"\n(Eval error: {exc})"
                    log.debug("  Test eval error for '%s': %s", test["description"], exc)
                    failures.append({
                        "description": augmented["description"],
                        "fix_hint": augmented["fix_hint"],
                    })
                    continue
                if not ok:
                    failures.append({
                        "description": test["description"],
                        "fix_hint": test["fix_hint"],
                    })

            for itest in UNIVERSAL_INTERACTION_TESTS:
                try:
                    before = page.evaluate(itest["js_before"])
                    page.evaluate(itest["action"])
                    # MINOR-5: raised to 1.2s for slow framer-motion animations
                    time.sleep(INTERACTION_SETTLE)
                    after = page.evaluate(itest["js_after"])
                    ok = _evaluate_assertion(itest["assert"], before, after)
                except Exception as exc:
                    ok = False
                    log.debug("  Interaction test error for '%s': %s", itest["description"], exc)
                if not ok:
                    failures.append({
                        "description": itest["description"],
                        "fix_hint": itest["fix_hint"],
                    })

            # LOGICAL-10: topic-specific screenshot name prevents overwriting
            shot_path = project_dir / f"{topic_slug}_screenshot.png"
            page.screenshot(path=str(shot_path))
            log.info("  📸 Screenshot: %s", shot_path)

            browser.close()

    except Exception as e:
        failures.append({
            "description": "Playwright error",
            "fix_hint": str(e),
            "infrastructure": True,
        })
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    return failures


def _evaluate_assertion(assertion: str, before: Any, after: Any) -> bool:
    """Safe assertion lookup — no eval()."""
    _SAFE_ASSERTIONS: dict[str, Any] = {
        "before !== after": lambda b, a: b != a,
        "before != after":  lambda b, a: b != a,
        "before === after": lambda b, a: b == a,
        "before == after":  lambda b, a: b == a,
    }
    handler = _SAFE_ASSERTIONS.get(assertion.strip())
    if handler is None:
        log.warning(
            "  Unknown assertion '%s' — treating as failure. "
            "Add it to _SAFE_ASSERTIONS if it's intentional.",
            assertion,
        )
        return False
    return handler(before, after)


def runtime_fix_loop(
    topic: str,
    pattern_name: str,
    pattern: dict[str, Any],
    project_dir: Path,
    files: dict[str, str],
    topic_slug: str,
) -> dict[str, str]:
    """Semantic runtime validation loop with TARGETED PATCHES.

    Same token-efficiency strategy as build_error_loop:
      - On rebuild failure: send only files relevant to the build error.
      - On semantic test failure: send only files relevant to the failing tests.
      - Maintain attempt history so the LLM doesn't repeat itself.
    """
    status("STEP 3 / 4", "SEMANTIC RUNTIME VALIDATION")
    log.info("[Step 3] Semantic runtime validation...")
    system = UNIVERSAL_SYSTEM_PROMPT + "\n\n" + pattern["prompt_extra"]

    attempt_history: list[str] = []

    for attempt in range(1, RUNTIME_RETRIES + 1):
        r = _run_npm_build(project_dir)
        if r is None:
            log.info("  ⚠️  Build timed out.")
            break

        # ── Rebuild failure path ──
        if r.returncode != 0:
            log.info("  ⚠️  Rebuild failed (attempt %d) — sending error to LLM.", attempt)
            build_error_log = (r.stderr or r.stdout).strip()
            print_error_block(f"Rebuild attempt {attempt} error", build_error_log)

            relevant = select_relevant_files(files, build_error_log, max_files=5)
            log.info("  → Sending %d relevant file(s): %s",
                     len(relevant), ", ".join(relevant.keys()))

            history_block = ""
            if attempt_history:
                history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat):\n" + \
                    "\n".join(f"  - {h}" for h in attempt_history)

            build_fix_prompt = f"""The "{topic}" visualisation failed to rebuild during runtime validation.

<current_build_error>
{build_error_log[:3000]}
</current_build_error>
{history_block}

Apply the MINIMUM change needed. Do NOT rewrite working code.

Output ONLY changed files in this format:
==== FILE: path ====
[full new content of that file only]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed>

<relevant_files>
{format_files_compact(relevant)}
</relevant_files>
"""
            raw = llm_call(
                [
                    {"role": "system", "content": system},
                    {"role": "user",   "content": build_fix_prompt},
                ],
                temperature=1,
                max_tokens=LLM_FIX_MAX_TOKENS,
                step_label=f"step3_rebuild_fix:attempt_{attempt}",
            )

            summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
            attempt_history.append(
                summary_match.group(1).strip() if summary_match
                else (build_error_log.splitlines()[0][:140] if build_error_log else "no summary")
            )

            patched = parse_files(raw)
            if not patched:
                log.info("  ⚠️  LLM returned no files for build fix.")
                break
            patched = enforce_pinned_deps(patched)
            files = merge_patches(files, patched)
            write_to_disk(project_dir, patched)
            if "package.json" in patched:
                log.info("  package.json changed — re-running npm install...")
                if not _run_npm_install(project_dir):
                    break
            continue

        # ── Semantic test path ──
        failures = run_semantic_checks(project_dir, pattern["tests"], topic_slug=topic_slug)

        if not failures:
            log.info("  ✅ All checks passed (attempt %d).", attempt)
            return files

        infra_failures = [f for f in failures if f.get("infrastructure")]
        if infra_failures:
            log.info("\n  ⚠️  Infrastructure error — cannot validate runtime:")
            for f in infra_failures:
                log.info("    - %s: %s", f["description"], f["fix_hint"].splitlines()[0])
            log.info("  Skipping LLM fix loop (cannot fix infra failures from prompts).")
            log.info("  Most likely fix: run  %s -m playwright install chromium", sys.executable)
            return files

        failure_report = "\n".join(
            f"FAIL [{i+1}]: {f['description']}\n  -> {f['fix_hint']}"
            for i, f in enumerate(failures)
        )
        log.info("\n  ❌ %d failure(s):\n%s", len(failures), failure_report)

        # ── Pick relevant files based on the failure descriptions ──
        # Concatenate descriptions + hints so select_relevant_files can match keywords
        failure_text = "\n".join(f["description"] + " " + f["fix_hint"] for f in failures)

        # For semantic failures, App.tsx is almost always involved
        relevant = select_relevant_files(files, failure_text, max_files=5)
        if "src/App.tsx" not in relevant and "src/App.tsx" in files:
            relevant["src/App.tsx"] = files["src/App.tsx"]
        log.info("  → Sending %d relevant file(s): %s",
                 len(relevant), ", ".join(relevant.keys()))

        history_block = ""
        if attempt_history:
            history_block = "\n\nPREVIOUS ATTEMPTS (do NOT repeat):\n" + \
                "\n".join(f"  - {h}" for h in attempt_history)

        fix_prompt = f"""The "{topic}" visualisation (pattern: {pattern_name}) has runtime failures:

{failure_report}

These are SEMANTIC/LOGIC failures, not syntax errors.

Diagnosis guide:
- "complete at step 0": done state initialised wrong — never true at start.
- "Step Forward no change": render doesn't read from steps[stepIdx].
- "0 or 1 total steps": step-generation loop is broken.
- "no circles/nodes": SVG structure missing.
- "console errors": unhandled exception — check the message.
- "glass-panel": tailwind config tokens not resolving.
{history_block}

Fix ONLY what the failure report lists. Apply the MINIMUM change needed.

Output ONLY changed files:
==== FILE: path ====
[full new content of that file only]
==== END FILE ====

After your file blocks, on a single line:
SUMMARY: <one sentence describing what you changed>

<relevant_files>
{format_files_compact(relevant)}
</relevant_files>
"""
        raw = llm_call(
            [
                {"role": "system", "content": system},
                {"role": "user",   "content": fix_prompt},
            ],
            temperature=1,
            max_tokens=LLM_FIX_MAX_TOKENS,
            step_label=f"step3_semantic_fix:attempt_{attempt}",
        )

        summary_match = re.search(r"^SUMMARY:\s*(.+)$", raw, re.MULTILINE)
        attempt_history.append(
            summary_match.group(1).strip() if summary_match
            else (failures[0]["description"][:140] if failures else "no summary")
        )

        patched = parse_files(raw)
        if not patched:
            log.info("  ⚠️  LLM returned no files.")
            break
        patched = enforce_pinned_deps(patched)
        files = merge_patches(files, patched)
        write_to_disk(project_dir, patched)
        log.info("  → Patched %d file(s).", len(patched))

        if "package.json" in patched:
            log.info("  package.json changed — re-running npm install...")
            if not _run_npm_install(project_dir):
                break

    log.info("  ⚠️  Runtime loop exhausted.")
    return files


# ─────────────────────────────────────────────────────────────
# STEP 4 — OPTIONAL DESIGN POLISH
# ─────────────────────────────────────────────────────────────

def design_polish_pass(
    topic: str,
    pattern_name: str,
    project_dir: Path,
    files: dict[str, str],
) -> dict[str, str]:
    log.info("\n[Step 4] Design polish pass...")

    polish_prompt = f"""Polish the visual design of this "{topic}" visualisation ({pattern_name} pattern).

Design direction:
- Dark background: #0a0e1a. Card surfaces: #111827.
- Primary accent: #6366f1 (indigo). Active/highlight: #f59e0b (amber). Done/sorted: #10b981 (emerald).
- Monospace font for values/numbers, clean sans-serif for labels.
- Smooth CSS transitions (150-200ms) on all color and size changes.
- Pill-shaped control buttons with hover states (scale 1.02, brightness 1.1).
- Responsive layout: bars/nodes sized with % or SVG viewBox, not fixed px.
- Step message area with a subtle border and monospace font.

DO NOT modify: steps[] generation, stepIdx logic, any algorithm/math code, data structures.
Only touch: className attributes, inline styles, SVG colors/sizes, layout wrapper divs, transitions.

<codebase>
{format_files_for_prompt(files)}
</codebase>

Output FULL content of every changed file.
"""

    raw = llm_call(
        [
            {"role": "system", "content": POLISH_SYSTEM_PROMPT},
            {"role": "user",   "content": polish_prompt},
        ],
        temperature=1,
        step_label="step4_polish",
    )

    fixed = parse_files(raw)
    if fixed:
        files.update(fixed)
        write_to_disk(project_dir, fixed)
        log.info("  ✅ Polish applied (%d file(s)).", len(fixed))
    else:
        log.info("  ⚠️  No files returned.")
    return files


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Universal Visualization Builder (v4.2) — works for any CS/ML/math topic"
    )
    parser.add_argument("--topic", required=True,
                        help="Topic to visualise (e.g. 'gradient descent', 'AVL tree', 'TCP handshake')")
    parser.add_argument("--polish", action="store_true",
                        help="Run design polish pass after validation")
    args = parser.parse_args()

    _init_client()

    log.info("=" * 60)
    log.info("  Universal Viz Agent v4.3 — '%s'", args.topic)
    log.info("=" * 60)

    log.info("\n[Classify] Identifying visualization pattern...")
    pattern_name, pattern = classify_topic(args.topic)

    safe_name = re.sub(r"[^a-z0-9]+", "-", args.topic.lower()).strip("-") + "-viz"
    project_dir = Path.cwd() / safe_name
    topic_slug = safe_name

    log.info("[Config] Token budget: %d  Model: %s  Provider: %s",
             TOKEN_BUDGET, MODEL_NAME, LLM_PROVIDER)

    # 1. Generate
    status("STEP 1 / 4", "GENERATE DRAFT CODE")
    files = generate_draft_code(args.topic, pattern_name, pattern, project_dir)
    write_to_disk(project_dir, files)

    # 2. Build loop
    files, build_ok = build_error_loop(args.topic, pattern, project_dir, files)

    # 3. Semantic runtime loop
    if build_ok:
        files = runtime_fix_loop(
            args.topic, pattern_name, pattern, project_dir, files, topic_slug=topic_slug
        )
    else:
        log.info("\n[Step 3] Skipping runtime validation — build did not succeed.")

    # 4. Polish (optional)
    if args.polish:
        if not build_ok:
            log.info("\n[Step 4] Skipping polish — build did not succeed.")
        else:
            status("STEP 4 / 4", "DESIGN POLISH")
            files = design_polish_pass(args.topic, pattern_name, project_dir, files)
            polish_r = _run_npm_build(project_dir)
            if polish_r is None:
                log.info("  ❌ Post-polish rebuild timed out.")
            elif polish_r.returncode != 0:
                log.info("  ❌ Post-polish rebuild failed.")
                print_error_block("Post-polish build error", polish_r.stderr or polish_r.stdout)
            else:
                log.info("  ✅ Post-polish build OK")

    # Final summary
    log.info("\n" + "=" * 60)
    log.info("  DONE!")
    log.info("  Topic:   %s", args.topic)
    log.info("  Pattern: %s", pattern_name)
    log.info("  Project: %s", project_dir)
    log.info("\n  Run:  cd %s && npm run dev", safe_name)
    shot = project_dir / f"{topic_slug}_screenshot.png"
    if shot.exists():
        log.info("  Screenshot: %s", shot)
    log.info("=" * 60)

    # ── Token usage / cost summary ──
    token_tracker.print_summary()


if __name__ == "__main__":
    main()
