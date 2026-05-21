# Prompt Management — Spec

**Date:** 2026-05-21
**Scope:** Add a Prompts management surface (left-nav item + list/detail page) backed by a versioned data model. Replace the existing single-row `templates` table with `prompts` + `prompt_versions`. Out of scope: experiments, prompt execution (Gemini calls), prompt-execution badges on clips, diff viewer.

## Background

The annotator currently stores each prompt as a single mutable row in `templates` (`name`, `description`, `prompt`, `output_schema`, `target_map`, `model`, `archived`). There is no notion of versions, no way to keep a known-good prompt frozen while iterating on a successor, and no first-class management UI — prompts are created via REST routes (`/api/templates`) and seeded from `backend/seeds/default_template.json`.

The Claude Design mockup (extracted to `/tmp/design/catdv-annotator/`, primary file `project/screens.jsx` → `TemplatesScreen`) reimagines this as a left-rail list + right-pane editor with per-version state pills (`production` / `draft`), a model picker, a kebab menu for create-new-version / duplicate / export / archive, and dirty-state Save. The mock's "Experiment" action and the Studio comparison view are explicitly out of scope here.

This spec ports the management UI faithfully and introduces the versioning model. Prompt execution remains on the existing pipeline (annotator + jobs) and is rewired to reference `prompt_version_id` instead of `template_id`.

## Goals

1. Manage prompts as identified entities with versioned bodies. Promote drafts to production safely; never silently mutate a production-pinned prompt.
2. Visual + interaction parity with the design mock's prompt editor (320px list + detail pane, version/state tags, model picker, kebab actions, dirty Save).
3. Drop the `templates` table; rewire annotator/jobs/review/seed to the new model in the same change.
4. Server-rendered Jinja + HTMX, matching the rest of the app. Alpine.js only for trivially-small client state (dirty tracking, kebab open/close).

## Non-Goals

- Running a prompt against clips (job execution, Gemini calls).
- "Experiment" view, Studio comparison, tweaks panel from the design mock.
- Prompt-execution dots/badges on clips.
- Version diff viewer.
- Re-running existing annotations after a version is promoted.
- Live multi-user editing concerns (single-user local app).
- Keeping the old `templates` table or `/api/templates` routes for backwards compatibility — they are removed.

## Domain model

A **prompt** is the long-lived identity (`name`, `description`). A **prompt version** is the snapshot of editable content (`body`, `target_map`, `output_schema`, `model`) plus a `state` that controls mutability and promotion.

**Version states:**

- `draft` — mutable. Edits saved in place.
- `production` — immutable. At most one per prompt. Used by the annotator.
- `archived` — immutable. Was once production; superseded by a newer promote or deliberately archived.

**Transitions:**

```
   create new version
        │
        ▼
     draft ──── promote ─────▶ production
        │                          │
        │ archive (rare)           │ auto-archive when another draft is promoted
        ▼                          ▼
     archived ◀───────────── archived
```

**Invariants:**

- At most one `production` version per prompt (enforced by partial unique index).
- `body`, `target_map`, `output_schema`, `model` are mutable iff `state = 'draft'`. PUT against a non-draft version returns `409 Conflict` with `error_code: "version_immutable"`.
- Promoting a draft is atomic: in one transaction, the draft becomes `production` and the previously-production version (if any) becomes `archived`.
- Archiving a prompt is a soft-delete on the prompt row; individual version states are preserved so restore yields the same production version.
- `version_num` is monotonic per prompt, starts at 1, never reused.

**Initial state of v1.** Newly-created prompts start at `v1 = draft` (you have to explicitly promote to start using it). The migration backfill is the one exception: each existing `templates` row becomes `v1 = production` so the live annotator pipeline keeps working without a manual promote step. The `seed_default_prompt` loader matches the migration's behavior — seeded prompt is created as `v1 = production`.

**Prompt-level archive vs version-level archive** are distinct. Kebab "Archive" archives the prompt (all versions become invisible to default views). Version-level `archived` state is a consequence of promotion, not user-driven from the UI in this scope.

## Schema (migration `0009_prompts_and_versions.sql`)

