"""Build the final embed manifest for a completed job.

Pure function — given a JobState, produces the list of EmbedManifestEntry
records that the content creator drops into the source script.
"""
from __future__ import annotations

from models import EmbedManifestEntry, JobState


def build_manifest(job: JobState) -> list[EmbedManifestEntry]:
    """Return the manifest entries for a job's builds.

    Body verbatim from main.py:_build_manifest. The leading underscore
    in the original name is dropped because this is now a public service
    function. No other changes.
    """
    entries: list[EmbedManifestEntry] = []
    for topic in job.topics:
        task = job.builds.get(topic.id)
        if task is None:
            continue
        # Look up the chosen suggestion title (if any)
        viz_title = ""
        if task.selected_suggestion_id:
            sugs = job.suggestions.get(topic.id, [])
            sug = next((s for s in sugs if s.id == task.selected_suggestion_id), None)
            if sug:
                viz_title = sug.title
        if not viz_title and task.custom_notes:
            viz_title = "Custom — " + task.custom_notes[:40]

        entries.append(EmbedManifestEntry(
            section=topic.section,
            embed_after_sentence=topic.embed_after_sentence,
            topic=topic.topic,
            why_visual_helps=topic.why_visual_helps,
            viz_title=viz_title,
            viz_brief=task.final_viz_brief,
            project_dir=task.project_dir,
            screenshot_path=task.screenshot_path,
            github_repo_url=task.github_repo_url if task.github_status == "published" else "",
            status="ok" if task.phase == "completed" else "failed",
        ))
    return entries
