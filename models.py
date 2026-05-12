"""Pydantic models — the entire data shape of the orchestrator.

A "Job" is the lifecycle of one HackMD upload. It moves through phases:
  uploaded -> topics_extracted -> awaiting_user_picks -> building -> done

Each topic the user picks spawns a "BuildTask" which runs fixed_main_v6.py
in a subprocess and writes back its progress + final viz path.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────
# Audience + complexity enums
# ──────────────────────────────────────────────

AudienceDifficulty = Literal["beginner", "intermediate", "advanced"]
VizComplexity = Literal["low", "medium", "high"]


# ──────────────────────────────────────────────
# Topic extraction (Agent 1)
# ──────────────────────────────────────────────

class ExtractedTopic(BaseModel):
    """A pedagogically valuable visualization opportunity in the script."""

    id: str = Field(..., description="Stable id assigned by the orchestrator (topic_1, topic_2 ...)")
    section: str = Field(..., description="Markdown section heading the topic appears under")
    topic: str = Field(..., description="Short topic name suitable as a slug")
    embed_after_sentence: str = Field(
        ...,
        description=(
            "Verbatim sentence from the source after which the viz should be embedded. "
            "Must appear EXACTLY in the source for the assembler to find it."
        ),
    )
    why_visual_helps: str = Field(
        ...,
        description="One-sentence justification of why a viz adds genuine value here.",
    )
    audience_difficulty: AudienceDifficulty = "beginner"
    surrounding_context: str = Field(
        "",
        description="~500 chars of context around the embed point, used by the suggestion agent.",
    )


class TopicExtractionResult(BaseModel):
    script_name: str
    topics: list[ExtractedTopic]
    extraction_note: str = ""  # any caveats from the LLM


# ──────────────────────────────────────────────
# Viz suggestions (Agent 2)
# ──────────────────────────────────────────────

class VizSuggestion(BaseModel):
    id: str = Field(..., description="viz_1 ... viz_5")
    title: str = Field(..., min_length=2, max_length=80)
    approach: str = Field(..., description="2-4 sentences describing what would be built and shown.")
    beginner_benefit: str = Field(..., description="One sentence of value for a beginner learner.")
    intermediate_benefit: str = Field(..., description="One sentence of value for an intermediate learner.")
    complexity: VizComplexity = "medium"


class VizSuggestionsResult(BaseModel):
    topic_id: str
    suggestions: list[VizSuggestion]


# ──────────────────────────────────────────────
# Build task (calls fixed_main_v6.py)
# ──────────────────────────────────────────────

BuildPhase = Literal[
    "queued",
    "step1_generate",
    "step2_build",
    "step3_runtime",
    "step4_polish",
    "completed",
    "failed",
]


class BuildTask(BaseModel):
    id: str
    topic_id: str
    selected_suggestion_id: Optional[str] = None
    custom_notes: str = ""
    short_topic: str = ""               # <= 60 chars, used as --topic / dirname
    final_viz_brief: str = ""           # full brief for LLM prompt + UI display
    phase: BuildPhase = "queued"
    progress_log: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    project_dir: str = ""               # path to the generated Vite project
    screenshot_path: str = ""
    error: str = ""
    token_usage: dict = Field(default_factory=dict)

    # GitHub publish — each successful build gets its own standalone repo
    github_status: str = "not_started"       # not_started | publishing | published | skipped | failed
    github_repo_url: str = ""                # https://github.com/<owner>/<repo>
    github_clone_url: str = ""               # https://github.com/<owner>/<repo>.git
    github_repo_name: str = ""
    github_commit_sha: str = ""
    github_error: str = ""


class BuildRequest(BaseModel):
    """Request payload from the frontend when the user picks a suggestion."""

    suggestion_id: Optional[str] = Field(
        None,
        description="Required unless custom_notes alone is being used.",
    )
    custom_notes: str = Field(
        "",
        max_length=2000,
        description="Optional free-text customisation appended to the chosen suggestion.",
    )
    # Legacy fields from the old Pages flow — kept for backwards-compat with older
    # frontends that still send them. The new per-viz-repo flow ignores both.
    github_module: str = Field("", max_length=200, description="(deprecated) ignored.")
    github_class: str = Field("", max_length=200, description="(deprecated) ignored.")


# ──────────────────────────────────────────────
# Job (top-level state)
# ──────────────────────────────────────────────

class JobStatus(str, Enum):
    UPLOADED = "uploaded"
    TOPICS_EXTRACTED = "topics_extracted"
    AWAITING_USER_PICKS = "awaiting_user_picks"
    BUILDING = "building"
    DONE = "done"
    FAILED = "failed"


class EmbedManifestEntry(BaseModel):
    """Final per-topic record returned to the content creator."""

    section: str
    embed_after_sentence: str
    topic: str
    why_visual_helps: str
    viz_title: str
    viz_brief: str
    project_dir: str
    screenshot_path: str = ""
    github_repo_url: str = ""       # standalone repo for this viz (if published)
    status: Literal["ok", "failed", "skipped"] = "ok"


class JobState(BaseModel):
    job_id: str
    script_name: str
    track: str = "Academy DSA"
    status: JobStatus = JobStatus.UPLOADED
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Agent outputs
    topics: list[ExtractedTopic] = Field(default_factory=list)
    suggestions: dict[str, list[VizSuggestion]] = Field(
        default_factory=dict,
        description="topic_id -> list of viz suggestions",
    )

    # User picks
    builds: dict[str, BuildTask] = Field(
        default_factory=dict,
        description="topic_id -> BuildTask (one viz per topic for now)",
    )

    # Final output
    manifest: list[EmbedManifestEntry] = Field(default_factory=list)

    # Error / log
    error: str = ""
    logs: list[str] = Field(default_factory=list)

    # Token tracking (cumulative, all LLM calls + all subprocess builds)
    token_usage: dict = Field(default_factory=dict)


# ──────────────────────────────────────────────
# Misc API request/response shapes
# ──────────────────────────────────────────────

class UploadResponse(BaseModel):
    job_id: str
    script_name: str
    char_count: int
    status: JobStatus


class JobSummary(BaseModel):
    job_id: str
    script_name: str
    status: JobStatus
    created_at: datetime
    topic_count: int
    build_count: int


class HealthResponse(BaseModel):
    ok: bool
    fixed_main_path: str
    fixed_main_exists: bool
    text_model: str
    output_dir: str
