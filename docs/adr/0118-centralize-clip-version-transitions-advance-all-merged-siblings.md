# 0118. Centralize clip-version transitions; advance every merged-publish sibling

**Date:** 2026-06-23
**Status:** Accepted

## Context

`SyncEngine` drains `pending_operations` into CatDV ChangeSets and, on each
outcome, transitions the originating `clip_version` (live / conflict / failed).
Two defects surfaced in a strict review of the clip-version-history work:

1. **Merged siblings strand on `publishing`.** When several publishes for one
   clip are enqueued (offline) they merge into a single PUT (ADR 0091). The
   drained rows then carry more than one `origin_clip_version_id`, but
   `_version_id_from_rows` returned only `max(ids)` and the engine transitioned
   that one version. `ClipVersionsRepo.mark_live` happens to supersede the older
   `publishing` siblings (anomaly A4 fan-out), so the **ok** path was fine — but
   `mark_conflict` / `mark_failed` are single-row `WHERE id = ?` with no fan-out,
   so on a conflicted or failed merged PUT the older sibling versions were left
   on `publishing` **forever** (History shows a stuck "Publishing…").

2. **Scattered transition logic.** The guard
   `if self._clip_versions is not None and version_id is not None` plus the
   transition call were hand-duplicated across five branches (`_retry_or_fail`
   ceiling, the two raised-error `except` arms, and the ok/conflict/fatal arms of
   `_handle_result`), with a separate `_mark_version_failed` helper for the
   raised paths. A new result/error branch can silently forget to advance its
   version — which already happened once (the raised-fatal path; publishing
   audit anomaly A9, the reason `_mark_version_failed` was added).

The two are one root cause: there was no single place that owns "this drain
advanced these versions".

## Alternatives

- **Make `mark_conflict` / `mark_failed` fan out like `mark_live`.** Rejected:
  pushes merge-awareness into the leaf repo and is the wrong semantics —
  `mark_live` supersedes siblings because a *newer live version replaced them*;
  on conflict/failed nothing landed, so "superseded" would be a lie. Each merged
  version genuinely conflicted/failed and should say so.
- **Mark every merged version `live` by iterating all ids.** Rejected for the ok
  path: redundant with `mark_live`'s existing A4 fan-out and order-dependent
  (marking an older id live last would leave the wrong version live).
- **Leave `max(ids)` and just add fan-out at each call site.** Rejected: keeps
  the five-way duplication that caused A9.

## Decision

Replace `_version_id_from_rows` (single max) and `_mark_version_failed` with one
chokepoint, `_advance_versions(rows, *, state, reason="")`, that every result and
error branch calls:

- `state="live"` → `mark_live(max(ids))` only; the repo's A4 fan-out supersedes
  the older merged siblings.
- `state="conflict"` / `state="failed"` → iterate **all** distinct version ids
  (oldest-enqueued first, via `dict.fromkeys`) and mark each, so no merged
  sibling is left on `publishing`.

The `None`-guard lives once, inside the helper. Net effect: five duplicated
guard blocks and two helpers collapse to one helper and five one-line calls
(−32 lines in `sync_engine.py`).

## Consequences

- Merged-publish drains that conflict or fail now transition every version; the
  "stuck Publishing…" class of bug is closed for those paths.
- A future result/error branch advances its version by calling one helper; it
  can't reintroduce A9 by forgetting a scattered guard.
- A conflicted/failed merged publish can now leave **multiple** `conflict` /
  `failed` rows for one clip. This is intended and honest — each was a distinct
  publish attempt. `live_version_num_by_clip` only reads `live`, so the live
  label is unaffected; retry re-drains them together and `mark_live` collapses to
  one live row on success.
- The `live` path is unchanged behaviourally (still `mark_live(max)`), so the
  existing supersede semantics and tests hold.
