# Batches hub — a dedicated overview of annotation runs

**Date:** 2026-06-02
**Status:** Approved (design)

Origin: a Claude Design handoff bundle (`batches.html` prototype + chat
transcript). The prototype is a client-simulated mock; this spec maps it
onto the real `jobs` / `run_group` / `review_items` backend.

## Problem

Running a batch today is a fire-and-forget action: select clips on the
list → **Actions → Annotate selected** → one job per media kind is created
sharing a `run_group` (see `docs/specs/2026-05-30-bulk-annotate-selected-design.md`),
and progress is only visible via a transient top-bar pill and the `batch=`
filter on the clips list. There is **no persistent place** to answer:

- What batches have run, and when?
- How many clips ran, how many produced drafts, how many failed?
- How many of a batch's drafts have been reviewed vs. still awaiting review?
- Which clips failed, with what error — and can I re-run just those?

That dedicated job-history surface was an explicit non-goal of the
bulk-annotate spec; this spec delivers it as a first-class **Batches** hub
with its own left-rail entry.

## Goals

- A new **Batches** surface at `/batches`, reachable from a new left-rail
  button (layers icon) present on every page.
- An **overview metric strip**: total batches, drafts produced, awaiting
  review, failed clips.
- A **batch history table**, **one row per `run_group`** (jobs with no
  `run_group` appear as singleton `job:<id>` batches), showing:
  prompt · version · model, started time, **ran**, a **Completed** bar
  (with failed count called out in red), a **Reviewed** bar (`reviewed/
  completed`), a **status** pill, and row actions.
- **Failed-clip inspection + retry**: expand a row with failures to list
  each failed clip + its error; **Retry** one clip or **Retry all failed**
  for the batch. Retry re-runs only the failed clips and folds successes
  back into the completed/reviewed tallies.
- A **Review →** hand-off: for batches with un-reviewed drafts, jump to the
  existing review surface scoped to that batch.
- **+ New batch** routes the user to the clips list to use the existing
  Annotate-selected flow (which already mints `run_group` batches that
  appear here). No second clip picker is built.
- **Live updates** for running batches, reusing the existing global `jobs`
  SSE topic — no client-side simulation.
- The read path is **fully offline-safe** (pure DB); only retry needs live
  services.

## Non-goals

- A new clip-picker modal. "+ New batch" reuses the clips-list selection +
  Annotate-selected flow per the reuse rule in CLAUDE.md. (The prototype's
  in-page picker and its `clipData.js` / `clipList.js` replica modules are
  **not** ported.)
- Changing how jobs are created or executed (`POST /api/jobs`,
  `JobsRepo.create_job`, `annotator.run_job` stay as-is, plus one small
  backward-compatible filter param on `run_job` for per-clip retry).
- Per-clip prompt overrides, multi-prompt-per-kind, or editing a batch
  after it starts.
- Removing or changing the top-bar jobs indicator (it stays; the hub is the
  persistent/history complement to the live pill).
- Cancelling a running batch from the hub (cancel stays on the top-bar
  indicator via the existing `POST /api/jobs/{id}/cancel`).

## Design

### Batch = a group of jobs

A "batch" is the set of `jobs` sharing a non-null `run_group`, keyed by
`batch_key = COALESCE(run_group, 'job:' || id)`. A single bulk action that
spanned multiple media kinds produced several per-kind jobs under one
`run_group`; those collapse into one batch row. Per-batch fields aggregate
across the member jobs:

| Field | Source |
|---|---|
| `started` | `MIN(jobs.created_at)` over member jobs |
| `prompt` / `version` / `model` | from the **primary** job (lowest job id): `prompts.name`, `prompt_versions.version_num`, `prompt_versions.model`. If the batch has >1 distinct `prompt_version_id`, label as `"<name> + N more"` |
| `ran` | `COUNT(job_items)` over member jobs |
| `failed` | `COUNT(job_items WHERE status = 'error')` |
| `completed` | items past the in-flight set and not errored: `status NOT IN ('pending','resolving','uploading','prompting','error')` (matches `JobsRepo.progress`'s `done` minus `errors`) |
| `running` | any member `jobs.status = 'running'`, or any item in the in-flight set |
| `awaiting` | `COUNT(DISTINCT clip)` with un-applied `review_items` reachable via `annotations.job_id ∈ member jobs` — the **same** predicate as `ReviewItemsRepo.count_pending_clips(job_id=…)`, so the Review hand-off lands on exactly these clips |
| `reviewed` | `completed − awaiting` (clamped ≥ 0) |

### Backend — aggregation (read path, offline-safe)

Add to `JobsRepo` (repos are leaves — no service imports):

- `list_batches(conn, *, limit=50) -> list[dict]` — one **grouped** query
  over `jobs` joined to `prompt_versions` / `prompts`, with the per-batch
  item counts (`completed` / `failed` / `running`) computed by joining a
  `job_items` aggregate, and the `awaiting` count from a `review_items ⋈
  annotations` aggregate keyed on `annotations.job_id`. **One query for the
  whole page** — no per-batch loop (Performance discipline / ADR 0046). A
  `tests/.../query_count` guard asserts the statement count is flat across
  10 / 100 / 1000 batches.
- `batch_metrics(conn, *, limit)` — the four metric-strip totals over the
  same windowed set (`Batches` shows the grand total of distinct batch
  keys; `Drafts produced` / `Awaiting review` / `Failed clips` sum across
  the shown window, matching the prototype's "across recent batches" copy).
- `failed_items_for_batch(conn, batch_key) -> list[dict]` — the
  expand-row data: `catdv_clip_id`, `error_message`, and a clip name
  resolved from `annotations.catdv_clip_name` when present, else the
  `clip_list_cache` (offline-safe), else the bare id.

Multi-key reads that take id lists go through
`repositories/_batch.py::chunked_in_clause`.

### Backend — routes

New `backend/app/routes/batches.py` (router does **not** import `httpx`;
goes through repos/services per the import-linter contract):

- `GET /batches` (depends on `get_core_ctx`) — renders `pages/batches.html`
  via the shared `templates` env. Pure DB; works fully offline.
- `GET /batches/table` — the HTMX partial (`_batches_table.html`) used for
  live refresh; same data as the page body.
- `POST /batches/{batch_key}/retry-failed` (depends on `get_live_ctx` →
  typed 503 when offline) with optional `clip_ids: list[int]` body. Resolves
  the batch's jobs that have failed items and, for each, spawns the existing
  background runner (the `_run_in_bg(live, job_id)` pattern from
  `routes/jobs.py`). On `HX-Request`, returns the swapped `_batches_table.html`
  partial; the client pushes a success toast.

**Why retry is thin:** `annotator.run_job` already processes items with
`status in ('pending','error')` and `continue`s past completed ones
(`annotator.py:116`). Re-invoking `run_job` for a batch's jobs therefore
re-runs exactly the failed items and leaves existing drafts intact. The
only addition is an **optional `only_clip_ids` filter on `run_job`** (default
`None` = today's behavior) so a single-clip retry re-runs just that clip;
`JobsRepo` gets no new write method.

Register the router in the app and add the page route to whatever wires
`pages/*` routes (mirroring how `/cache`, `/prompts`, `/studio` are
registered).

### Frontend — page (reuse-first, server-rendered)

`backend/app/templates/pages/batches.html` extends `layout.html`, sets
`{% block rail_active %}batches{% endblock %}`, and composes:

- The shared **`.metric-strip`** / `.metric` blocks (already in `app.css`,
  used by the cache page) for the four overview numbers — the `danger`
  variant for Failed clips.
- `{% include "pages/_batches_table.html" %}` — the history table built
  from server data, using `_ui.html` macros (`{{ ui.button(...) }}`),
  `.pill` / `{{ ui.status_pill(...) }}` for status, and the `smpte` /
  `bytes_human` filters where relevant. Progress bars use the existing
  `.metric .m-bar` / a small `.miniprog` rule set scoped to this page;
  colors come from `:root` tokens (`--good`, `--accent`, `--bad`,
  `--info`) — **no new hexes**, `.btn` not `*-btn`.
- `_batch_fail_rows.html` — the expandable failed-clip detail, rendered
  inline and toggled with a tiny Alpine `x-data` holding only the
  `expanded` set (cross-row UI state, not cross-component — no
  `Alpine.store` needed; if shared state were required it would use
  `Alpine.store`, never `_x_dataStack`).

Status label/class mirrors the prototype: running → `accent` "Running
X/Y"; not running with `awaiting>0` → "Awaiting review" (if `reviewed==0`)
or "N to review"; otherwise `ok` "Applied".

### Frontend — rail + nav

- New `backend/app/templates/icons/_batches.svg` — the layers glyph from
  the prototype (`<polygon points="12 3 21 8 12 13 3 8 12 3">` +
  `<polyline points="3 13 12 18 21 13">`).
- One added line in the shared `pages/_rail.html` (`href="/batches"`,
  `title="Batches"`, active when `rail_active == 'batches'`) — appears on
  every page automatically.

### Frontend — live refresh

A minimal Alpine controller on the page opens **one** `EventSource` to the
existing `GET /api/jobs/events` (global `jobs` topic). On any frame — and
gated to "there is ≥1 running batch on the page" — it triggers
`htmx.ajax('GET', '/batches/table', {target, swap:'outerHTML'})` to re-swap
the table, then re-evaluates the gate. It also listens for the existing
`jobs-changed` window event (dispatched by `bulkAnnotate.js`) so a freshly
started batch appears immediately. Subtree re-init after the swap goes
through `window.htmxAlpine.reinit(el)` (the single lifecycle helper) — no
hand-rolled `Alpine.initTree` / `htmx.process`, no `location.reload()`.

### "+ New batch"

The header button links to the clips list (`/`). The existing
Annotate-selected flow there creates `run_group` batches that surface in
the hub. (Optional, low-cost nicety, in scope: after a bulk run starts,
the success toast can link to `/batches`. If it adds risk, it drops to a
follow-up.)

## Reuse map (no duplication)

| Need | Reuse |
|---|---|
| Batch = grouped jobs | `jobs.run_group`, `JobsRepo` (new read methods only) |
| Per-item progress semantics | `JobsRepo.progress` definition of done/errors |
| Reviewed / awaiting counts | `ReviewItemsRepo.count_pending_clips(job_id=…)` predicate |
| Retry execution | `annotator.run_job` (already re-runs `error` items) + `routes/jobs.py::_run_in_bg` background pattern |
| New batch | clips-list selection + Annotate-selected (`bulkAnnotate.js`) |
| Live updates | global `jobs` SSE topic + `/api/jobs/events`; `jobs-changed` event |
| Page shell / macros / tokens | `layout.html`, `_rail.html`, `_ui.html`, `.metric-strip`, `.pill`, `:root` tokens, `smpte` / `bytes_human` filters |
| HTMX↔Alpine lifecycle | `window.htmxAlpine.reinit` |
| Toasts | `Alpine.store('toast').push(msg, {level})` |
| Review hand-off | existing clips-list `batch=` review surface (param confirmed against `clip_list_filters.py` at plan time) |

## Error handling

- **Retry while offline / services down:** `POST /batches/{key}/retry-failed`
  depends on `get_live_ctx` → typed 503; the client surfaces it as an error
  toast (no silent `.catch`, no `alert`).
- **Batch with a deleted/archived prompt version:** the join tolerates it —
  fall back to a `"(prompt unavailable)"` label rather than dropping the row.
- **Provider/transient failures during retry:** recorded as item `error` by
  `run_job` exactly as a first run; the row re-shows the failed clips with
  the new error. Absence is never inferred — `run_job` keeps using
  `is_provider_not_found` / `humanise` as today.
- **Empty state:** no batches yet → a centered empty message with a link to
  the clips list ("Select clips and run Annotate to create your first
  batch").
- **Clip name unresolvable offline:** failed-row falls back to the clip id.

## Testing

Backend (pytest):

- `list_batches` groups jobs by `run_group` (2 per-kind jobs in one group →
  1 batch row; a job with no `run_group` → its own row) with correct
  `ran` / `completed` / `failed` / `reviewed` / `awaiting` / `running`.
- Multi-prompt run_group → `"<name> + N more"` label; primary = lowest job
  id.
- `batch_metrics` totals match the summed rows.
- **N+1 guard** (`tests/_helpers/query_count.assert_query_count`): flat
  statement count for 10 / 100 / 1000 batches (ADR 0046).
- `failed_items_for_batch` resolves clip names (annotation → cache → id).
- Route: `GET /batches` renders (200, contains the rail-active marker and a
  known batch); `GET /batches/table` returns the partial; retry route
  returns 503 offline and, with a live ctx stub, re-runs only failed items
  (existing `run_job` execution tests stay green; `run_job(only_clip_ids=…)`
  processes just the named clip).
- Template render smoke test through the shared env (guards the single-env
  rule).

Frontend / integration:

- Manual acceptance flows below.

## Manual acceptance flows

1. **Overview renders from real data.** Run a couple of batches first (flow
   2 of the bulk-annotate spec, or pre-seed jobs). Open `/batches`.
   *Expected:* metric strip shows non-zero Batches / Drafts produced /
   Awaiting review / Failed clips; the table lists one row per `run_group`
   with prompt · v · model, started time, `ran`, a Completed bar, a
   Reviewed bar, and a status pill. The **Batches** rail icon is active.

2. **Rail entry on every page.** From `/`, `/prompts`, `/studio`, `/cache`,
   click the new **Batches** rail icon. *Expected:* lands on `/batches`;
   the icon is the active rail item on that page only.

3. **Live progress.** Start a batch via clips-list **Annotate selected**,
   then open `/batches` (or have it open in another tab). *Expected:* the
   new batch appears at the top with an `accent` "Running X/Y" pill and a
   Completed bar that advances without a manual refresh; on completion the
   pill settles to "Awaiting review".

4. **Failed-clip inspection + retry all.** For a batch with ≥1 failure,
   click the red "· N failed ▾". *Expected:* the row expands to list each
   failed clip name + its error. Click **Retry all failed** (online).
   *Expected:* the failed clips re-run (only those — existing drafts
   untouched); on success the failed count clears, completed/reviewed bars
   update, and a success toast appears. No full-page reload.

5. **Retry a single clip.** In the expanded failures, click **Retry** on
   one clip. *Expected:* only that clip re-runs; the other failures remain
   listed; the table updates in place.

6. **Retry offline.** With services offline, attempt **Retry all failed**.
   *Expected:* an error toast naming the offline condition (typed 503); the
   row is unchanged. The rest of `/batches` still renders (read path is
   offline-safe).

7. **Review hand-off.** On a batch with un-reviewed drafts, click
   **Review →**. *Expected:* lands on the review surface scoped to that
   batch's pending clips; reviewing/accepting there reduces the batch's
   "awaiting" and raises its Reviewed bar on return to `/batches`.

8. **+ New batch.** Click **+ New batch**. *Expected:* lands on the clips
   list ready to select clips and run Annotate-selected; the resulting run
   shows up as a new batch in the hub.

9. **Empty state.** With no jobs in the DB, open `/batches`. *Expected:* a
   clear empty message linking to the clips list, no broken table.

10. **Existing surfaces unaffected.** The top-bar jobs indicator and the
    clips-list `batch=` filter behave exactly as before; single-clip
    Annotate is untouched.
