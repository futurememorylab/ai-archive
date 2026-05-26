# Prompt Studio — Design Spec

**Date:** 2026-05-26
**Status:** Draft, awaiting review
**Author:** Peter Hora (with Claude)
**Builds on:** prompt management (ADR 0010), Gemini annotator pipeline
(`services/annotator.py`), proxy cache + offline fallback (ADR 0014,
0015, 0017), boot-with-failed-CatDV (ADR 0023), local AI input store
(`archive/ai_store.py`).
**Decision record:** ADR 0026.

---

## 1. Motivation

Prompt iteration today only happens by promoting a draft to production
and running a real annotation job. That couples experiments to the
production write path, requires a CatDV seat (always scarce — see
`CLAUDE.md`), and gives no way to compare two prompt versions against
the same fixed clip set. Prompt Studio is a sandbox for prompt
development:

- A **testbench** is a hand-curated set of clips (nested folders + clip
  refs). Each item is either an uploaded MP4 or a CatDV clip ref, and
  optionally carries a hand-authored **gold** annotation.
- A **run** executes a single prompt version against a single testbench,
  producing one output per item. Runs are stored separately from the
  production `annotations` table.
- **Comparison** picks two runs (or a run plus the gold values) and
  renders the outputs side by side, using the same Jinja partials the
  production annotate view uses.

Future evals will sit on top of the gold + run-items data shape; this
spec lays the bones so evals are an additive layer, not a rewrite.

### Non-goals

- **Writing Studio results to CatDV.** Studio outputs land in
  `studio_run_items`, never in `annotations`, never in the review queue.
- **Automatic diffing.** Side-by-side render only in v1; an evals layer
  with per-field scoring is a follow-up.
- **Garbage collecting uploaded files.** Uploads are append-only for
  now; retention policy is deferred (§10).
- **The evals layer itself.** Out of scope. The schema is designed to
  accept it as a pure addition.
- **Production runs going through Studio.** Studio is sandbox-only; the
  existing job/review/write pipeline is unchanged.

---

## 2. User-visible behavior

### 2.1 Entry point

A new top-level nav entry **Studio** in the topbar rail
(`_rail.html`), to the right of *Prompts*. Visible always (Studio works
without CatDV — see §3.5).

Studio's landing page is a two-column layout:

- Left rail: list of testbenches (name, item count, last-run date).
  Header has *+ New testbench*.
- Right pane: the selected testbench's folder tree on top, a runs table
  underneath.

### 2.2 Testbench browser

The folder tree is rendered as a collapsible list (one self-referential
parent_id, recursive CTE on read). Each row in a folder is a testbench
item showing:

- a small thumbnail (proxy poster for CatDV clips, first-frame for
  uploads),
- the source label (`CatDV #1234` or `upload: foo.mp4`),
- the gold pill (`gold ✓` / `gold —`),
- a kebab menu (*Edit gold*, *Replace upload*, *Remove from testbench*).

Folder actions: *New subfolder*, *Rename*, *Delete (only if empty)*,
*Add CatDV clip…* (opens an existing clip picker when CatDV is online;
disabled otherwise), *Upload video…* (always available).

A clip can appear in multiple folders within the same testbench (rare
but cheap), and the same CatDV clip ref can live in multiple
testbenches with different gold values — gold is per testbench item,
not per clip (ADR 0026 §c).

### 2.3 Editing gold

Clicking *Edit gold* opens a dialog with a single multi-line text area
labeled *"Manual description (free-form, optional)"*. The text is
stored as JSON under the key `description`:

```json
{ "description": "Inside a textile workshop, daylight, ~1935..." }
```

The schema is JSON so the evals layer can extend it later (`expected.*`
fields, rubrics, scores) without a migration. The dialog only writes
the `description` field today; existing extra keys are preserved on
edit (round-trip safe).

### 2.4 Starting a run

Above the runs table: *Run with prompt ▾* picker (lists all prompts;
expanding a prompt shows its versions with state pills) → *Start*. The
API POSTs to `/api/studio/runs` with `testbench_id` +
`prompt_version_id`. The page reloads to show the new run row in state
`pending`, then `running`, then `completed`.