```sql
-- New tables
CREATE TABLE prompts (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL UNIQUE,
  description   TEXT,
  archived      INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE prompt_versions (
  id              INTEGER PRIMARY KEY,
  prompt_id       INTEGER NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
  version_num     INTEGER NOT NULL,
  state           TEXT NOT NULL CHECK (state IN ('draft','production','archived')),
  body            TEXT NOT NULL,
  target_map      TEXT NOT NULL,           -- JSON
  output_schema   TEXT NOT NULL,           -- JSON
  model           TEXT NOT NULL,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  UNIQUE (prompt_id, version_num)
);

CREATE UNIQUE INDEX idx_one_prod_per_prompt
  ON prompt_versions(prompt_id) WHERE state = 'production';

CREATE INDEX idx_prompt_versions_prompt ON prompt_versions(prompt_id);

-- Data backfill: each existing templates row -> 1 prompt + 1 v1@production
INSERT INTO prompts (id, name, description, archived, created_at, updated_at)
  SELECT id, name, description, archived, created_at, updated_at FROM templates;

INSERT INTO prompt_versions
  (prompt_id, version_num, state, body, target_map, output_schema, model, created_at, updated_at)
  SELECT id, 1, 'production', prompt, target_map, output_schema, model, created_at, updated_at
  FROM templates;

-- Rebuild annotations: template_id -> prompt_version_id
-- (SQLite-compatible: build new table, copy, drop, rename, recreate indexes/triggers/FTS)
CREATE TABLE annotations_new (
  id                 INTEGER PRIMARY KEY,
  catdv_clip_id      INTEGER NOT NULL,
  catdv_clip_name    TEXT NOT NULL,
  prompt_version_id  INTEGER NOT NULL REFERENCES prompt_versions(id),
  job_id             INTEGER REFERENCES jobs(id),
  model              TEXT NOT NULL,
  prompt_used        TEXT NOT NULL,
  raw_response       TEXT NOT NULL,
  structured_output  TEXT NOT NULL,
  clip_snapshot      TEXT NOT NULL,
  created_at         TEXT NOT NULL
);
INSERT INTO annotations_new
  SELECT a.id, a.catdv_clip_id, a.catdv_clip_name,
         pv.id, a.job_id, a.model, a.prompt_used, a.raw_response,
         a.structured_output, a.clip_snapshot, a.created_at
  FROM annotations a
  JOIN prompt_versions pv ON pv.prompt_id = a.template_id AND pv.version_num = 1;
DROP TABLE annotations;     -- triggers + FTS dropped with it
ALTER TABLE annotations_new RENAME TO annotations;
CREATE INDEX idx_annotations_clip ON annotations(catdv_clip_id);
CREATE INDEX idx_annotations_prompt_version ON annotations(prompt_version_id);
-- Recreate annotations_fts virtual table + ai/ad triggers as in 0001.

-- Same rebuild dance for jobs: template_id -> prompt_version_id
-- (smaller table, follows the same shape)

DROP TABLE templates;
```

Migration is non-reversible by design. Existing prompts and their annotations are preserved end-to-end.

## API surface

REST namespace: `/api/prompts`. Verb-style sub-paths (`:archive`, `:promote`) used for state mutations to keep them visually distinct from RESTful CRUD.

```
GET    /api/prompts                          → list active prompts
                                               query: ?archived=1 → archived-only view
GET    /api/prompts/{id}                     → prompt + all versions (versions desc by version_num)
POST   /api/prompts                          → create prompt + initial v1 (state=draft)
                                               body: { name, description, body, target_map,
                                                       output_schema, model }
PATCH  /api/prompts/{id}                     → update name and/or description only
                                               (409 on UNIQUE name collision — UI surfaces the error inline)
POST   /api/prompts/{id}:archive             → soft-delete prompt
POST   /api/prompts/{id}:restore             → un-archive prompt
POST   /api/prompts/{id}:duplicate           → create new prompt with v1 (state=draft) cloned
                                               from source's current production version
                                               (fallback: latest version).
                                               Name = "Copy of <name>"; if that exists (incl.
                                               archived prompts — UNIQUE is global), append " (2)",
                                               " (3)", … until free.

POST   /api/prompts/{id}/versions            → create new draft version
                                               body: { from_version_id?: int }  (default: current prod, fallback latest)
GET    /api/prompts/{id}/versions/{vid}      → single version
PUT    /api/prompts/{id}/versions/{vid}      → edit version (draft only; 409 otherwise)
                                               body: { body?, target_map?, output_schema?, model? }
POST   /api/prompts/{id}/versions/{vid}:promote → state -> production; demotes previous prod -> archived
GET    /api/prompts/{id}/versions/{vid}/export  → JSON export for the "Export JSON" kebab item.
                                                   Kebab is on the prompt; "Export JSON" exports
                                                   the version currently shown in the detail pane.
                                                   Shape: { prompt: {name, description},
                                                            version: {version_num, state, body,
                                                                     target_map, output_schema, model} }
```

Response shapes (Pydantic):

