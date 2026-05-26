"""Unit tests for backend.services.manifest_builder.build_manifest."""
from __future__ import annotations

from backend.services.manifest_builder import build_manifest
from backend.models import (
    BuildTask,
    EmbedManifestEntry,
    ExtractedTopic,
    JobState,
    JobStatus,
    VizSuggestion,
)


def _make_topic(idx: int) -> ExtractedTopic:
    return ExtractedTopic(
        id=f"topic_{idx}",
        section=f"## Section {idx}",
        topic=f"Topic {idx}",
        embed_after_sentence=f"Sentence {idx}.",
        why_visual_helps=f"It helps {idx}.",
        surrounding_context="",
    )


def _make_build(topic_id: str, phase: str = "done") -> BuildTask:
    return BuildTask(
        id=f"build_{topic_id}",
        topic_id=topic_id,
        short_topic=topic_id,
        final_viz_brief="A brief description of the viz.",
        phase=phase,
        project_dir=f"/tmp/proj_{topic_id}",
        screenshot_path="",
        github_repo_url="",
        github_status="not_started",
    )


def _fixture_job(num_topics: int, completed_indices: list[int]) -> JobState:
    """Build a JobState with N topics; only the indices in completed_indices
    have BuildTasks in the 'done' phase."""
    topics = [_make_topic(i) for i in range(num_topics)]
    builds: dict[str, BuildTask] = {}
    for i in completed_indices:
        topic_id = f"topic_{i}"
        builds[topic_id] = _make_build(topic_id, phase="done")
    return JobState(
        job_id="test-job-1",
        script_name="test_script.md",
        status=JobStatus.DONE,
        topics=topics,
        builds=builds,
    )


def test_manifest_for_single_completed_build():
    job = _fixture_job(num_topics=1, completed_indices=[0])
    out = build_manifest(job)
    assert len(out) == 1
    assert out[0].status == "ok"


def test_manifest_empty_for_no_topics():
    job = _fixture_job(num_topics=0, completed_indices=[])
    out = build_manifest(job)
    assert out == []


def test_manifest_skips_topics_with_no_build():
    # Topics that have no corresponding BuildTask are silently skipped.
    job = _fixture_job(num_topics=2, completed_indices=[0])
    # topic_1 has no build
    out = build_manifest(job)
    assert len(out) == 1
    assert out[0].topic == "Topic 0"


def test_manifest_includes_failed_builds_with_failed_status():
    # A topic whose build is in 'failed' phase → status="failed" in manifest.
    job = _fixture_job(num_topics=1, completed_indices=[])
    # Add a build manually in 'failed' phase
    topic_id = "topic_0"
    job.topics = [_make_topic(0)]
    job.builds[topic_id] = _make_build(topic_id, phase="failed")
    out = build_manifest(job)
    assert len(out) == 1
    assert out[0].status == "failed"


def test_manifest_non_done_non_failed_is_status_failed():
    # Any phase other than "done" maps to status="failed".
    job = _fixture_job(num_topics=1, completed_indices=[])
    topic_id = "topic_0"
    job.topics = [_make_topic(0)]
    job.builds[topic_id] = _make_build(topic_id, phase="draft")
    out = build_manifest(job)
    assert len(out) == 1
    assert out[0].status == "failed"


def test_manifest_picks_up_github_repo_url():
    job = _fixture_job(num_topics=1, completed_indices=[0])
    build = list(job.builds.values())[0]
    build.github_repo_url = "https://github.com/foo/bar"
    build.github_status = "published"
    out = build_manifest(job)
    assert out[0].github_repo_url == "https://github.com/foo/bar"


def test_manifest_github_repo_url_empty_when_not_published():
    # github_repo_url is only included when github_status == "published".
    job = _fixture_job(num_topics=1, completed_indices=[0])
    build = list(job.builds.values())[0]
    build.github_repo_url = "https://github.com/foo/bar"
    build.github_status = "not_started"
    out = build_manifest(job)
    assert out[0].github_repo_url == ""


def test_manifest_viz_title_from_suggestion():
    job = _fixture_job(num_topics=1, completed_indices=[0])
    topic_id = "topic_0"
    suggestion = VizSuggestion(
        id="viz_1",
        title="My Viz Title",
        approach="Shows data.",
        beginner_benefit="Easy to follow.",
        intermediate_benefit="Deeper insight.",
    )
    job.suggestions[topic_id] = [suggestion]
    job.builds[topic_id].selected_suggestion_id = "viz_1"
    out = build_manifest(job)
    assert out[0].viz_title == "My Viz Title"


def test_manifest_viz_title_from_custom_notes_when_no_suggestion():
    job = _fixture_job(num_topics=1, completed_indices=[0])
    topic_id = "topic_0"
    job.builds[topic_id].custom_notes = "My custom visualization notes here"
    out = build_manifest(job)
    assert out[0].viz_title.startswith("Custom — ")
    assert "My custom" in out[0].viz_title


def test_manifest_multiple_topics_ordering():
    # Entries appear in topics list order.
    job = _fixture_job(num_topics=3, completed_indices=[0, 1, 2])
    out = build_manifest(job)
    assert len(out) == 3
    assert out[0].topic == "Topic 0"
    assert out[1].topic == "Topic 1"
    assert out[2].topic == "Topic 2"


def test_manifest_includes_embed_url_when_published():
    from backend.models import BuildTask

    job = _fixture_job(num_topics=1, completed_indices=[0])
    topic_id = "topic_0"
    build = job.builds[topic_id]
    build.github_status = "published"
    build.github_repo_url = "https://github.com/u/m"
    build.embed_url = "https://u.github.io/m/t/"
    build.repo_edit_url = "https://github.com/u/m/tree/main/t"
    entries = build_manifest(job)
    assert len(entries) == 1
    e = entries[0]
    assert e.embed_url == "https://u.github.io/m/t/"
    assert e.repo_edit_url == "https://github.com/u/m/tree/main/t"
    assert e.status == "ok"


def test_manifest_embed_url_empty_when_not_published():
    """If github_status is not 'published', embed_url + repo_edit_url should be blanked
    in the manifest entry even if the BuildTask has them set."""
    job = _fixture_job(num_topics=1, completed_indices=[0])
    topic_id = "topic_0"
    build = job.builds[topic_id]
    build.github_status = "failed"
    build.embed_url = "https://u.github.io/m/t/"
    build.repo_edit_url = "https://github.com/u/m/tree/main/t"
    entries = build_manifest(job)
    assert entries[0].embed_url == ""
    assert entries[0].repo_edit_url == ""
