"""The two LLM agents driven by this orchestrator.

  Agent A (topic_extraction_agent):
    Reads a HackMD lecture script and returns the 3-7 places where a viz
    would meaningfully help a learner. For each, returns:
      - section heading
      - verbatim sentence after which the viz should be embedded
      - one-line "why visual helps" justification
      - audience difficulty (beginner / intermediate / advanced)

  Agent B (viz_suggestion_agent):
    For one extracted topic, returns 5 distinct visualization approaches.
    Each approach has a beginner_benefit AND intermediate_benefit so the
    instructor can choose based on their cohort.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from llm_client import llm_call, LLMTask
from models import (
    ExtractedTopic,
    TopicExtractionResult,
    VizSuggestion,
    VizSuggestionsResult,
)

logger = logging.getLogger("hackmd-orch.agents")


# ──────────────────────────────────────────────────────
# JSON safe-extract helper — same pattern from image_gen_main
# ──────────────────────────────────────────────────────

def _parse_json_safe(raw: str) -> dict:
    """Tolerant JSON parse: strips markdown fences, then falls back to regex."""
    raw = raw.strip()
    raw = re.sub(r"```json\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```\s*", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as primary:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("[JSON] parse failed. Raw head: %s", raw[:200].replace("\n", " "))
        raise primary


# ──────────────────────────────────────────────────────
# Surrounding-context extractor
# ──────────────────────────────────────────────────────

def _extract_context(script_text: str, anchor: str, span_chars: int = 500) -> str:
    """Find the anchor sentence in the script and return ~span_chars around it."""
    if not anchor:
        return ""
    idx = script_text.find(anchor)
    if idx < 0:
        # try a softer match — first 60 chars
        anchor_head = anchor[:60]
        idx = script_text.find(anchor_head)
    if idx < 0:
        return ""
    start = max(0, idx - span_chars // 2)
    end = min(len(script_text), idx + len(anchor) + span_chars // 2)
    return script_text[start:end]


# ──────────────────────────────────────────────────────
# Agent A — Topic extraction
# ──────────────────────────────────────────────────────

_TOPIC_EXTRACTION_SYSTEM = """\
You are a senior curriculum designer specialising in beginner-friendly technical \
education. You read lecture scripts (markdown) and identify the places where adding \
an interactive visualization would meaningfully shorten the time it takes a beginner \
learner to grasp a concept.

You only flag a topic if the visual would add real value beyond what prose alone \
conveys — anything spatial, dynamic, multi-step, or transformation-based is a strong \
candidate. Skip topics that are well-served by a single sentence of plain English.
"""

_TOPIC_EXTRACTION_USER_TEMPLATE = """\
Track / Program: {track}
Script filename: {filename}

LECTURE SCRIPT (HackMD markdown):
\"\"\"
{script_text}
\"\"\"

TASK
====
Identify between 3 and 7 places in this script where adding an INTERACTIVE \
VISUALIZATION (not just an image) would help a learner — especially a beginner — \
understand the concept faster.

For each place, produce:
  - section: the markdown heading the topic appears under (verbatim, including \
the "##" prefix, e.g. "## How CNNs Work")
  - topic: 2-6 word descriptive name (e.g. "Convolutional Layer Operation")
  - embed_after_sentence: the EXACT sentence (verbatim from the script, including \
punctuation) after which the viz should be embedded. The sentence MUST appear \
character-for-character in the script.
  - why_visual_helps: one sentence on what makes this hard to grasp from text alone, \
and what the viz would solve.
  - audience_difficulty: "beginner" / "intermediate" / "advanced" — rank the \
typical learner's familiarity with this idea at this point in the script.

QUALITY RULES
=============
- Pick distinct concepts. Do not return 5 variants of the same idea.
- The embed_after_sentence must be a complete sentence (no trailing fragments).
- Prefer dynamic / multi-step / spatial concepts (e.g. backprop, sliding windows, \
sorting steps, traversals, gradient descent) over static facts.
- Skip purely textual definitions, bibliographic content, or already-visual sections \
(those that already include diagrams or links).
- The total list must be 3-7 entries.

Return STRICT JSON with this schema (no prose, no markdown fences):