A run is serial through the testbench items in the order they appear in
the tree (deterministic for reproducibility). SSE events on
`studio_run:{id}` drive per-item status updates (mirrors the existing
`job:{id}` topic).

### 2.5 Run detail

Clicking a run opens a detail page listing each item, status, latency,
and a preview of the output. Statuses:

| Status | Meaning |
|---|---|
| `pending` | not yet processed |
| `resolving` | fetching media |
| `uploading` | pushing to AI store |
| `prompting` | Gemini call in flight |
| `done` | output stored |
| `error` | exception during pipeline (transient, retryable) |
| `unacceptable` | resolver chain exhausted; see `unacceptable_reason` |

`unacceptable` is the new state introduced by Studio (§3.5).
Production jobs never enter it.

### 2.6 Comparison view

`/studio/testbenches/{id}/compare?left=run-A&right=run-B` (either side
can be `gold` instead of a run id). Renders a two-column table, one row
per testbench item. Each cell uses the same Jinja partial the
production annotate view uses for that target_map (so the output
matches what the operator already reads daily). No automatic diff
highlighting in v1.

Items with no output on either side (e.g. `unacceptable` in both runs)
collapse to a single muted row. Items where one side is `unacceptable`
and the other has output show the available side with a `—` placeholder
opposite.

When either side is `gold`, items lacking `gold_json` show a `—`
placeholder labeled *"no reference"*.

### 2.7 No menu entry when CatDV is offline?

Studio works either way. Clip-picker dialogs detect `mode != "online"`
and either disable the *Add CatDV clip…* action or open in
*cache-only* mode (existing pattern — see `services/proxy_resolver.py`
`source="cache-only"`). Uploading is always available.

---

## 3. Architecture

### 3.1 Component map

```
routes/                services/                 repositories/
─────────              ─────────                 ─────────────
studio.py        ──►   studio_runs.py     ──►   testbenches.py
(pages + API)          (worker, queue)          studio_runs.py
                                                testbench_items.py
                       annotator pipeline
                       ─ proxy_resolver ─────►  proxy_cache.py
                       ─ ai_store    ───────►  ai_store_files.py
                       ─ gemini      ───────►  (external)
                       ─ event_bus
```

- **`routes/studio.py`** — Studio pages (`/studio`, `/studio/testbenches/{id}`,
  `/studio/runs/{id}`, `/studio/testbenches/{id}/compare`) and a small
  JSON API (`/api/studio/...`) used by the page actions and SSE.
- **`services/studio_runs.py`** — owns the run worker. Mirrors
  `services/annotator.run_job` over the new tables, calling the same
  per-item primitives (`proxy_resolver.path_for_clip_id`,
  `ai_store.ensure_uploaded`, `gemini.annotate`). One `asyncio.Task` per
  in-flight run; started via the existing app-context lifecycle.
- **`repositories/testbenches.py`**, **`testbench_items.py`**,
  **`studio_runs.py`** — raw SQL over the new tables.
