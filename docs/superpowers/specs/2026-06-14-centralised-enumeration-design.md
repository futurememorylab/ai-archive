# Centralised enumeration + Admin console — design spec

**Issue:** [#13 Centralised enumeration](https://github.com/futurememorylab/ai-archive/issues/13)
**Date:** 2026-06-14
**Status:** Draft

## Problem

Value lists that mean one thing are duplicated across the codebase. The worst
offender is the **Gemini generation-model list**, which lives independently in
three places that drift out of sync:

- `backend/app/settings.py` — `gemini_model` default (`"gemini-2.5-flash-lite"`).
- `backend/app/services/pricing.py` — per-model rate cards (only covers the
  `gemini-2.5-*` family; the `gemini-3.*` models in the dropdown have no rates).
- `backend/app/templates/pages/_prompt_new.html` — a hardcoded Jinja list of 8
  model IDs rendered into the `<select>`.

When a new model ships, a human has to remember all three. The issue asks for:

1. A central source of truth for enumerations used in multiple places.
2. A service that serves enumerations to **both** backend and frontend.
3. Runtime configuration — a user can add a newly-available model or remove an
   unsupported one **without a code change**.
4. CLAUDE.md guidance on how to use and handle enums.

## Scope

### In scope

- A self-describing, DB-backed enumeration registry (two tables).
- `EnumService` as the single read/write API, wired onto `CoreCtx` (offline-safe).
- The **generation-model catalog** migrated to the registry and made editable.
- A new **Admin console** (bottom-pinned rail icon) with one data-driven tab
  today (editable enumerations), built to grow (user management, etc.).
- Rewiring the three drift sites to read from `EnumService`.
- A new CLAUDE.md "Enumerations" section.
- A linked follow-up GitHub issue cataloguing code-coupled enums to centralise
  later.

### Out of scope (and why)

- **Code-coupled status enums** (`JobStatus`, `ItemStatus`, review `decision`,
  prompt version state, cache/anno filters, toast levels). Each value has a
  matching code branch; making them user-editable is incoherent (a user-added
  status no code handles silently breaks logic). They stay as `Literal` types in
  `models/`. The follow-up issue tracks *centralising* (not making editable) the
  ones that are duplicated.
- **Live-session model + voice.** Single settings today, not a dropdown. Left as
  plain settings; the registry pattern can absorb them later if needed.
- **Editing pricing rate cards.** Rates stay in `pricing.py`; the console only
  *surfaces* when a model has no rate card (a warning badge). Existing
  warn-and-proceed behaviour is unchanged.

## Architecture

```
                    ┌─────────────────────────────────────────┐
                    │  Admin console  /admin  (data-driven)    │
                    │  one section per editable enum_definition │
                    └──────────────┬──────────────────────────┘
                                   │ HTMX CRUD (partials + toast)
  Prompt "New" dropdown ──read──┐  │
  prompts.py default model ─────┤  │
  GET /api/enums/<key> (JSON) ──┤  │
                                ▼  ▼
                        ┌──────────────────┐
                        │   EnumService    │  (on CoreCtx — DB-only, offline-safe)
                        │  definitions()   │
                        │  values(key)     │
                        │  add/remove/...  │  (writes refused when editable = 0)
                        └────────┬─────────┘
                                 │
                        ┌────────▼─────────┐
                        │  EnumValuesRepo  │  (async, aiosqlite, commit flag; leaf)
                        └────────┬─────────┘
                                 │
              ┌──────────────────▼───────────────────┐
              │  enum_definitions   +   enum_values   │  (seeded in migration)
              └───────────────────────────────────────┘
```

## Components

### 1. Schema — two tables

New migration `backend/migrations/00NN_enumerations.sql`:

```sql
CREATE TABLE enum_definitions (
  key         TEXT PRIMARY KEY,            -- 'gemini_generation_model'
  name        TEXT NOT NULL,               -- 'Gemini generation models' (section title)
  description TEXT,                         -- help text under the heading
  editable    INTEGER NOT NULL DEFAULT 0,  -- 1 = admin may add/remove values
  created_at  TEXT NOT NULL
);

CREATE TABLE enum_values (
  enum_key   TEXT NOT NULL REFERENCES enum_definitions(key),
  value      TEXT NOT NULL,                -- 'gemini-2.5-flash'
  label      TEXT,                         -- optional display name
  enabled    INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (enum_key, value)
);
CREATE INDEX idx_enum_values_key ON enum_values(enum_key, sort_order);
```

**Seeded in the same migration** (one-time, so later user edits persist and are
never clobbered):

- One `enum_definitions` row: `gemini_generation_model`,
  name `Gemini generation models`, `editable = 1`.
- Eight `enum_values` rows — the models currently hardcoded in `_prompt_new.html`
  (`gemini-2.5-pro`, `gemini-2.5-flash`, `gemini-2.5-flash-lite`,
  `gemini-3-flash-preview`, `gemini-3.1-pro-preview`, `gemini-3.1-flash-lite`,
  `gemini-3.1-flash-lite-preview`, `gemini-3.5-flash`) with ascending
  `sort_order`, all `enabled = 1`.

Constant defaults also live in code (`backend/app/enums/defaults.py`) as the
authoritative seed source the migration is generated from, and for tests.

### 2. `EnumValuesRepo` — `backend/app/repositories/enum_values.py`

Follows the existing repo pattern (async, takes `aiosqlite.Connection` + a
`commit` flag, row→model conversion). Leaf layer — must not import services
(import-linter contract).

Methods:

- `definitions(editable_only: bool = False) -> list[EnumDefinition]`
- `values(enum_key: str, include_disabled: bool = False) -> list[EnumValue]`
- `definition(enum_key: str) -> EnumDefinition | None`
- `add_value(enum_key, value, label, *, commit)` — raises on PK conflict.
- `set_enabled(enum_key, value, enabled, *, commit)`
- `remove_value(enum_key, value, *, commit)`
- `count_enabled(enum_key) -> int`

Single-key reads; no list-of-keys fan-out, so no `chunked_in_clause` needed.

### 3. `EnumService` — `backend/app/services/enum_service.py`

The single source of truth for reads and the gatekeeper for writes. Pydantic
models `EnumDefinition` and `EnumValue` (`value`, `label`, `enabled`,
`sort_order`).

- `generation_models(enabled_only: bool = True) -> list[EnumValue]` — convenience
  wrapper over `values("gemini_generation_model", ...)`.
- `definitions(editable_only=False)` / `values(key, ...)` — generic reads used by
  the console and the JSON API.
- `add_value(key, value, label=None)` / `set_enabled(...)` / `remove_value(...)`:
  - Refuse the write with a clear error when the definition's `editable = 0`.
  - **Last-enabled guard:** refuse a remove/disable that would drop the enabled
    count to zero (the consuming dropdown must never be empty).
  - Surface duplicate-add (PK conflict) as a clean "already in the list" error.
- Errors raised here are rendered to users via
  `backend/app/services/errors.py::humanise`.

Wired onto **`CoreCtx`** (it needs only `db` + repo), so every route reaches it
via `Depends(get_core_ctx)` and it works when CatDV/GCS are offline. No new
`Optional` service fields on a god-context (respects ADR 0047).

### 4. Consumption rewiring (kill the drift)

- **Prompt "New" dropdown** — `_prompt_new.html` stops hardcoding the list; the
  prompt-new route passes `generation_models = ctx.enum_service.generation_models()`
  into the template context, and the `<select>` loops over it (`value` +
  `label or value`).
- **Default model fallback** — `routes/pages/prompts.py` (lines ~66/82) uses the
  first enabled catalog entry from `EnumService` instead of the hardcoded
  `"gemini-2.5-flash-lite"`. `settings.gemini_model` remains the env-level
  override but is no longer the dropdown's source.
- **JSON API** — `GET /api/enums/{key}` returns the enabled values as JSON for
  any frontend/JS consumer that needs the canonical list.

### 5. Admin console

**Rail entry point** — new `backend/app/templates/icons/_admin.svg` (stroke-based,
`currentColor`, 20px to match siblings). `_rail.html` gains a flex spacer so the
Admin button pins to the **bottom-left**; the rail becomes a top-group /
bottom-group column. `.active` highlight via `rail_active = "admin"`.

**Route** — `backend/app/routes/pages/admin.py`, registered in
`routes/pages/__init__.py`:

- `GET /admin` → renders the console shell. Tabs are **data-driven**: one tab per
  `enum_definitions` row where `editable = 1` (today: just "Gemini generation
  models"). Default tab = the first editable enum.
- `GET /admin/enums/{key}` → the values-table partial for one enum (HTMX target).
- `POST /admin/enums/{key}/values` → add a value.
- `POST /admin/enums/{key}/values/{value}/enabled` → toggle enabled.
- `DELETE /admin/enums/{key}/values/{value}` → remove a value.

All mutating routes return the **updated values-table partial** on
`HX-Request: true` and push a success toast via `Alpine.store('toast')`. No
`location.reload()`; no full-page redirect on CRUD.

**Templates** — `pages/admin.html` (console shell extending `layout.html`) +
`pages/_admin_enum_table.html` (the per-enum values table partial). Built only
from the shared UI library (`design-language.md`): `ui.page_header`,
`ui.breadcrumb`, the tab pattern reused from the Cache page, `ui.button`,
`ui.field` for the add form, `ui.modal` if the add form is a dialog. No new
`*-btn` / `modal-*` / `*-menu` vocabulary (guarded by
`tests/unit/test_design_language_guard.py`).

**Per-row affordances:** value, label, enabled toggle, delete. A subtle
"no rate card" badge shows when a model id is absent from `pricing.py`'s rate map
(so the user knows cost tracking will be incomplete for it).

### 6. CLAUDE.md — new "Enumerations" section

Documents the decision rule and the mechanics:

- **Code-coupled enum** (every value has matching handling logic — statuses,
  decisions, filters, levels): stays a `Literal` in `models/`. Do **not** put it
  in the registry.
- **Open editable list** (model catalogs and similar — values aren't branched on
  in code): goes in the `enum_definitions` / `enum_values` registry, read through
  `EnumService`, edited in the Admin console. Never hardcode such a list in a
  template again.
- How to add a new editable enum: add a `defaults.py` constant + a migration
  seeding one `enum_definitions` row (`editable = 1`) and its values; read it via
  `EnumService`; it appears in the console automatically (data-driven tabs).
- Reminder that `EnumService` lives on `CoreCtx` and is offline-safe.

### 7. Linked follow-up issue

Open a GitHub issue (linked to #13) listing code-coupled enums that are
*duplicated* and worth centralising into a shared module **without** making them
editable: `JobStatus` / `ItemStatus` + the `_BATCH_STATUS_VIEW` display map,
`CacheFilter` / `AnnoFilter`, toast levels (JS), prompt version state. Note it is
a refactor-only, behaviour-preserving change.

## Data flow

1. **Boot / migration** — `enum_definitions` + `enum_values` seeded once with the
   generation-model catalog.
2. **Prompt-new render** — route reads `EnumService.generation_models()` → passes
   to template → dropdown renders from the DB-backed, enabled, sorted list.
3. **Admin edit** — user opens `/admin`, the console lists editable enums from
   `definitions(editable_only=True)`; add/toggle/remove → HTMX → repo write →
   updated partial + toast. Next prompt-new render reflects it.
4. **Default fallback** — `prompts.py` resolves the default model from the first
   enabled catalog entry.

## Error handling

- **Write to a non-editable enum** → `EnumService` refuses → `humanise`d error →
  toast. (Defence in depth; the console only exposes editable enums.)
- **Remove/disable the last enabled value** → refused with a clear toast; the
  consuming dropdown can never be emptied.
- **Duplicate add** → PK conflict surfaced as "That value is already in the list."
- **Offline** (CatDV/GCS down) → console and edits still work; `EnumService` is
  DB-only on `CoreCtx`.
- No `BaseException` catches; provider-absence narrowing is not relevant here
  (no external calls).

## Testing (TDD)

Write the failing test first for each unit.

- **Repo** (`tests/unit/test_enum_values_repo.py`): add/list/remove, enabled
  filter, sort order, PK-conflict raises, `count_enabled`,
  `definitions(editable_only=True)`.
- **Service** (`tests/unit/test_enum_service.py`): `generation_models` returns
  enabled+sorted; write refused when `editable = 0`; last-enabled guard on both
  remove and disable; duplicate-add error; seed values present after migration.
- **Routes** (`tests/integration/test_admin_enums.py`): `/admin` renders the
  Models tab; `HX-Request` add/toggle/delete return the **partial**, not the full
  page; remove-last-enabled returns an error (no mutation); JSON
  `GET /api/enums/{key}` shape.
- **Consumption** (`tests/integration/test_prompt_new_models.py`): the New-prompt
  dropdown reflects the catalog, including a value added at runtime and excluding
  a disabled one; default-model fallback comes from the catalog.
- **Offline** (`tests/unit/test_enum_service_offline.py`): service works with a
  `CoreCtx` built without live providers.
- **Guards:** design-language guard and the shared-Jinja-env / single-lifecycle
  guards continue to pass for the new templates.

## Manual acceptance flows

1. **Open the console.** From any page, click the **Admin** icon at the bottom of
   the left rail. *Expected:* `/admin` loads with a "Gemini generation models"
   tab listing the 8 seeded models.
2. **Add a model.** In the Models tab, add `gemini-4.0-pro` via the add form.
   *Expected:* row appears, success toast, no page reload. Open **Prompts → New** —
   the model dropdown now includes `gemini-4.0-pro`.
3. **Disable a model.** Back in the console, toggle `gemini-3-flash-preview` to
   disabled. *Expected:* open **Prompts → New** — it is no longer offered in the
   dropdown.
4. **Guard the last model.** Disable/delete models until one enabled remains, then
   try to remove/disable it. *Expected:* the action is blocked with a clear toast;
   the value remains.
5. **Rate-card hint.** A model id not present in `pricing.py` shows a subtle
   "no rate card" badge in the console. *Expected:* badge visible; the model still
   selectable in New-prompt.
6. **Offline.** With CatDV and GCS unreachable, reload `/admin`. *Expected:* the
   console still loads and an add/remove still persists (DB-only path).
7. **Default model.** With the catalog edited, create a new prompt without picking
   a model. *Expected:* the default is the first enabled catalog entry, not a
   stale hardcoded id.
