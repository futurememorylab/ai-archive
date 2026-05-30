# Bulk "Annotate selected" — per-kind multi-clip annotation from the clips list

**Date:** 2026-05-30
**Status:** Approved (design)

## Problem

The clips list has an **Actions** dropdown (`clips.html` → `bulkSel()`)
with *Review selected*, *Apply drafts*, *Cache locally*, and *Remove from
local cache* — but **no way to run annotations across the selected
clips**. Today the only way to run a production prompt is the single-clip
**Annotate** dropdown on `clip_detail.html` (`clipAnnotate.js`), one clip
at a time.

The backend already supports multi-clip jobs: `POST /api/jobs` accepts
`clip_ids: list[int]` with one `prompt_version_id`, and
`annotator.run_job` iterates over all items. The gap is purely a
**clips-list UI surface** plus a **persistent progress indicator** so a
batch can run while the user keeps working.

A real selection is often **mixed media kinds** (video / image / audio).
A single prompt can't serve all of them, so the picker must let the user
assign **one prompt per kind present in the selection**, and each clip
must route to the prompt matching its kind at run time.

## Goals

- A new **"Annotate selected →"** item in the clips-list Actions menu,
  enabled when ≥1 clip is selected.
- A picker modal that, **for each media kind present in the selection**,
  shows the clip count and a dropdown of compatible **production**
  prompts (prompt `media_kind` equals that kind, or `any`).
- Kinds with **no compatible / assigned prompt are flagged before Run**
  (e.g. *"Audio (2 clips) — no prompt assigned, will be skipped"*), so
  the user knows exactly what will and won't run. Run is enabled as long
  as ≥1 kind is assigned.
- On Run, create **one job per assigned kind** (each with that kind's
  prompt and its matching clip IDs), then show a persistent top-bar
  progress indicator.
