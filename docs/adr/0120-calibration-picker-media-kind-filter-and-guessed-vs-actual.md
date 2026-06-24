# 0120. Auto-filter the calibration clip picker to the prompt's media-kind; show guessed-vs-actual per resolution

**Date:** 2026-06-23
**Status:** Accepted

## Context

Three improvements to the Admin → Prompts calibration tab:

1. **Picker should only show clips of the prompt's media kind.** A `Prompt`
   carries `media_kind ∈ {video, image, any}`. When calibrating a video prompt,
   showing image clips (and vice-versa) is noise — and HIGH-resolution jobs are
   silently skipped for the wrong kind (ADR 0119), so an off-kind selection
   produces a confusingly partial sweep. The picker is the SHARED clip picker
   (`window.clipPickerCore()` → `/batches/picker` → `query_clip_page`) reused by
   the Batches and Studio pickers, so any filter must be opt-in and leave those
   callers byte-for-byte unchanged.

2. **The estimator's accuracy was invisible per resolution.** `run_telemetry`
   stores both `est_cost_usd_p50` (the pre-run guess) and `cost_usd` (actual).
   The results panel showed only actual cost, so an admin couldn't see whether
   the estimator was over- or under-shooting at each resolution.

3. **The confidence labels (rough/fair/good) had no legend.**

The hard part is (1): a clip's kind is **path-derived** at render time
(`is_image_path(media.filePath)`), with **no stored kind column** — so it cannot
be pushed into the archive provider's server-side `list_clips(offset, limit)`
pagination.

## Alternatives

- **Push `kind` into the provider query / a SQL WHERE.** Rejected: there is no
  kind column; CatDV's own still/duration flags are unreliable (see
  `media_kind.py`), so extension is the only source of truth, and it only exists
  on the hydrated `CanonicalClip`.
- **Post-filter a single fetched page.** Rejected: breaks pagination and the
  `total` count — filtering after the server slice drops rows from the page and
  makes `total` wrong.
- **Fork the picker for calibration.** Rejected outright — the shared picker is
  guarded by `test_clip_picker_single_definition`; duplication is the thing the
  CLAUDE.md frontend rules forbid.

## Decision

1. **Filter at the row level, fetch-all-then-slice, only when `kind` is set.**
   `query_clip_page` gains `kind: str | None = None` (default None → no-op for
   every existing caller). When a kind is active it fetches the whole result set
   (`list_clips(offset=0, limit=_KIND_FILTER_FETCH_LIMIT)`, or the filtered path
   asked for everything), filters by the path-derived kind in Python, then
   slices `[offset:offset+limit]` and sets `total = len(filtered)`. So both the
   page and the total reflect the filtered set, mirroring the existing
   `_filtered_page` "fetch candidates → filter → slice → count" shape. The
   bound (5000) keeps the full fetch safe for the AI catalog the picker lists.

2. **`kind` threads through one opt-in param the whole way.**
   `/batches/picker?kind=…` → `query_clip_page(kind=…)`. Client side,
   `clipPickerCore()` gets `kind: null` state, added to the query string only
   when truthy (`if (this.kind) params.set('kind', this.kind)`), so the Batches
   and Studio pickers — which never set it — send the exact same request as
   before. `adminPromptsTab().openCalibrate(versionId, label, mediaKind)` sets
   `this.kind = (mediaKind && mediaKind !== 'any') ? mediaKind : null` BEFORE the
   first `fetchPage()`, so the initial page is already filtered.

3. **`stats_by_resolution` also sums the estimate.** It now returns
   `{res: {count, cost_usd, est_cost_usd}}` (`COALESCE(SUM(est_cost_usd_p50),0)`).
   `_prompts_view` carries `est_cost_usd` per resolution; the panel renders
   `est $X → actual $Y · <confidence>`. The `pricing_missing` → "no rate card"
   path (ADR 0119) is unchanged.

4. **A one-line confidence legend** ("rough <3 · fair 3–9 · good 10+") sits once
   above the table, using the sanctioned `.meta` class.

## Consequences

- **Positive:** the calibration picker shows exactly the clips the sweep will
  use; the estimator's per-resolution bias is visible; the shared picker stays a
  single definition (Batches/Studio unaffected — proven by
  `test_routes_batches.py` no-kind tests + `test_clip_picker_single_definition`).
- **Negative / accepted:** the kind filter fetches the full result set rather
  than one page (kind is path-derived, not server-filterable). Bounded at 5000
  and confined to the picker's catalog, this is acceptable; if a future catalog
  outgrows it, the right fix is a stored/indexed kind column, not paging the
  filter.
