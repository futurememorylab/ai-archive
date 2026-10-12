# Centralised enumeration + Admin console — design spec

**Issue:** [#13 Centralised enumeration](https://github.com/futurememorylab/ai-archive/issues/13)
**Date:** 2026-06-14
**Status:** Draft

## Problem

Value lists that mean one thing are duplicated across the codebase, in Python
**and** in the frontend. The worst offender is the **Gemini generation-model
list**, which lives independently in four places that drift out of sync:

- `backend/app/settings.py` — `gemini_model` default (`"gemini-2.5-flash-lite"`).
- `backend/app/services/pricing.py` — per-model rate cards (only covers the
  `gemini-2.5-*` family; the `gemini-3.*` models in the dropdown have no rates).
- `backend/app/templates/pages/_prompt_new.html` — a hardcoded Jinja list of 8
  model IDs rendered into the New-prompt `<select>`.
- `backend/app/static/promptEditor.js:34` — the same 8 IDs again as an Alpine
  `MODELS` array, powering the Edit-prompt model picker.

Fixed enums leak the same way: toast levels, job/item statuses and cache/anno
filters are restated in JS and templates with no shared source. The issue asks
for:

1. A central source of truth for enumerations used in multiple places.
2. A service that serves enumerations to **both** backend and frontend.
3. Runtime configuration — a user can add a newly-available model or remove an
   unsupported one **without a code change**.
4. CLAUDE.md guidance on how to use and handle enums.

## Core model: one code registry, DB holds only edits

Two kinds of enumeration, one delivery channel:

- **Fixed enums** (every value has matching handling logic — toast levels,
  statuses, decisions, filters). **Code is the source of truth.** They stay typed
  as `Literal`s in `models/` for static checking, and are *also declared in the
  registry* so the frontend reads one canonical list instead of hardcoding. Not
  user-editable.
- **Editable lists** (the model catalog). Code declares the **seed** + metadata;
  the **DB stores the user's edits** (added values, enable/disable, default,
  soft-deleted seeds). Runtime value = `reconcile(code seed, DB edits)`.

```
        ┌────────────────────────────────────────────────────────────┐
        │  backend/app/enums/registry.py   ← CANONICAL for ALL enums  │
        │  EnumSpec(key, name, description, editable, values[])        │
        │   • gemini_generation_model  editable=True   (seed values)  │
        │   • toast_level              editable=False  (fixed values) │
        │   • … fixed enums migrated opportunistically (see follow-up)│
        └───────────────┬─────────────────────────────┬──────────────┘
        fixed: served direct from code                │ editable: code = seed only
                        │                              ▼
                        │                  ┌────────────────────────┐
                        │                  │  enum_values  (DB)      │  edits only:
                        │                  │  adds, enabled,         │  user overrides
                        │                  │  is_default, removed    │  + reconcile state
                        │                  └───────────┬────────────┘
                        ▼                              ▼
                ┌──────────────────────────────────────────────────┐
                │  EnumService  (on CoreCtx — DB-only, offline-safe) │
                │  values(key) · definitions() · add/remove/default  │
                │  reconcile_seeds() at boot · total fallback chain  │
                └───────────────┬──────────────────────┬─────────────┘
        backend reads           │                      │  frontend reads
   (prompt dropdown, default,   │                      │  window.APP_ENUMS (layout)
    pricing badge)              ▼                      ▼  + GET /api/enums/{key}
                        Admin console /admin (data-driven from registry)
```