```python
class PromptOut(BaseModel):
    id: int
    name: str
    description: str | None
    archived: bool
    created_at: str
    updated_at: str
    current_production_version_id: int | None   # convenience for list rail
    latest_version_id: int                       # always present after create
    versions: list[PromptVersionOut] | None      # populated by GET /{id}, omitted in list

class PromptVersionOut(BaseModel):
    id: int
    prompt_id: int
    version_num: int
    state: Literal["draft", "production", "archived"]
    body: str
    target_map: dict
    output_schema: dict
    model: str
    created_at: str
    updated_at: str
```

`409 Conflict` body: `{"error_code": "version_immutable", "message": "cannot edit version in state X"}`.

## Page routes + UI

Server-rendered, HTMX-driven. New left-nav entry "Prompts" → `/prompts`.

```
GET  /prompts                       → full page; selects current production of first prompt by default
GET  /prompts/_list                 → HTMX partial: left-rail list (320px)
GET  /prompts/{id}                  → full page with that prompt + its current version selected
GET  /prompts/{id}/_detail          → HTMX partial: right-pane detail
                                       query: ?version_id=<vid> (default: current production, fallback latest)
GET  /prompts/archived              → archived prompts view (same layout, list filtered)
```

Action endpoints used by the UI (HTMX targets) — these wrap the same JSON APIs but return HTML partials:

```
POST /prompts/{id}/_save_version    → PUT /api/prompts/{id}/versions/{vid} + re-render detail
POST /prompts/{id}/_new_version     → POST /api/prompts/{id}/versions + re-render detail (new draft selected)
POST /prompts/{id}/_promote         → POST .../versions/{vid}:promote + re-render detail
POST /prompts/{id}/_duplicate       → POST .../:duplicate + HX-Redirect to new prompt
POST /prompts/{id}/_archive         → POST .../:archive + HX-Redirect to /prompts
```

This split (REST JSON under `/api/`, HTMX HTML actions under page paths) follows the existing pattern in `routes/cache.py` and `routes/pages.py`.

## Templates

```
pages/prompts.html              → full page (header + 2-col body)
pages/_prompts_list.html        → left-rail list (rows: name + desc)
pages/_prompt_detail.html       → right pane container
pages/_prompt_detail_header.html → title row with v/state tags, model picker, Save, kebab
pages/_prompt_menu.html         → kebab popover items
pages/_prompt_version_picker.html → version dropdown (vN tag → list of versions)
```

CSS lives in the existing single stylesheet alongside `.detail`, `.cache-tbl`, etc. New classes mirror the design's: `.prompts-page`, `.tmpl-row`, `.tmpl-menu`, `.tmpl-menu-item`, `.tag.good`, `.tag.accent`, `.tag.mono-cell`, `.model-picker`, `.model-menu`, `.json-editor`. Pixel-faithful to `screens.jsx` `TemplatesScreen` (lines 199–370) and `styles.css`.

## Alpine component

`backend/app/static/promptEditor.js`:

```js
Alpine.data("promptEditor", (initial) => ({
  // copies of server-rendered values for dirty-tracking
  initial: { body: initial.body, target_map: initial.target_map_text,
             output_schema: initial.output_schema_text, model: initial.model },
  draft:   { ...initial },
  state:   initial.state,
  menuOpen: false,
  modelOpen: false,

  get dirty() {
    return this.state === "draft" && (
      this.draft.body !== this.initial.body ||
      this.draft.target_map !== this.initial.target_map ||
      this.draft.output_schema !== this.initial.output_schema ||
      this.draft.model !== this.initial.model
    );
  },

  get canEdit() { return this.state === "draft"; },

  // HTMX submits via hx-post; component just controls open/close + dirty styling.
}));
```

Server renders `target_map` and `output_schema` as pretty-printed JSON strings into `<textarea>`s; client only tracks dirtiness. JSON parse/validation happens server-side on save.

## Backend layering

New / changed files:

- **NEW** `backend/app/models/prompt.py` — `Prompt`, `PromptVersion`, `PromptVersionState` Pydantic models. `TargetMap` and `TargetEntry` move here from `models/template.py`.
- **NEW** `backend/app/repositories/prompts.py` — `PromptsRepo`:
  - `list_active`, `list_archived`, `get_with_versions`, `get_version`
  - `create_with_initial_version`, `update_metadata`, `archive`, `restore`, `duplicate`
  - `create_version` (from source version), `update_version` (draft-only), `promote_version` (atomic with demote)