- A **persistent top-bar batch indicator**, visible from any page, that:
  - shows aggregate progress across active jobs (e.g. *"Annotating
    14/30"*),
  - turns **red on failure** and stays noticeable until dismissed,
  - **click → navigates to the clips list filtered to the batch**,
  - has a small **✕ to cancel** the running job(s).
- The rest of the app stays fully usable during a run — the user can
  review/accept earlier drafts while the batch runs.

## Non-goals

- Changing the single-clip Annotate dropdown behavior on
  `clip_detail.html`.
- Per-clip prompt overrides (routing is by kind only).
- Multi-prompt-per-kind (one prompt per kind).
- A dedicated job-detail page/drawer (the existing `batch=` filter on the
  clips list is the progress surface).
- Persisting indicator state in client storage — the server is the source
  of truth; the indicator re-derives state on each page load from active
  jobs.
- Draft/archived prompts in the picker (production prompts only, matching
  today's Annotate dropdown).
- Offline kickoff: when annotation services are unavailable the modal
  surfaces a clear message rather than silently no-op'ing.

## Design

### Selection → per-kind grouping (client)

Each clip row already renders its kind in the `.col-type` cell
(`_clips_row_cells.html`: `<td class="col-type mono">{{ row.kind }}</td>`)
and the selection checkbox value encodes the clip id (`key/clip_id`,
split in `bulkSel()._selectedClipIds()`). The picker reads each selected
row's `.col-type` text — exactly the pattern `reviewSelected()` already
uses to read `.col-drafts` — and groups selected clip IDs by kind.

### Picker modal

A new component opens from the Actions menu into `#modal-root`. For each
kind present:

- a label + count (e.g. *"Video — 8 clips"*),
- a `<select>` of compatible production prompts.

Prompt list + filtering reuses the logic already in `clipAnnotate.js`:
fetch `GET /api/prompts?archived=0`, keep those with
`current_production_version_id != null`, and for each kind keep prompts
whose `media_kind` is that kind or `any`. Kinds with no compatible prompt
(or left unassigned) render a **skip warning row** before Run. If only one
kind is present the modal collapses to a single dropdown.

Run is disabled until ≥1 kind has a prompt assigned. The Run button
summarizes the action (e.g. *"Annotate 8 clips (skipping 2)"*).

### Job kickoff (client → existing API)

On Run, for each assigned kind, `POST /api/jobs` with that kind's
`prompt_version_id` and its `clip_ids` (reusing the fetch + EventSource
pattern from `clipAnnotate.js::pick`). Drafts land in **review scope**,
identical to the single-clip flow. The returned job IDs seed the top-bar
indicator.

### Global jobs stream (backend)

The current event infra is **per-job**: topic `job:{job_id}`, SSE at
`/api/jobs/{job_id}/events` (`routes/events.py`, `services/events.py`
`EventBus`). The topbar needs to watch *all* active jobs with a single
subscription, so:

- Add a **global `jobs` topic**. In `annotator.run_job`, in addition to
  the existing `job:{id}` publishes, publish job-level lifecycle to
  `jobs`: `running` (with `done`/`total`), per-item progress ticks, and
  the terminal `completed` / `failed` / `cancelled` with counts.
- Add `GET /api/jobs/events` in `routes/events.py` that streams the
  `jobs` topic (reusing `_event_generator`).
- Add an **active-jobs query** (`JobsRepo`) returning currently
  `running` jobs with their `done`/`total` counts, so the indicator
  renders correct initial state after a navigation/reload. Done/total is
  derivable from `job_items` status counts; `total_clips` already exists
  on `jobs`.

### Top-bar indicator (`_topbar_pills.html`)

A new Alpine component in the existing pillset. On load it calls the
active-jobs endpoint, renders aggregate progress, and subscribes to
`/api/jobs/events` for live updates. Behaviors:

- **Aggregate progress** across all active jobs: *"Annotating {done}/
  {total}"*.
- **Click** → navigate to the clips list filtered to the batch (existing
  `batch=` query param). With multiple per-kind jobs, link to the most
  recent / a batch view listing them.
- **✕ cancel** → `POST /api/jobs/{id}/cancel` for each active job
  (endpoint already exists).
- **Failure**: red state, persists until dismissed; clicking through
  lands on the batch-filtered list where failed clips are visible.
- Hidden entirely when no jobs are active.

The indicator reuses topbar pill styling + design tokens; no new color
hexes.

## Reuse map (no duplication)

| Need | Reuse |
|---|---|
| Selection + per-row kind read | `bulkSel()` / `rowSelect()` in `clips.html`, `.col-type` cell |
| Production-prompt fetch + kind filter | `clipAnnotate.js` (`/api/prompts?archived=0` + `media_kind` filter) |
| Job kickoff + SSE attach | `clipAnnotate.js::pick` / `attachStream` pattern |
| Multi-clip job execution | `POST /api/jobs`, `JobsRepo.create_job`, `annotator.run_job` (unchanged) |
| Cancel | `POST /api/jobs/{id}/cancel` (unchanged) |
| Batch progress surface | existing `batch=` filter on clips list |
| Modal host / buttons / tokens | `#modal-root`, `{{ ui.button(...) }}`, `:root` tokens, topbar pill styles |

## Error handling

- **Unassigned kinds**: skipped; surfaced *before* Run (warning rows) and
  again in the Run-button summary / post-run notice.
- **Per-clip failures**: already recorded as item `error` by `run_job`;
  the indicator turns red and the batch-filtered list shows which clips
  failed.
- **Services unavailable / offline**: `POST /api/jobs` already skips
  auto-start when `archive/ai_store/gemini/proxy_resolver` are missing;
  the modal surfaces a clear message instead of a silent no-op.
- **Empty selection / no production prompts**: Actions item disabled /
  modal shows the same empty-state copy as the single-clip dropdown
  ("No production prompts. Open Prompts…").

## Testing

Backend (pytest):

- Grouping → one job per kind with correct clip partitioning (2 kinds →
  2 jobs, each with only its clips).
- Global `jobs` topic receives `running` / progress / terminal frames
  during `run_job`.
- Active-jobs query returns running jobs with correct `done`/`total`.

Frontend / integration:

- Existing `run_job` execution tests remain green.
- Manual acceptance flows below.

## Manual acceptance flows

1. **Mixed-kind run with skip notice.** On `/` (clips list) with online
   mode, select 8 video, 3 image, and 2 audio clips. Open **Actions →
   Annotate selected**. *Expected:* modal lists Video (8) and Image (3)
   with prompt dropdowns, and an Audio (2) row warning *"no prompt
   assigned, will be skipped"*. Assign a video prompt and an image
   prompt. Run button reads *"Annotate 11 clips (skipping 2)"*. Click
   Run. *Expected:* modal closes; top-bar shows *"Annotating …/11"*.

2. **Progress visible from another page.** While flow 1 runs, navigate to
   `/prompts`. *Expected:* the top-bar indicator is still present and its
   count advances. Open a clip and accept an earlier draft. *Expected:*
   that still works; the indicator keeps updating.

3. **Click-through to batch.** Click the top-bar indicator. *Expected:*
   lands on the clips list filtered to the batch; rows show drafts
   appearing as items complete.

4. **Cancel mid-run.** Start a run over many clips; click **✕** on the
   indicator. *Expected:* `POST /api/jobs/{id}/cancel` fires for the
   active job(s); in-flight items finish or stop and the indicator clears
   to a cancelled/terminal state.

5. **Failure surfacing.** Induce a failure (e.g. a clip with no
   resolvable proxy while offline, or a forced error). *Expected:* the
   indicator turns red and remains noticeable from any page; clicking
   through shows the failed clip(s) in the batch-filtered list.

6. **Single-kind collapse.** Select only video clips and open the picker.
   *Expected:* a single prompt dropdown (no per-kind rows), Run annotates
   all selected.

7. **Single-clip Annotate unaffected.** On a clip detail page, use the
   existing **Annotate** dropdown. *Expected:* unchanged behavior — runs
   one clip, swaps the draft inline.
