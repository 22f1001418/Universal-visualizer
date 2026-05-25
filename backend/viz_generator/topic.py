"""Topic classification for the viz generator.

The first LLM call in the pipeline. Maps a free-text topic + brief into
a structured (topic_kind, metadata) tuple that the subsequent phases use
to pick prompts and templates.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from backend.viz_generator.llm import llm_call

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# TOPIC PATTERNS AND PRIORITY
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