- **NEW** `backend/app/routes/prompts.py` — REST API.
- **NEW** templates listed above.
- **NEW** `backend/app/static/promptEditor.js`.
- **CHANGED** `backend/app/routes/pages.py` — add `/prompts` family of handlers.
- **CHANGED** `backend/app/services/annotator.py` — `job.prompt_version_id` lookup via `PromptsRepo.get_version`.
- **CHANGED** `backend/app/routes/jobs.py` — pass `prompts_repo`; `job.template_id` → `job.prompt_version_id` everywhere.
- **CHANGED** `backend/app/routes/review.py` — same rename.
- **CHANGED** `backend/app/services/target_map.py` — import `TargetMap`/`TargetEntry` from new module.
- **CHANGED** `backend/app/services/write_queue.py` — same import.
- **CHANGED** `backend/app/context.py` — drop `templates_repo`, add `prompts_repo`.
- **CHANGED** `backend/app/seed.py` — `seed_default_template` → `seed_default_prompt` (creates 1 prompt + 1 v1 @ production from `seeds/default_template.json`). Seed file stays at that path; only the loader changes.
- **CHANGED** `backend/app/main.py` — register prompts router, drop templates router.
- **CHANGED** `backend/app/templates/pages/_rail.html` — add Prompts nav item.
- **CHANGED** `backend/app/models/job.py` — `template_id` → `prompt_version_id` (and the schema column matches).
- **CHANGED** `backend/app/models/annotation.py` — same rename.
- **REMOVED** `backend/app/models/template.py`, `backend/app/repositories/templates.py`, `backend/app/routes/templates.py`.

`backend/seeds/default_template.json` content is left as-is (still the seed); only the loader changes. A follow-up could rename it to `default_prompt.json` for consistency — not in this scope.

## Testing

Per project convention (TDD by default, integration tests over mocks per global rules):

**Repository (`tests/test_prompts_repo.py`)** — sqlite in-memory:

- create_with_initial_version yields v1 with `state=draft`.
- update_version on draft persists; on production raises `VersionImmutableError`; on archived raises `VersionImmutableError`.
- promote_version: draft → production; previous production → archived; both in one txn.
- partial unique index actually rejects inserting a second `production` row for the same prompt.
- archive then restore is idempotent and preserves version states.
- duplicate copies current production into v1@draft of a new prompt with name `Copy of <orig>`; on second duplicate, appends `(2)`.
- create_version with explicit `from_version_id` copies that version; without, copies current production; without prod, copies latest.
- version_num is monotonic per prompt across creates, archives, promotes.

**Routes (`tests/test_prompts_routes.py`)** — FastAPI test client:

- Full CRUD happy path: POST → GET → PATCH → POST :duplicate → POST :archive.
- 404 for unknown prompt/version IDs.
- 409 on PUT a production or archived version.
- `?archived=1` filter shows only archived.
- Export returns the expected JSON shape (body + target_map + output_schema + model).

**Migration (`tests/test_migration_0009.py`)**:

- Build a fixture DB at migration 0008 with: 2 `templates` rows, 1 `annotation` referencing one of them, 1 `job` referencing the other. Apply 0009. Assert: 2 `prompts`, 2 `prompt_versions` (both v1@production), annotation.prompt_version_id points correctly, job.prompt_version_id points correctly, `templates` table is gone, FTS over annotations still works.

**SSR smoke (`tests/test_prompts_pages.py`)**:

- `/prompts` returns 200 and contains the seeded prompt name.
- `/prompts/{id}/_detail` returns 200 with the body textarea populated.
- `/prompts/archived` returns 200 and lists only archived prompts.

**Annotator regression**:

- Existing annotator tests updated to construct jobs with `prompt_version_id` instead of `template_id`. Behavior unchanged.

No client-side JS tests — Alpine logic in `promptEditor.js` stays trivial enough to verify by inspection (existing project pattern).

## Risks + open questions

- **JSON validation feedback.** Saving an invalid JSON `target_map` or `output_schema` returns 422; the UI should show the error inline, not lose the edit. Plan: server returns the error message and the unsaved values; HTMX swaps an error pill above the offending textarea.
- **Promote with unsaved dirty edits.** UI disables Promote when dirty. Save first, then Promote. Two clicks intentional.
- **Migration on a populated DB.** The local instance has ≥ 1 templates row and existing annotations referencing it. Migration must run cleanly there before merging. Plan: add a manual smoke-test step in the PR description: `cp data/app.db /tmp/pre.db; ./run.sh --migrate-only; sqlite3 data/app.db 'SELECT COUNT(*) FROM prompts, prompt_versions, annotations';`.
- **Seed file naming.** `default_template.json` is now misleading. Renaming is a follow-up.
- **Concurrent edits.** Single-user app — not handling write-skew between two browser tabs. If both save the same draft, last write wins.
