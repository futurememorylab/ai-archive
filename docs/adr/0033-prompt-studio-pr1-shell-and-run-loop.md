# 0033. Prompt Studio PR1 — shell and run loop

- **Date:** 2026-05-26
- **Status:** Accepted
- **Lifespan:** Feature

## Context

The Prompt Studio is an iteration UI for prompts — pick a clip, run, see structured output, tweak, re-run. A prior attempt (PR #9, commit `8a9b2bb`) shipped end-to-end in one PR and was reverted (`1065546`); the spec/plan/ADR were removed from main (`14dc801`). This ADR records the implementation decisions for PR1 of the new attempt.

## Alternatives

- **Single PR for the whole studio.** Rejected: that's what PR #9 did and got reverted. Shipping smaller, focused PRs reduces integration risk and complexity per review.
- **Separate jobs subsystem for studio runs.** Rejected: duplicates worker orchestration, introduces two cancellation paths, and requires separate error-handling logic. Reusing `jobs` keeps the control plane unified.
- **Synchronous in-request runs.** Rejected: model latency makes that an awkward UX — no progress reporting, no cancellation, and long HTTP holds block the browser.
- **Per-prompt testbenches (the original mock framing).** Rejected: the user prefers plain global folders of clips, unconstrained to individual prompts. This simplifies the data model and aligns with the actual workflow.

## Decision

- **Reuse the existing `jobs` pipeline** with a nullable `jobs.kind` column. `kind='studio'` triggers an annotator-side branch that persists to `studio_run` and skips the CatDV-write step.
- **Three new tables** (`studio_folder`, `studio_folder_clip`, `studio_run`). Folders are flat (one-level), globally scoped (not per-prompt), and clip membership is a many-to-many (folder_id, clip_id) key pair.
- **Studio operates on all versions.** Any version is runnable; only the *draft* body is editable in Studio. The `/prompts` page keeps lifecycle management; Studio is purely the iteration loop.
- **One focused clip at a time.** No multi-select or batch runs in v1. This keeps UI and job orchestration simple and focused.
- **Model picker overrides per-run.** The model picker on the run screen allows overriding the version's stored model; `studio_run.model` records what actually executed.
- **`jobs.kind` is intentionally unconstrained** (no CHECK). Future job kinds may be added without schema changes; the discriminator is owned by the application layer.
- **`studio_run.status` uses a CHECK constraint** (`'pending' | 'running' | 'ok' | 'error'`), matching the pattern in `0009_prompts_and_versions.sql` for finite-state columns.

## Consequences

- **Cancel/retry/SSE come free.** The jobs pipeline handles all three for studio runs; `jobs.kind` discriminates within the annotator, not at the queue level.
- **`studio_run` history is persisted forever.** v1 UI only shows the latest per (version, clip). Future PRs can add timeline or history views without reshaping the schema.
- **One branching point in the annotator.** The `_process_item` method branches on `kind` once after the shared resolve→upload→prompt path; both finalizers (jobs and studio) share no state, reducing coupling.
- **Folder names are globally unique.** No namespacing constraints exist yet. If we ever need per-prompt folders, name uniqueness becomes a problem and requires a schema revision.
- **`studio_runs_repo` is now a required argument** to `run_job`. Tests that constructed jobs needed updates; a back-fill was completed in the test suite.
