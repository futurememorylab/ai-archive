# 0080. Centralised enumeration: code registry + EnumService + editable model catalog

**Date:** 2026-06-14
**Status:** Accepted

## Context

Enumerated value sets were duplicated across the codebase. The Gemini
generation-model list, in particular, was hardcoded in three places — the
New-prompt template's `<select>`, the Alpine editor's `MODELS` array in
`promptEditor.js`, and the create-route's default-model fallback — so adding or
retiring a model meant editing several files in lockstep, and the frontend had no
single source of truth. Toast severity levels were similarly duplicated between
`toast.js` and the backend.

Two distinct needs sit behind "centralise enums":

- Some enums are **fixed** — every value is coupled to handling logic (a code
  branch, a CSS class). These must stay `Literal`s in `models/` for static
  checking; centralisation here is only about giving the frontend one read-only
  copy.
- Some enums are **editable open sets** — the model catalog is just data passed
  through to Gemini, and the user must be able to add a newly released model or
  hide an unsupported one at runtime without a deploy.

The full design lives in
`docs/superpowers/specs/2026-06-14-centralised-enumeration-design.md`; the
implementation plan in
`docs/superpowers/plans/2026-06-14-centralised-enumeration.md`.

## Alternatives

- **Source of truth for editable enums:** a code registry whose seed is
  reconciled into a DB table (chosen) vs. a pure DB-driven `enum_definitions` +
  `enum_values` pair with no code anchor (rejected — loses the static `EnumSpec`
  declaration and makes the seed invisible in the tree) vs. config files
  (rejected — no transactional edits, no admin UI).
- **Storing fixed enums in the DB too:** rejected. Fixed enums are code-coupled;
  serving them from code (the DB is never consulted) keeps them consistent with
  the `Literal` and removes a needless query.
- **Reconcile semantics:** INSERT-OR-IGNORE seed materialisation with
  soft-delete tombstones (chosen — a value the user deleted is never resurrected
  on the next boot, and user edits are never clobbered) vs. truncate-and-reseed
  (rejected — destroys edits) vs. no reconcile / pure-DB (rejected — an empty DB
  would yield an empty list).
- **Frontend delivery:** `window.APP_ENUMS` carries only fixed enums (static,
  DB-free, injected once in `layout.html`); editable lists come via server-render
  route context or `GET /api/enums/{key}` (chosen) vs. shipping editable lists in
  `APP_ENUMS` too (rejected — they change at runtime and the server render can
  union orphaned saved values, which a static blob cannot).
- **Edit-form orphan handling:** union the prompt version's saved model into the
  Edit picker when it is no longer in the catalog (chosen — editing never
  silently switches a prompt's model) vs. dropping it (rejected — data loss on
  save).

## Decision

- `backend/app/enums/registry.py` is the canonical declaration: `EnumSpec`
  (`editable` flag) + `EnumValueSpec` (one `default=True` for editable enums).
- A single DB table `enum_values` (migration `0020`) holds **only** editable
  enums' edits — there is deliberately **no** `enum_definitions` table; the
  registry is the definition. Rows carry `source` (`seed`/`user`), an `enabled`
  flag, a partial unique index enforcing one live default per key, and a
  `removed` tombstone column.
- `EnumService` (on `CoreCtx`, DB-only and offline-safe via a `db_provider`
  lambda like `CacheInspector`) is the single read/write API. It serves fixed
  enums from the registry, editable enums from the DB with a registry-seed
  fallback so a list is never empty, runs `reconcile_seeds()` at boot, and
  enforces the write guards (not-editable, last-enabled, current-default,
  duplicate). `EnumValuesRepo` is the leaf.
- The Admin console (`/admin`, bottom-pinned rail gear) edits editable enums
  through HTMX partials; its tabs are data-driven from
  `definitions(editable_only=True)`, so a new editable `EnumSpec` appears with no
  route change.
- `toast_level` is migrated as the fixed-enum exemplar; the remaining fixed
  enums are deferred to a follow-up (behaviour-preserving migration).

## Consequences

- Adding or retiring a Gemini model is now a runtime Admin action or a one-line
  registry edit, never a multi-file change; the frontend reads models from one
  place.
- The app stays fully navigable offline: `EnumService` is a `CoreCtx` member with
  no live-provider dependency, and reconcile/reads are pure DB.
- A new editable enum needs only an `EnumSpec` — it shares `enum_values`, so no
  new migration is required until a second editable enum wants its own columns.
- Fixed enums still require their `Literal` in `models/`; the registry entry is
  an additional (guard-tested) mirror, not a replacement — a small duplication
  accepted to keep static checking.
- The remaining fixed enums (`JobStatus`/`ItemStatus`, `CacheFilter`/`AnnoFilter`,
  review `decision`, prompt version state) are still hardcoded on the frontend
  until the follow-up issue migrates them.