- **Existing services reused as-is:** `proxy_resolver`, `ai_store`,
  `gemini` (the annotator service module's `gemini` adapter),
  `event_bus`, `view_models._fix` for any Czech display strings.

### 3.2 Resolver chain (CatDV clip refs, possibly offline)

`services/studio_runs.resolve_clip_input(item)` for a `source_kind ==
"catdv_clip"` item proceeds in order:

1. **Live archive lookup** — if `mode == "online"`, call
   `archive.get_clip(provider_clip_id)` to refresh metadata and confirm
   the proxy is fetchable.
2. **`proxy_cache` fallback** — if (1) fails or `mode != "online"`, use
   `proxy_resolver` with `source="cache-only"`; fetch a cached
   `clip_snapshot` from the existing `clip_cache` repo for metadata.
3. **`ai_store` fallback** — if no local file exists but the same
   `clip_key = ("catdv", provider_clip_id)` was previously uploaded to
   Gemini, reuse the stored `file_ref` via
   `ai_store.reference_for_gemini`. (The annotator already does this
   for cache hits; Studio just walks the chain further before giving up.)
4. **Mark `unacceptable`** with `unacceptable_reason` describing which
   step failed (e.g. `"catdv offline; proxy not cached; ai_store has no
   reference"`). The run proceeds to the next item.

Uploads (`source_kind == "upload"`) skip step 1; resolver returns the
local file path directly.

### 3.3 Run pipeline (per item)

Identical to `annotator._process_item`, factored into a callable that
both pipelines share. Concretely, a new helper
`services/annotator.process_item_into(out_writer)` extracts the
state-machine logic; `annotator.run_job` and `studio_runs.run` both
call it. The per-item output is written via the `out_writer` callback
(production writes to `annotations` + `review_items`; Studio writes to
`studio_run_items`).

Studio does **not** create `review_items` rows. Studio outputs cannot
be promoted to CatDV writeback — by design.

### 3.4 Reuse of the existing queue

Per ADR 0026 §a, "reuse the existing queue" means reusing the per-item
pipeline machinery (`proxy_resolver`, `ai_store`, `gemini`, the
state-machine in `_process_item`), not literally enqueuing into the
`jobs` table. Studio has its own serial worker for its own tables.
This avoids cross-contamination of sandbox outputs into the production
review/write pipeline.

### 3.5 Boot without CatDV

The app already keeps the CatDV client alive across login failures at
boot (ADR 0023). Studio routes do not call `archive.get_clip` or any
CatDV-touching method at request time except via the resolver chain
in §3.2, which is fail-soft. Production routes (`routes/catdv.py`,
`routes/sync.py`, `routes/jobs.py`) keep their existing CatDV
preconditions; Studio routes do not assert any.

Concretely: nothing in `routes/studio.py` reads
`app_state.mode`, except (a) to disable the *Add CatDV clip* button
in the picker dialog when offline, and (b) to choose the resolver
source. Studio APIs serve 200s regardless of CatDV state.

---

## 4. Backend surface

### 4.1 New routes (`backend/app/routes/studio.py`)

Pages:

| Method | Path | Renders |
|---|---|---|
| `GET`  | `/studio` | Testbench list + (if any selected) folder tree + runs table. |
| `GET`  | `/studio/testbenches/{id}` | Same, with testbench preselected. |
| `GET`  | `/studio/runs/{id}` | Run detail (per-item status table). |
| `GET`  | `/studio/testbenches/{id}/compare?left=...&right=...` | Side-by-side comparison. |

JSON API (used by Alpine page actions + SSE consumers):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/studio/testbenches` | Create testbench. Body: `{name, description?}`. |
| `POST` | `/api/studio/testbenches/{id}:rename` | Rename. |
| `POST` | `/api/studio/testbenches/{id}:archive` | Soft-delete. |
| `POST` | `/api/studio/testbenches/{id}/folders` | Create folder. Body: `{parent_id?, name}`. |
| `POST` | `/api/studio/folders/{id}:rename` | Rename folder. |
| `DELETE` | `/api/studio/folders/{id}` | Delete (only if empty). |
| `POST` | `/api/studio/folders/{id}/items:add_catdv` | Add a CatDV clip. Body: `{provider_clip_id, name}`. |
| `POST` | `/api/studio/folders/{id}/items:add_upload` | Multipart upload; stores under `var/studio_uploads/`. |
| `PUT`  | `/api/studio/items/{id}/gold` | Set gold JSON. Body: `{description}` (extra keys preserved). |
| `DELETE` | `/api/studio/items/{id}` | Remove item from testbench (does not delete the underlying upload file). |
| `POST` | `/api/studio/runs` | Start a run. Body: `{testbench_id, prompt_version_id}`. |
| `POST` | `/api/studio/runs/{id}:cancel` | Cancel an in-flight run. |
| `GET`  | `/api/studio/runs/{id}/events` | SSE stream (`studio_run:{id}` topic). |

All routes serve 200 / 404 / 400 as appropriate, **regardless of
`app_state.mode`**. The only mode-aware behavior is the resolver chain
inside the run worker.

### 4.2 Services

**`backend/app/services/studio_runs.py`**

```python
class StudioRunsService:
    async def start(self, *, testbench_id: int, prompt_version_id: int) -> int: ...
    async def cancel(self, run_id: int) -> None: ...
    async def run(self, run_id: int) -> None:
        """Serial worker. Iterates testbench items in tree order, calls
        the shared per-item pipeline, writes results to studio_run_items."""

    async def _resolve_clip_input(self, item) -> ResolvedInput | Unacceptable: ...
```

`ResolvedInput` carries `local_path | None`, `file_ref | None`, and
`clip_snapshot: dict`. The per-item pipeline reuses these the same way
`annotator._process_item` does.

**`backend/app/services/annotator.py`** — factor `_process_item` into
`process_item(*, resolved, version, gemini, ...) -> AnnotationOutput`
(a dataclass with `structured`, `raw_text`, `prompt_used`, `model`).
`run_job` and `studio_runs.run` both call it.

### 4.3 Repositories

**`repositories/testbenches.py`**

```python
class TestbenchesRepo:
    async def create(self, name: str, description: str | None) -> Testbench: ...
    async def get(self, id: int) -> Testbench: ...
    async def list_active(self) -> list[Testbench]: ...
    async def rename(self, id: int, name: str) -> None: ...
    async def archive(self, id: int) -> None: ...
    async def list_folders(self, testbench_id: int) -> list[TestbenchFolder]: ...
    async def create_folder(self, testbench_id, parent_id, name) -> int: ...
    async def rename_folder(self, id: int, name: str) -> None: ...
    async def delete_folder(self, id: int) -> None: ...
```

**`repositories/testbench_items.py`**

```python
class TestbenchItemsRepo:
    async def add_catdv(self, folder_id, provider_clip_id, name) -> int: ...
    async def add_upload(self, folder_id, upload_path, original_name) -> int: ...
    async def list_for_testbench(self, testbench_id: int) -> list[TestbenchItem]:
        """Returns items in deterministic tree order (folder DFS, then
        insertion order within folder). Used by the run worker."""
    async def set_gold(self, item_id: int, gold_json: dict) -> None: ...
    async def remove(self, item_id: int) -> None: ...
```

**`repositories/studio_runs.py`**

```python
class StudioRunsRepo:
    async def create(self, testbench_id, prompt_version_id) -> int: ...
    async def get(self, id: int) -> StudioRun: ...
    async def list_for_testbench(self, testbench_id: int) -> list[StudioRun]: ...
    async def update_status(self, id, status, *, started_at=None, finished_at=None) -> None: ...
    async def list_items(self, run_id: int) -> list[StudioRunItem]: ...
    async def upsert_item(self, run_id, testbench_item_id, ...) -> None: ...
    async def update_item_status(self, item_id, status, *, error=None, unacceptable_reason=None) -> None: ...
    async def attach_output(self, item_id, *, structured_json, raw_text, prompt_used, model, latency_ms) -> None: ...
```

### 4.4 Schema (`backend/migrations/0011_studio.sql`)

```sql
CREATE TABLE testbenches (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  description  TEXT,
  archived     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);

CREATE TABLE testbench_folders (
  id            INTEGER PRIMARY KEY,
  testbench_id  INTEGER NOT NULL REFERENCES testbenches(id) ON DELETE CASCADE,
  parent_id     INTEGER REFERENCES testbench_folders(id) ON DELETE CASCADE,
  name          TEXT NOT NULL,
  sort_index    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  UNIQUE (testbench_id, parent_id, name)
);
CREATE INDEX idx_tb_folders_parent ON testbench_folders(parent_id);

CREATE TABLE testbench_items (
  id                INTEGER PRIMARY KEY,
  folder_id         INTEGER NOT NULL REFERENCES testbench_folders(id) ON DELETE CASCADE,
  source_kind       TEXT NOT NULL CHECK (source_kind IN ('upload','catdv_clip')),
  -- one of the next two is set depending on source_kind:
  upload_path       TEXT,        -- relative path under var/studio_uploads/
  upload_orig_name  TEXT,
  catdv_provider_clip_id TEXT,
  display_name      TEXT NOT NULL,
  gold_json         TEXT,        -- JSON; NULL = no gold set
  sort_index        INTEGER NOT NULL DEFAULT 0,
  created_at        TEXT NOT NULL,
  CHECK (
    (source_kind = 'upload'     AND upload_path IS NOT NULL AND catdv_provider_clip_id IS NULL) OR
    (source_kind = 'catdv_clip' AND catdv_provider_clip_id IS NOT NULL AND upload_path IS NULL)
  )
);
CREATE INDEX idx_tb_items_folder ON testbench_items(folder_id);

CREATE TABLE studio_runs (
  id                  INTEGER PRIMARY KEY,
  testbench_id        INTEGER NOT NULL REFERENCES testbenches(id),
  prompt_version_id   INTEGER NOT NULL REFERENCES prompt_versions(id),
  status              TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed','cancelled')),
  created_at          TEXT NOT NULL,
  started_at          TEXT,
  finished_at         TEXT,
  notes               TEXT
);
CREATE INDEX idx_studio_runs_testbench ON studio_runs(testbench_id, created_at DESC);

CREATE TABLE studio_run_items (
  id                  INTEGER PRIMARY KEY,
  run_id              INTEGER NOT NULL REFERENCES studio_runs(id) ON DELETE CASCADE,
  testbench_item_id   INTEGER NOT NULL REFERENCES testbench_items(id),
  status              TEXT NOT NULL CHECK (status IN (
                        'pending','resolving','uploading','prompting',
                        'done','error','unacceptable')),
  error               TEXT,
  unacceptable_reason TEXT,
  structured_json     TEXT,           -- Gemini structured output
  raw_text            TEXT,           -- Gemini raw response
  prompt_used         TEXT,           -- rendered prompt body (with duration anchor)
  model               TEXT,
  latency_ms          INTEGER,
  started_at          TEXT,
  finished_at         TEXT,
  UNIQUE (run_id, testbench_item_id)
);
CREATE INDEX idx_studio_run_items_run ON studio_run_items(run_id);
```

Notes on the schema:

- No FK from `testbench_items.catdv_provider_clip_id` to any local
  clips table. CatDV clip identity lives in the archive; we only keep
  the string ref. Resolver chain handles the case where the clip is
  unreachable.
- `gold_json` is `TEXT` at the storage level but always written as JSON
  (`json_valid()` is not checked in v1 because SQLite makes it awkward;
  the route layer validates).
- `studio_run_items.testbench_item_id` is **not** snapshotted as a copy
  of the item's source — the FK is enough because uploads are
  append-only and CatDV clip refs are stable strings. The output side
  (`structured_json`, `prompt_used`, `model`) is fully captured per
  item, so a run can be re-rendered without re-resolving the source.
- Cancellation deletes the in-flight run item rows that are still
  `pending` and leaves any completed/`unacceptable` items intact, the
  same way `jobs` cancellation works.

### 4.5 Settings additions (`backend/app/settings.py`)

```python
studio_uploads_dir: Path = Path("var/studio_uploads")   # relative to repo root
studio_max_upload_mb: int = 500                          # multipart limit
```

No new external credentials; Studio reuses the existing `gemini`
service and the existing `ai_store`.

---

## 5. Frontend

### 5.1 Pages

- `templates/pages/studio.html` — landing layout. Server-rendered list
  of testbenches + folder tree. Folder/item actions go through small
  Alpine components.
- `templates/pages/studio_run.html` — run detail page. Per-item table
  with SSE-driven status updates (reuses the `events.js` SSE helper
  already used by jobs).
- `templates/pages/studio_compare.html` — comparison view. Two-column
  table; each cell renders via the same Jinja include used in
  `_anno_panels.html` so the output looks identical to production
  annotate panels.
- Partials:
  - `_studio_testbench_list.html`
  - `_studio_folder_tree.html` (renders one level; recurses via include)
  - `_studio_runs_table.html`
  - `_studio_run_item_row.html` (SSE-swappable)
  - `_studio_compare_cell.html` (delegates to the production cell partial)

### 5.2 Static JS

`backend/app/static/studio.js` — Alpine components:

- `studioPage()` — testbench selection, folder expand/collapse,
  add-folder / add-item dialog state.
- `studioRunView(runId)` — wires SSE on `studio_run:{id}` and swaps
  per-item rows.
- `studioGoldDialog(itemId)` — open/save the description editor; PUTs
  to `/api/studio/items/{id}/gold`, preserving any extra JSON keys
  already present.

No new audio / video processing components; the player and annotate
components are not reused (Studio doesn't play media inline in v1 —
clicking a thumbnail in the run detail opens the existing
`clip_detail` page in a new tab when CatDV is online).

### 5.3 Rail

Add a Studio tab to `_rail.html`. Active when path starts with
`/studio`.

---

## 6. Error handling

| Failure | Effect |
|---|---|
| Upload exceeds `studio_max_upload_mb` | 413 with message; upload not persisted. |
| Upload write to `var/studio_uploads/` fails (disk full) | 500 with operator-facing message; row not inserted; partial file removed. |
| Folder delete with children | 409 with message; UI prevents the action anyway. |
| Run started while another run is in-flight for the same testbench | Allowed. Multiple concurrent runs are fine; they each go through the same `gemini` service which has its own rate limits. |
| Resolver chain exhausted for an item | Item set to `unacceptable` with `unacceptable_reason`; run continues. |
| Gemini call raises | Item set to `error` with the exception string; run continues. The run's final status is `failed` if any item is `error` (matches `jobs` semantics). `unacceptable` does not flip the run to `failed`. |
| Cancellation requested mid-run | Set run to `cancelled`; the worker breaks at the next item boundary. In-flight Gemini calls are not killed — the response is discarded on completion. |
| Gold JSON parse error on PUT | 400 with message; existing gold not modified. |
| Compare view with one run cancelled / failed | Render available items; missing items show `—`. |

---

## 7. Testing

### 7.1 Unit / integration (TDD per project default)

- `tests/repositories/test_testbenches_repo.py` — CRUD, folder tree
  recursion, unique-name constraints, cascade on testbench delete.
- `tests/repositories/test_testbench_items_repo.py` — both source
  kinds, gold round-trip preserves unknown keys, item-order
  determinism within a folder, CHECK constraint enforces
  source_kind / column pairing.
- `tests/repositories/test_studio_runs_repo.py` — run lifecycle,
  per-item status transitions, unique `(run_id, testbench_item_id)`.
- `tests/services/test_studio_runs.py`:
  - resolver chain hits live → cache → ai_store → unacceptable, with
    the appropriate `unacceptable_reason` at each fallout point;
  - run worker iterates in tree order;
  - per-item pipeline reuses `process_item` (same output shape as
    the production annotator path);
  - cancellation stops at the next item boundary;
  - run finishes `completed` when all items `done`/`unacceptable`;
    `failed` if any item `error`.
- `tests/services/test_annotator_process_item.py` — the factored-out
  `process_item` is callable without `JobsRepo` and returns the
  expected `AnnotationOutput` dataclass.
- `tests/routes/test_studio.py`:
  - 200s when CatDV is unconfigured / offline;
  - upload happy path persists under `studio_uploads_dir`;
  - PUT `/gold` preserves unknown JSON keys;
  - SSE stream emits per-item state transitions;
  - compare endpoint renders both sides and `—` placeholders;
  - cannot delete a non-empty folder.

### 7.2 Manual verification checklist

The full Studio flow needs a real Gemini key. Run with CatDV both
online and offline to cover the resolver chain.

- [ ] Studio nav link is visible regardless of `mode`.
- [ ] Create testbench, create nested folders, rename/delete folders.
- [ ] Upload an MP4 (>100 MB) — appears with correct thumbnail; file
      lands under `var/studio_uploads/`.
- [ ] Add a CatDV clip ref while CatDV is online; verify thumbnail
      poster matches the production clip list.
- [ ] Set gold via *Edit gold*; reopen the dialog and confirm the text
      round-trips.
- [ ] Start a run with prompt v1; per-item statuses tick through
      `resolving → uploading → prompting → done` over SSE.
- [ ] Start a second run with prompt v2 of the same prompt while v1
      is still in flight; verify both progress independently.
- [ ] Compare run-A vs run-B side-by-side; the output renders match
      the production annotate panel rendering.
- [ ] Compare run-A vs `gold`; items without gold show `— no reference`.
- [ ] Disconnect CatDV (`/connection:offline`), then start a run that
      includes both an upload and a previously-cached CatDV clip.
      Uploads complete; cached CatDV clip resolves via proxy_cache.
- [ ] Disconnect CatDV and include a CatDV clip whose proxy was never
      cached; verify the item ends `unacceptable` with a sensible
      `unacceptable_reason` and the rest of the run completes.
- [ ] Cancel an in-flight run; subsequent items don't start; the run
      ends `cancelled`; previously-completed items remain.
- [ ] Restart the app mid-run (SIGTERM, then start again); the run is
      either resumable or cleanly marked `failed` — see open item §10.

---

## 8. Files touched

### New

- `backend/app/routes/studio.py`
- `backend/app/services/studio_runs.py`
- `backend/app/repositories/testbenches.py`
- `backend/app/repositories/testbench_items.py`
- `backend/app/repositories/studio_runs.py`
- `backend/app/models/studio.py`  (Testbench, TestbenchFolder,
  TestbenchItem, StudioRun, StudioRunItem, AnnotationOutput dataclass)
- `backend/app/static/studio.js`
- `backend/app/templates/pages/studio.html`
- `backend/app/templates/pages/studio_run.html`
- `backend/app/templates/pages/studio_compare.html`
- `backend/app/templates/pages/_studio_testbench_list.html`
- `backend/app/templates/pages/_studio_folder_tree.html`
- `backend/app/templates/pages/_studio_runs_table.html`
- `backend/app/templates/pages/_studio_run_item_row.html`
- `backend/app/templates/pages/_studio_compare_cell.html`
- `backend/migrations/0011_studio.sql`
- `docs/specs/2026-05-26-prompt-studio-design.md` (this file)
- `docs/adr/0026-prompt-studio.md`

### Modified

- `backend/app/services/annotator.py` — factor `_process_item` into
  shared `process_item` callable; existing `run_job` unchanged in
  behavior.
- `backend/app/context.py` — register `StudioRunsService`, wire repos.
- `backend/app/main.py` — register the Studio router.
- `backend/app/settings.py` — `studio_uploads_dir`, `studio_max_upload_mb`.
- `backend/app/templates/pages/_rail.html` — Studio nav entry.
- `docs/decisions.md` — index entry for ADR 0026.
- `docs/ARCHITECTURE.md` — add Studio symptom row to the
  "Symptom → first file to read" table.

---

## 9. Open items for implementation

Pinned for the plan; not blockers for the design.

- **Crash recovery.** What happens to in-flight `studio_runs` rows
  across a SIGTERM? The simplest answer mirrors `jobs.reset_transient`
  on startup — sweep any `running` runs to `failed` and any in-flight
  items to `error`. Decide whether resume-from-where-we-left-off is
  worth the complexity (probably not for v1, given runs are small).
- **`process_item` factoring.** The shared callable's signature has
  to satisfy both `JobsRepo` (writes `annotations` + `review_items`
  + per-item state) and Studio (writes `studio_run_items` only). The
  obvious shape is "return an `AnnotationOutput` dataclass, let the
  caller persist it." Confirm at implementation time that nothing in
  `_process_item` mid-flow writes to `jobs_repo` in a way the Studio
  caller would need to mock.
- **Upload retention / GC.** Out of scope for v1 (ADR 0026 §g).
  Implementation note for later: a SQL `EXCEPT` query of upload paths
  vs. referenced paths in `testbench_items` ∪ `studio_run_items`
  surfaces orphans.
- **Gold JSON shape evolution.** Today writes `{"description": "..."}`.
  The dialog UI in §2.3 is hard-coded to that key. When evals lands,
  the dialog grows fields for `expected.*`; the JSON column accepts
  it without migration.
- **Concurrent run worker count.** v1 runs one worker per active run
  (an `asyncio.Task` per run). If many runs are kicked off in parallel,
  Gemini rate limits will start gating; revisit if that becomes a
  bottleneck.
- **CatDV clip picker UX when offline.** Decide whether the picker
  uses `cache-only` listing (clips with a cached proxy) or is fully
  disabled. Probably cache-only listing, but defer to the UI plan.
- **Compare view for >2 runs.** v1 supports exactly two sides
  (left/right). N-way compare is a follow-up.
