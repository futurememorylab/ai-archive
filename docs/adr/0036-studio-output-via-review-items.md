# 0036. Prompt Studio output renders via review_items, not raw output_json

**Date:** 2026-05-28
**Status:** Accepted

## Context

The Prompt Studio Output card was rendering through a bespoke
`services/studio_panels.py` adapter that re-parsed `studio_run.output_json`
at render time. The adapter assumed a flat `{in_secs, out_secs}` scene shape,
but Gemini emits the seeded-schema's nested `{"in": {"secs": …}, "out":
{"secs": …}}` shape — so the Output card showed 0 markers after a successful
run. Field values arrived wrapped as `{"value": …, "evidence_secs": [...]}`
and were rendered as Python dict reprs. Notes (target_map `kind:"note"`)
were lumped into the fields panel.

Meanwhile the clip-detail page handles the identical Gemini output without
any of these problems because it normalizes once at write time via
`target_map.expand()` → `review_items`, then renders from the DB.

## Alternatives

1. **Fix the studio_panels adapter** to unwrap nested secs + value
   envelopes. Smallest diff, but leaves two parallel "Gemini JSON → panels"
   code paths that must stay in sync forever.

2. **Reuse `target_map.expand` in-memory** on every studio render. No
   schema change, but still two read paths (DB-backed vs JSON-derived) and
   no future hook for History/compare-by-items.

3. **(Chosen) Persist review_items for studio runs.** Add a nullable
   `studio_run_id` FK to `review_items` with a CHECK that exactly one of
   `(annotation_id, studio_run_id)` is set. Studio's `_finalize_studio`
   calls `target_map.expand(..., studio_run_id=run_id, ...)` and bulk-
   inserts items. Both clip-detail and studio render through
   `build_draft_view`. One normalizer (`target_map.expand`), one renderer
   (`build_draft_view` + `_anno_panels.html`), one shape (the normalized
   `panels` dict).

## Decision

Persist review_items for studio runs (alternative 3). Studio runs never
write to CatDV, but their normalized review_items live in the DB alongside
annotation-bound items, discriminated by the owner column. The bespoke
`studio_panels.py` adapter is deleted.

## Consequences

- Migration 0014 rebuilds `review_items` to allow nullable `annotation_id`
  and adds `studio_run_id` + CHECK constraint. Existing rows migrate
  unchanged.
- `target_map.expand` takes the owner discriminator as a keyword-only
  argument; exactly one must be supplied (runtime guard).
- `services/studio_panels.py` is gone. Anyone touching the studio Output
  card now edits `services/draft_view.py` + `templates/pages/_anno_panels.html`,
  same as for clip-detail.
- Player overlay also reads from review_items, so the timeline matches
  whatever the Output card shows by construction.
- Future Studio "History" / "compare runs" features get review_items as
  their foundation for free.