{{
  "extraction_note": "short freeform note about what you found / skipped",
  "topics": [
    {{
      "section": "## How CNNs Work",
      "topic": "Convolutional Layer Operation",
      "embed_after_sentence": "The kernel slides across the input matrix, computing dot products at each position.",
      "why_visual_helps": "Beginners struggle to mentally simulate the kernel sweep; an animated slide makes the math tangible.",
      "audience_difficulty": "beginner"
    }}
  ]
}}
"""


def topic_extraction_agent(
    script_text: str,
    filename: str,
    track: str,
    job_id: str,
) -> TopicExtractionResult:
    """Run Agent A and return a parsed TopicExtractionResult."""
    user_prompt = _TOPIC_EXTRACTION_USER_TEMPLATE.format(
        track=track,
        filename=filename,
        script_text=script_text,
    )

    raw = llm_call(
        system_prompt=_TOPIC_EXTRACTION_SYSTEM,
        user_prompt=user_prompt,
        step_label="agent_A_topic_extraction",
        job_id=job_id,
        temperature=0.2,
        max_tokens=4096,
        task=LLMTask.AGENT_A_EXTRACT,
        json_mode=True,
    )
    data = _parse_json_safe(raw)

    raw_topics: list[dict[str, Any]] = data.get("topics", []) or []
    if not raw_topics:
        raise RuntimeError(
            "Topic extraction returned 0 topics. The script may be too short, "
            "already visual-rich, or the LLM rejected it. "
            "Try a longer / more conceptual section."
        )

    # Verify that each embed_after_sentence actually exists in the source.
    # If not, mark with surrounding_context="" and let the user fix manually.
    parsed: list[ExtractedTopic] = []
    for i, t in enumerate(raw_topics[:7], start=1):
        anchor = (t.get("embed_after_sentence") or "").strip()
        context = _extract_context(script_text, anchor)
        if not context:
            logger.warning(
                "[Agent A] Topic %d's anchor not found verbatim in source: %r",
                i, anchor[:80],
            )

        parsed.append(ExtractedTopic(
            id=f"topic_{i}",
            section=(t.get("section") or "Unknown section").strip(),
            topic=(t.get("topic") or f"Topic {i}").strip(),
            embed_after_sentence=anchor,
            why_visual_helps=(t.get("why_visual_helps") or "").strip(),
            audience_difficulty=t.get("audience_difficulty", "beginner"),
            surrounding_context=context,
        ))

    return TopicExtractionResult(
        script_name=filename,
        topics=parsed,
        extraction_note=(data.get("extraction_note") or "").strip(),
    )


# ──────────────────────────────────────────────────────
# Agent B — Viz suggestion
# ──────────────────────────────────────────────────────

_VIZ_SUGGESTION_SYSTEM = """\
You are an interactive-visualization architect for technical education. Given one \
specific topic from a lecture script, you propose five DIFFERENT visualization \
approaches that could be built as a small React + framer-motion + Tailwind app.

You always vary the angle — different metaphors, levels of abstraction, or interaction \
modes. You never return five rephrasings of the same idea. You always think about how \
each approach lands differently for a beginner versus an intermediate learner.
"""

_VIZ_SUGGESTION_USER_TEMPLATE = """\
TRACK: {track}
TOPIC: {topic}
SECTION: {section}
LEARNER DIFFICULTY: {difficulty}
WHY THIS NEEDS A VISUAL: {why}

SURROUNDING SCRIPT CONTEXT (~500 chars around the embed point):
\"\"\"
{context}
\"\"\"

TASK
====
Propose EXACTLY 5 distinct visualization approaches. Each should be plausibly \
buildable as a single-page React + Vite + Tailwind + framer-motion app with one or \
two animation controls (play / step / reset).

For EACH approach, produce:
  - title: 3-6 words (e.g. "Matrix slide animation", "Filter sweep heatmap")
  - approach: 2-4 sentences describing what the viz would show. Be SPECIFIC: \
mention concrete shapes, colors, controls, and step transitions.
  - beginner_benefit: one sentence — what a learner just meeting this concept gets out of it.
  - intermediate_benefit: one sentence — what a learner who already knows the basics gets.
  - complexity: "low" / "medium" / "high" — rough effort to build.

QUALITY RULES
=============
- Each approach must be visually + conceptually distinct from the others. No near-duplicates.
- Stay grounded in the specific section context above. Avoid generic stock viz ideas.
- For beginners: prefer concrete, narrative, slow-paced.
- For intermediate: prefer comparative, parameterized, edge-case-aware.
- Do NOT propose anything that requires 3D rendering or a backend.
- Do NOT propose video, audio, or AR/VR.

Return STRICT JSON, no prose:

{{
  "suggestions": [
    {{
      "title": "Matrix slide animation",
      "approach": "Show a 5x5 input grid in pale grey. Overlay a 3x3 kernel that slides across each valid position. At each step, draw the dot product as a side panel that fills in cell-by-cell.",
      "beginner_benefit": "Lets a beginner see the kernel as a literal moving window so the math is concrete.",
      "intermediate_benefit": "Adds toggles for stride and padding so the learner can compare same vs valid convolution.",
      "complexity": "low"
    }}
  ]
}}
"""


def viz_suggestion_agent(
    topic: ExtractedTopic,
    track: str,
    job_id: str,
) -> VizSuggestionsResult:
    user_prompt = _VIZ_SUGGESTION_USER_TEMPLATE.format(
        track=track,
        topic=topic.topic,
        section=topic.section,
        difficulty=topic.audience_difficulty,
        why=topic.why_visual_helps,
        context=topic.surrounding_context or "(no surrounding context — anchor not located)",
    )

    raw = llm_call(
        system_prompt=_VIZ_SUGGESTION_SYSTEM,
        user_prompt=user_prompt,
        step_label=f"agent_B_viz_suggest:{topic.id}",
        job_id=job_id,
        temperature=0.5,   # higher diversity for 5 distinct ideas
        max_tokens=3072,
        task=LLMTask.AGENT_B_SUGGEST,
        json_mode=True,
    )
    data = _parse_json_safe(raw)
    raw_suggestions: list[dict[str, Any]] = data.get("suggestions", []) or []

    if len(raw_suggestions) < 3:
        raise RuntimeError(
            f"Viz suggestion agent returned only {len(raw_suggestions)} suggestions "
            "(expected 5). Retrying may help."
        )

    parsed: list[VizSuggestion] = []
    for i, s in enumerate(raw_suggestions[:5], start=1):
        parsed.append(VizSuggestion(
            id=f"viz_{i}",
            title=(s.get("title") or f"Approach {i}").strip()[:80],
            approach=(s.get("approach") or "").strip(),
            beginner_benefit=(s.get("beginner_benefit") or "").strip(),
            intermediate_benefit=(s.get("intermediate_benefit") or "").strip(),
            complexity=s.get("complexity", "medium"),
        ))

    return VizSuggestionsResult(topic_id=topic.id, suggestions=parsed)


# ──────────────────────────────────────────────────────
# Helper — assemble final viz brief from suggestion + custom notes
# ──────────────────────────────────────────────────────

def _ascii_sanitize(s: str) -> str:
    """Replace common non-ASCII separators with plain ASCII so the topic string
    is safe to use as a filename slug downstream.

    Specifically:
      - em-dash (U+2014) and en-dash (U+2013) -> regular hyphen
      - curly quotes (U+201C..U+201F, U+2018..U+201B) -> ASCII " and '
      - non-breaking space and other unicode whitespace -> regular space
    """
    replacements = {
        "\u2014": "-",   # em-dash
        "\u2013": "-",   # en-dash
        "\u2212": "-",   # minus sign
        "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
        "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
        "\u00a0": " ",   # non-breaking space
        "\u2009": " ", "\u200a": " ", "\u200b": " ",  # thin / hair / zero-width
    }
    out = s
    for k, v in replacements.items():
        out = out.replace(k, v)
    return out


# How long the SHORT topic string can be. fixed_main_v6.py slugifies the
# entire --topic argument and uses it as a filesystem directory name. macOS
# and Linux both cap individual path components at 255 bytes; we leave a wide
# safety margin because the slug also gets a "-viz" suffix and screenshot
# filenames are appended on top.
SHORT_TOPIC_MAX_LEN = 60


def assemble_viz_brief(
    topic: ExtractedTopic,
    suggestion: VizSuggestion | None,
    custom_notes: str,
) -> tuple[str, str]:
    """Compose two strings to drive fixed_main_v6.py:

    Returns:
      short_topic: <= 60 chars, ASCII-safe. This becomes the filename slug.
      full_brief : the rich prompt content that gets sent to the LLM.

    The orchestrator passes `short_topic` via --topic and `full_brief` via
    --extra-context (or an env var) so fixed_main_v6.py can use the rich brief
    in its prompt WITHOUT shoving 1500 characters into the project's directory
    name (which crashes mkdir on every modern filesystem).
    """
    # ── Build the SHORT topic — drives the filename slug ──
    # Just the topic name, optionally with a parenthesised suggestion title.
    short_topic = topic.topic.strip()
    if suggestion and suggestion.title:
        candidate = f"{short_topic} ({suggestion.title.strip()})"
        if len(candidate) <= SHORT_TOPIC_MAX_LEN:
            short_topic = candidate
    short_topic = _ascii_sanitize(short_topic)
    # Hard cap, just in case
    if len(short_topic) > SHORT_TOPIC_MAX_LEN:
        short_topic = short_topic[:SHORT_TOPIC_MAX_LEN].rstrip()

    # ── Build the FULL brief — drives the LLM prompt content ──
    parts: list[str] = [topic.topic]
    if suggestion:
        parts.append(f"({suggestion.title})")
        parts.append("- " + suggestion.approach)   # plain ASCII hyphen
    if custom_notes.strip():
        parts.append("Additional guidance from the instructor: " + custom_notes.strip())
    parts.append(
        f"Audience: {topic.audience_difficulty} learners. "
        f"Pedagogical goal: {topic.why_visual_helps}"
    )
    full_brief = _ascii_sanitize(" ".join(parts))[:1500]

    return short_topic, full_brief
