# 0010. Prompt management: replace templates with versioned prompts

- **Date:** 2026-05-21
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

Annotation prompts lived in a single mutable `templates` row
(`name`, `description`, `prompt`, `output_schema`, `target_map`, `model`,
`archived`). No versions, no way to freeze a known-good prompt while
iterating, no first-class management UI. The Claude Design mockup
(`screens.jsx` TemplatesScreen) proposed a list/detail layout with per-version
state pills, model picker, and kebab actions. Six design calls had to be
made: (a) whether to keep `templates` as a compatibility view or replace it
outright; (b) whether the title+description belong to the prompt identity or
to each version; (c) what to do with `output_schema` (the design omits it,
but the annotator needs it); (d) edit semantics for production versions; (e)
whether kebab "Archive" archives the whole prompt or just the current
version; (f) REST verb style for state mutations.

## Alternatives

(a) Add `prompts` + `prompt_versions` alongside `templates`,
keep the old table as a denormalized "current production" mirror so the
annotator keeps working unchanged; or add `parent_prompt_id` + `version_num`
+ `state` columns to `templates` (smallest schema delta). (b) Title +
description per-version, snapshotted alongside body (more flexibility, but
the list rail has to pick a version's title and "this prompt" becomes
ambiguous). (c) Hide `output_schema` from the UI but keep it in storage; or
remove it entirely and derive what the annotator needs from `target_map`.
(d) Allow in-place edits on production with no auto-archive (closest to
current behavior); or allow edits only on drafts but require manual archive
of the previous prod when promoting. (e) Archive just the current version
(kebab on prompt, but version-level effect); or two distinct menu items.
(f) `POST /actions/promote` REST-pure form; or PUT with a state field in the
body.

## Decision

(a) Replace `templates` outright. Migration 0009 creates
`prompts` + `prompt_versions`, backfills each existing row as v1@production,
rebuilds `annotations` and `jobs` to use `prompt_version_id`, then drops
`templates`. Annotator/jobs/review/seed all rewired in the same change.
(b) Title + description belong to the prompt (per-prompt UNIQUE name);
versions carry only body + target_map + output_schema + model + state.
(c) `output_schema` stays as a third editable JSON panel in the detail view,
versioned alongside body and target_map. (d) Production is immutable —
editing forces creation of a new draft; promoting a draft atomically
demotes the previous production to `archived` in a single transaction. A
partial unique index `idx_one_prod_per_prompt` enforces ≤1 production per
prompt at the database level even if the repo were bypassed. (e) Kebab
"Archive" archives the whole prompt (soft delete, surfaced in
`/prompts/archived`); version-level `archived` is a consequence of promote,
not a user action. (f) Verb-style sub-paths (`:archive`, `:promote`,
`:duplicate`, `:restore`) for state mutations to keep them visually distinct
from RESTful CRUD.

## Consequences

(a) Keeping `templates` as a compat mirror would leave a dead table
in the schema forever and require sync code on every promote — the mental
model of two tables doing the same job is worse than the one-time cost of
rewriting the annotator. The user explicitly asked for the clean rewrite.
(c) The annotator needs the JSON schema to constrain Gemini's response;
hiding it would mean new prompts can't be created end-to-end from the UI.
Deriving it from `target_map` lacks enough information. Three editor panels
in the same x-data scope is straightforward. (d) Mutation invariants
guarantee an annotation's `prompt_used` text cannot silently change after
the fact — critical for reproducibility. The partial unique index is
belt-and-suspenders defense against a hypothetical race or future refactor
that bypasses the repo's transaction. (e) The "Archived prompts" view is a
spec requirement; per-prompt archive is what the user asked for. Two
separate "Archive version" + "Archive prompt" entries would add UI noise
nobody asked for. (f) The other half of the codebase already uses
verb-style sub-paths for state mutations (see `routes/cache.py`); matching
that pattern keeps the API consistent.

**Implementation deviations from the spec (kept consistent across the
codebase):** (i) The page-action endpoints (`/prompts/{id}/_new_version`,
`_promote`, `_duplicate`, `_archive`, `_restore`) are plain POST → 303
redirect, not HTMX partials. The spec described HTMX hot-swaps but the
implementer found that 303-redirect-on-mutation matches the project's
established pattern and avoids hooking up partial-render endpoints for
five different result shapes. (ii) Save in the editor calls `/api/...`
directly via `fetch()` then `window.location.reload()` instead of an
HTMX-partial swap of the detail pane. Loses no information, simpler, but
discards client scroll position on save. (iii) `_target_map_to_json` uses
`model_dump_json(exclude_unset=True)` so a `TargetEntry` with only the
required fields (`kind`, plus `identifier`/`target` when applicable)
round-trips without spurious nulls — the test for export-shape depends on
this.

**Bugs caught in post-implementation review:** `VersionEdit.target_map` and
`PromptCreate.target_map` were originally typed `dict`, which let invalid
shapes persist and then break the detail view on the next GET (the model
constructor would raise on read). Both now use `TargetMap` so validation
happens at the route boundary. `promote_version` originally would silently
revive an archived version into production — now rejects with
`VersionImmutableError` unless target is `draft`, and is idempotent on
already-production. `create_version` originally accepted a
`from_version_id` belonging to a different prompt — now rejects with
`LookupError`. Page actions originally returned 500 on unknown IDs — now
return 404. The migration originally did not preserve `provider_id` /
`provider_clip_id` (added by 0003) through the `annotations` rebuild —
now does, with a test that explicitly asserts it.