> **Decision note (supersedes earlier draft):** an earlier version put enum
> *definitions* in a DB table. Because fixed enums are code-sourced (issue goal
> #2, "serve the frontend"), definitions are inherently code-declared; a DB
> definitions table would duplicate the registry and risk drift. Definitions now
> live in code; the DB holds only **editable-value edits**. One DB table, not two.

## Scope

### In scope

- A code **enum registry** (`backend/app/enums/`) — canonical declaration of every
  centralised enum, fixed or editable.
- One DB table (`enum_values`) holding **edits to editable enums** + reconcile
  state (soft-delete tombstones).
- `EnumService` — single read/write API on `CoreCtx` (offline-safe); boot-time
  **reconcile** of code seeds into the DB.
- The **generation-model catalog** migrated to the registry and made editable;
  the three drift sites rewired to read from the service.
- **Frontend delivery of fixed enums**: the mechanism (registry → `window.APP_ENUMS`
  + `/api/enums/{key}`) plus **one exemplar migrated now** — `toast_level`,
  consumed by `toast.js` — to prove the path end to end.
- A new **Admin console** (bottom-pinned rail icon), data-driven from the registry,
  with one editable enum today (the model catalog) and room to grow.
- A new CLAUDE.md "Enumerations" section.
- A linked follow-up issue migrating the remaining fixed enums into the registry.

### Out of scope (and why)

- **Migrating *all* fixed enums now.** The mechanism + `toast_level` exemplar ship
  here; statuses/filters/decisions follow in the linked issue (behaviour-
  preserving, no logic change). Boiling the ocean in one PR is the regression
  risk we are avoiding.
- **Live-session model + voice.** Single settings today, not a dropdown. The
  registry can absorb them later.
- **Editing pricing rate cards.** Rates stay in `pricing.py`; the console only
  *surfaces* a "no rate card" badge. (Catalog→pricing coupling is the natural
  next step — noted, not built.)

## Components

### 1. Enum registry — `backend/app/enums/registry.py`

Frozen dataclasses declaring every enum. Canonical for fixed enums; seed +
metadata for editable ones.

```python
@dataclass(frozen=True)
class EnumValueSpec:
    value: str
    label: str | None = None
    default: bool = False           # editable enums: the seeded default pick
    metadata: dict | None = None    # forward-compat: region, rates, caps, …

@dataclass(frozen=True)
class EnumSpec:
    key: str
    name: str                       # 'Gemini generation models' (console/API title)
    description: str
    editable: bool
    values: tuple[EnumValueSpec, ...]

ENUM_REGISTRY: dict[str, EnumSpec] = { … }
```

Seeds: `gemini_generation_model` (editable, the 8 current model IDs, default
`gemini-2.5-flash-lite`) and `toast_level` (fixed: `info`/`success`/`error`).

**Consistency guard:** a unit test asserts each *fixed* enum's registry values
equal `typing.get_args(<the Literal>)`, so the code SoT and the type can't drift.

### 2. Schema — one DB table (editable enums only)

New migration `backend/migrations/00NN_enum_values.sql` (number resolved against
the latest migration at implementation time):

```sql
CREATE TABLE enum_values (
  enum_key   TEXT NOT NULL,                -- must be an editable key in the registry
  value      TEXT NOT NULL,                -- 'gemini-2.5-flash'
  label      TEXT,
  enabled    INTEGER NOT NULL DEFAULT 1,
  is_default INTEGER NOT NULL DEFAULT 0,
  sort_order INTEGER NOT NULL DEFAULT 0,
  source     TEXT NOT NULL DEFAULT 'user', -- 'seed' (from registry) | 'user'
  removed    INTEGER NOT NULL DEFAULT 0,   -- soft-delete = reconcile tombstone
  metadata   TEXT,                         -- JSON; forward-compat (rates/region/…)
  created_at TEXT NOT NULL,
  PRIMARY KEY (enum_key, value)
);
-- at most one default among live rows
CREATE UNIQUE INDEX idx_enum_values_default
  ON enum_values(enum_key) WHERE is_default = 1 AND removed = 0;
CREATE INDEX idx_enum_values_key ON enum_values(enum_key, sort_order);
```

No `enum_definitions` table — definitions are in the registry. No FK target table,
so no FK row (the app runs `PRAGMA foreign_keys=ON`, `db.py:17`; nothing
references `enum_values`). Seeds are **not** baked into the migration SQL; they are
applied by reconcile (below) so the code registry stays the single seed source.

### 3. `EnumValuesRepo` — `backend/app/repositories/enum_values.py`

Existing repo pattern (async, `aiosqlite.Connection` + `commit` flag, row→model).
Leaf layer — no service imports (import-linter contract). Operates only on
editable keys.

- `live_values(enum_key) -> list[EnumValueRow]` — `removed = 0`, ordered.
- `all_rows(enum_key) -> list[EnumValueRow]` — incl. removed (for reconcile).
- `upsert_seed(enum_key, spec, *, commit)` — insert a seed row only if absent
  (never resurrects a `removed` row; never clobbers user state).
- `add_value(enum_key, value, label, *, commit)` — `source='user'`; PK conflict →
  raises (also covers re-adding a tombstoned value: flip `removed=0` instead).
- `set_enabled` / `set_default` / `soft_delete` / `count_enabled`.

### 4. `EnumService` — `backend/app/services/enum_service.py`

Single source of truth for reads, gatekeeper for writes, owner of reconcile. On
**`CoreCtx`** (needs only `db` + repo + the static registry), so it is reachable
via `Depends(get_core_ctx)` and works fully offline. No god-context / `Optional`
service fields (ADR 0047).

Reads:
- `definitions(editable_only=False) -> list[EnumDefinition]` — from the registry.
- `values(key, enabled_only=False) -> list[EnumValue]`:
  - **fixed** key → registry values (DB never consulted).
  - **editable** key → DB live rows.
  - **Total fallback:** if an editable key has zero live rows (migration skipped,
    everything soft-deleted out of band), fall back to the registry seed so the
    list is **never empty**. Code is the ultimate SoT; the DB is the override.
- `generation_models(enabled_only=True)` / `generation_default() -> str` — default
  resolution chain: flagged default → first enabled → registry seed default.

Writes (editable keys only):
- Gated: writing to a non-editable key (or an unknown key) raises a clear error
  (defence in depth; the console only exposes editable enums).
- **Last-enabled guard:** refuse a remove/disable that drops enabled count to 0.
- **Default integrity:** `set_default` requires the target enabled and auto-clears
  the prior default (one transaction; the partial unique index makes a partial
  failure safe). Disabling/removing the current default is refused with "set
  another value as default first".
- `remove` is a **soft delete** (`removed=1`) so reconcile won't re-add it.
- Errors surface via `services/errors.py::humanise`.

Reconcile:
- `reconcile_seeds()` runs at startup (idempotent). For each editable registry
  enum, `upsert_seed` every spec value — inserting newly-shipped code defaults on
  existing installs, **never** clobbering user edits and **never** resurrecting a
  tombstoned value. This is the boot-time reconcile (issue decision #5).

### 5. Consumption rewiring

- **Prompt "New" dropdown** — `_prompt_new.html` stops hardcoding the list; the
  route passes `generation_models = ctx.enum_service.generation_models()` and the
  `<select>` loops over it, pre-selecting `generation_default()`.
- **Default model fallback** — `routes/pages/prompts.py` (~66/82) uses
  `generation_default()` instead of the hardcoded `"gemini-2.5-flash-lite"`.
  `settings.gemini_model` stays an env override but is no longer the dropdown's
  source.
- **Edit-prompt model picker** — `promptEditor.js` drops its hardcoded `MODELS`
  array; the picker reads the list the prompt-detail route injects into the
  Alpine component (`MODELS: {{ model_options | tojson }}`).
- **Orphaned-reference handling (data correctness).** `PromptVersion.model` is a
  denormalised string (`prompt.py:59`) with no FK, so deleting/disabling a
  catalog model does not alter saved prompts — but the **Edit form must still
  render the prompt's current model even when it is absent/disabled/removed from
  the catalog**, flagged "unavailable", so editing never silently switches the
  model to the first option. Implemented **server-side**: the prompt-detail route
  builds `model_options` as the enabled catalog **unioned with the version's saved
  model** when missing, so the union (and the orphan flag) is computed once in
  Python, not duplicated in JS.
- **Frontend fixed enums** — fixed enums are static (registry, no DB), so
  `layout.html` injects them once as a Jinja-global blob:
  `window.APP_ENUMS = {{ app_enums_json | safe }}` (built at startup from the
  registry). `toast.js` consumes `APP_ENUMS.toast_level` instead of its inline
  comment-list. Editable lists are **not** in this static blob (they change at
  runtime); they reach the frontend via route context (above) and, for any future
  dynamic consumer, `GET /api/enums/{key}`.

### 6. Admin console

**Rail entry point** — new `templates/icons/_admin.svg` (stroke, `currentColor`,
20px). `_rail.html` gains a flex spacer so the Admin button pins **bottom-left**;
rail becomes a top-group / bottom-group column. `.active` via `rail_active="admin"`.

**Route** — `routes/pages/admin.py`, registered in `routes/pages/__init__.py`:

- `GET /admin` → console shell; tabs are **data-driven** from
  `definitions(editable_only=True)` (today: the model catalog). Default tab = first
  editable enum.
- `GET /admin/enums/{key}` → values-table partial (HTMX target).
- `POST /admin/enums/{key}/values` → add (re-adds a tombstoned value by clearing
  `removed`).
- `POST /admin/enums/{key}/values/{value}/enabled` → toggle.
- `POST /admin/enums/{key}/values/{value}/default` → make default.
- `DELETE /admin/enums/{key}/values/{value}` → soft-delete.

All mutating routes return the updated partial on `HX-Request: true` and push a
toast; no `location.reload()`, no full-page redirect on CRUD.

**Templates** — `pages/admin.html` + `pages/_admin_enum_table.html`, built only
from the shared UI library (`design-language.md`): `ui.page_header`,
`ui.breadcrumb`, the Cache-page tab pattern, `ui.button`, `ui.field`, `ui.modal`.
No new `*-btn` / `modal-*` / `*-menu` vocabulary (design-language guard).

**Per-row affordances:** value, label, enabled toggle, a one-per-enum **default**
marker, delete. Badges: "no rate card" when the model id is absent from
`pricing.py`'s rate map; "unavailable" is rendered in the *prompt* form, not here.

**`sort_order`** is kept for deterministic, seeded ordering. Drag-reorder UI is
**out of scope for v1** (no reorder route/method shipped — avoids dead code);
noted as a future affordance.

### 7. CLAUDE.md — new "Enumerations" section

- **Decision rule:** does any code branch on a specific value
  (`if x == 'applied'`)? → **fixed** enum: a `Literal` in `models/` **and** a
  registry entry (`editable=False`). Is the set open-ended, values just data passed
  through? → **editable** enum: registry `editable=True` + DB-backed, edited in the
  console. Never hardcode either kind in a template or JS again.
- How to add each kind; how the frontend consumes (`window.APP_ENUMS` /
  `/api/enums`); that `EnumService` is on `CoreCtx` and offline-safe; that fixed
  enums keep code as SoT (a guard test pins registry to the `Literal`).

### 8. Linked follow-up issue

Open an issue (linked to #13) to migrate the remaining *fixed* enums into the
registry for frontend SSOT — `JobStatus`/`ItemStatus` (+ the `_BATCH_STATUS_VIEW`
display map), `CacheFilter`/`AnnoFilter`, review `decision`, prompt version
state. Behaviour-preserving; each gets a registry entry + the `Literal`-consistency
guard, and its frontend usage switches to `APP_ENUMS`.

## Error handling

- **Write to a non-editable / unknown key** → refused, `humanise`d → toast.
- **Remove/disable the last enabled value** → refused with a clear toast.
- **Disable/remove the current default** → refused ("set another default first").
- **Duplicate add** → PK conflict surfaced as "already in the list" (a tombstoned
  value is revived rather than erroring).
- **Empty DB / skipped migration** → `values()` falls back to registry seeds; the
  app never shows an empty model list.
- **Offline** (CatDV/GCS down) → console and edits work; service is DB-only.
- Catch `Exception`, never `BaseException`.

## Testing (TDD)

- **Registry** (`tests/unit/test_enum_registry.py`): every fixed enum's values
  equal `get_args(<Literal>)` (drift guard); editable enums have exactly one
  seed default.
- **Repo** (`tests/unit/test_enum_values_repo.py`): live vs all rows, soft-delete,
  `upsert_seed` idempotent + no resurrection of tombstones, PK conflict,
  `count_enabled`.
- **Service** (`tests/unit/test_enum_service.py`): fixed key served from registry
  (DB ignored); editable served from DB; **empty-DB fallback to seed**;
  `generation_default` chain; `set_default` one-default invariant + rejects
  disabled target; disable/remove-of-default refused; last-enabled guard; write to
  non-editable refused; reconcile adds a new seed without clobbering edits and
  without reviving a tombstone.
- **Routes** (`tests/integration/test_admin_enums.py`): `/admin` lists editable
  enums; `HX-Request` add/toggle/delete/default return the **partial**;
  remove-last and remove-default refused; `GET /api/enums/{key}` shape for both a
  fixed and an editable key.
- **Consumption** (`tests/integration/test_prompt_new_models.py`): New-prompt
  dropdown reflects runtime adds, excludes disabled, pre-selects the default; **Edit
  form still shows a saved-but-removed model flagged unavailable** (orphan guard).
- **Frontend bootstrap** (`tests/integration/test_enum_bootstrap.py`):
  `window.APP_ENUMS.toast_level` present in `layout.html` output and equals the
  registry.
- **Offline** (`tests/unit/test_enum_service_offline.py`): works on a `CoreCtx`
  built without live providers.
- Existing design-language / shared-Jinja-env / single-lifecycle guards stay green
  for the new templates.

## Manual acceptance flows

1. **Open the console.** Click the **Admin** icon at the bottom of the left rail.
   *Expected:* `/admin` loads with a "Gemini generation models" tab listing the 8
   seeded models, one marked default.
2. **Add a model.** Add `gemini-4.0-pro`. *Expected:* row appears, success toast,
   no reload. **Prompts → New** now offers it.
3. **Disable a model.** Toggle `gemini-3-flash-preview` off. *Expected:*
   **Prompts → New** no longer offers it.
4. **Last-model guard.** Reduce to one enabled, then try to remove/disable it.
   *Expected:* blocked with a clear toast; the value remains.
5. **Set the default.** Mark `gemini-2.5-flash` default. *Expected:* the marker
   moves (exactly one stays); **Prompts → New** pre-selects it. Disabling/deleting
   that row is then blocked until another default is chosen.
6. **Orphan safety.** Create a prompt on model X, then delete X from the catalog.
   Open that prompt's Edit form. *Expected:* X is still shown (flagged
   "unavailable"); saving does not switch the model.
7. **Reconcile survives restart.** Remove a seeded model, restart the app.
   *Expected:* the removed model does **not** come back (tombstone honoured); a
   model newly added to the code registry **does** appear after restart.
8. **Fixed enum on the frontend.** Trigger a toast. *Expected:* it uses a level
   from `window.APP_ENUMS.toast_level`; no hardcoded level list remains in
   `toast.js`.
9. **Offline.** With CatDV and GCS unreachable, reload `/admin` and add/remove a
   model. *Expected:* console loads and edits persist (DB-only path).
