# Deployment design: tool on Railway, animations on Vercel

**Date:** 2026-06-01
**Status:** Approved design — ready for implementation plan

## Goal

Move from "all vizzes in one GitHub Pages monorepo" to a deployed model where:

- **The tool** (FastAPI + React SPA, Agents A/B, Playwright viz generator) runs as a
  long-lived service.
- **The generated animations** are hosted per-program on Vercel, with clean,
  stable, embeddable URLs and per-program access control.

Drivers (from brainstorming): **clean per-program URLs** and **access control /
ownership**. Scale: **4 programs × 10–15 modules each**, multiple vizzes per module.
Platform-generated URLs (`*.vercel.app`) are acceptable for now; custom domains are
a future option Vercel keeps open.

## Two independent deploy targets

### 1. The tool → stays on Railway (unchanged)

No change. The backend needs Playwright/Chromium, multi-minute background jobs,
subprocess spawning, and a writable `viz_outputs/` volume — none of which fit
Vercel's serverless model. The existing `Dockerfile` and `railway.toml` already
cover this. This decision is recorded here only so it is explicit; there is no
implementation work for the tool's own hosting.

### 2. The animations → per-program GitHub repo → Vercel

- **One repo per program** (`track`): `viz-<program-slug>`. The repo boundary
  provides access control (repo collaborators / teams).
- Each repo is **imported once into Vercel** (manual, 4×), framework preset
  "Other", no build command, output = repo root, production branch = `main`.
- The tool **pushes generated HTML to `main`**; Vercel auto-deploys on push.
- **Modules are folders, not branches.** Branch-per-module was considered and
  rejected: on Vercel only `main` is Production with a clean URL; other branches
  are Preview deployments with unstable, auth-protected URLs. Folders give the
  same separation with stable URLs and no fight with the platform.

## Repo layout (per program, all on `main`)

```
viz-<program-slug>/
  vercel.json                  # static config: cleanUrls + trailingSlash
  <module-slug>/
    <viz-slug>/
      index.html
      screenshot.png
    <viz-slug-2>/
      index.html
      screenshot.png
```

**Embed URL:** `https://<program>.vercel.app/<module-slug>/<viz-slug>/`

This is the existing monorepo layout plus a module-folder level, split into
per-program repos, with GitHub Pages dropped (Vercel hosts).

## `vercel.json` (committed to each program repo)

```json
{
  "cleanUrls": true,
  "trailingSlash": true
}
```

No build step — files are served statically from the repo root. The tool writes
this file once when it first creates a program repo.

## Program / module routing

- **Program = `track`.** The upload endpoint already takes `track` (one of
  `ALLOWED_TRACKS`). We reuse it for repo routing — no new "program" field.
- **Module = a new upload field.** A `module` slug (sanitized) is added to the
  upload form. One HackMD lecture script = one module = one upload, producing
  multiple vizzes under that module folder.
- Selection happens **at upload** in the SPA: the existing track selector plus a
  new module-slug text input.

## Components to change

1. **`backend/api/jobs.py` (`POST /upload`)** — add `module: str = Form(...)`,
   sanitize it, validate non-empty, and carry it onto `JobState`.
2. **`backend/models.py`** — add `module: str` to `JobState` (and any per-task
   slug fields needed to build the path).
3. **`backend/github_publisher.py`** — generalize from a single
   `viz_monorepo_name` to a **per-program repo** resolved from the job's `track`;
   push to `<module>/<viz>/index.html` + `screenshot.png` instead of `<slug>/`.
   Write `vercel.json` at repo root on first creation. **Remove
   `_ensure_pages_enabled`** (Vercel hosts, not Pages). Keep auto-create-repo,
   atomic-commit, and stale-ref retry logic.
4. **`backend/config.py`** — replace the single `viz_monorepo_name` with a
   **program → {repo_name, vercel_base_url}** mapping (4 entries). Resolution from
   `track`. Keep `github_token` / `github_owner` / `publish_to_github`.
5. **`backend/services/manifest_builder.py`** — emit the new
   `https://<program>.vercel.app/<module>/<viz>/` embed URL.
6. **SPA (frontend)** — add the module-slug input next to the track selector on
   the upload screen.
7. **Docs** — update `README.md` publish section and `env.example` with the new
   per-program config and the removal of the single-monorepo / Pages model.

## Config shape (illustrative)

```
PROGRAM_REPOS = {
  "Academy DSA":     {"repo": "viz-academy-dsa",     "vercel_base": "https://viz-academy-dsa.vercel.app"},
  "DSML DS":         {"repo": "viz-dsml-ds",         "vercel_base": "https://viz-dsml-ds.vercel.app"},
  ...  # 4 active programs
}
```

`vercel_base` is captured once after importing each repo into Vercel (the
project's production URL). At 4 programs this is hand-maintained config, not API
automation.

## Vercel setup (one-time, manual, per program)

1. Create/identify the program repo (the tool auto-creates on first publish).
2. In Vercel: **Add New Project → Import** the repo → framework "Other", no build
   command → Deploy.
3. Copy the production URL into the program's `vercel_base` config.
4. Subsequent pushes to `main` auto-deploy.

## Out of scope (YAGNI)

- Vercel API automation for project creation (4 programs → do it by hand).
- Custom domains (config keeps the door open; not built now).
- Branch-per-module deployment.
- Migrating the tool itself off Railway.

## Testing

- **`github_publisher`**: unit tests with mocked GitHub API — assert correct
  per-program repo resolution, `<module>/<viz>/` tree paths, `vercel.json`
  creation on first push, no Pages call, and stale-ref retry still works.
- **`manifest_builder`**: assert the new Vercel embed URL shape.
- **`POST /upload`**: assert `module` is required, sanitized, and rejected when
  empty/invalid.
- **Manual end-to-end**: upload one lecture under a program+module, confirm the
  files land at `<module>/<viz>/` on `main`, and the Vercel URL serves them.
