# 0093. `synced_at` server-confirm stamp + topbar annotate-phase breakdown

**Date:** 2026-06-17
**Status:** Accepted
**Lifespan:** Invariant

## Context

Operator feedback on the batch experience:

1. **"Applied" showed before the change was on CatDV.** `review_items.applied_at`
   is stamped at *enqueue* time by the WriteQueue (and is the double-click dedup
   key), so every surface reading it — the draft count/message — said "applied"
   the moment a change was queued, not when the SyncEngine confirmed the PUT
   landed. The true upstream state lived only in `pending_operations`.
2. **The batch upload was opaque.** Single-clip annotate narrates
   "Locating proxy… → Uploading proxy to GCS… → Calling Gemini…", but a *batch*
   showed only "Annotating X/Y" in the topbar — no sign of the slow proxy
   download / GCS upload phase over the VPN.

(A third report — a `run_telemetry` insert crash — was **dev-DB schema drift**,
not a code change: a stale `event_id NOT NULL` column from a pre-release 0016.
Repaired in place; no ADR-worthy decision.)

## Decision

- **`synced_at` confirm stamp (migration 0022).** `review_items.synced_at` is
  stamped by the SyncEngine when a clip's write-back actually lands
  (`_handle_result` ok-branch reads each op's `origin_review_item_ids` and calls
  `ReviewItemsRepo.mark_synced`). `applied_at` keeps its enqueue/dedup meaning;
  `synced_at` is the server-confirmation. The SyncEngine takes the review-items
  repo as an **optional** ctor arg (default None → no stamping), so existing
  constructions/tests are untouched; `context.py` wires it. `draft_review_arrays`
  exposes `synced_count` next to `applied_count`, and the draft message now
  reports what is actually on the server ("N sent — M confirmed on the server").
- **Topbar phase breakdown.** `JobsRepo.phase_counts` groups `job_items` into
  caching (resolving+uploading) / annotating (prompting) / queued / done / error;
  `/api/jobs/active` returns it per job; `jobsIndicator` aggregates across jobs
  and renders "Caching N · Annotating M · K queued" (falling back to the
  done/total count). Because phase transitions emit *per-item* events, not the
  job-level events the indicator's SSE carries, the indicator refreshes
  `/active` every 2s **only while a batch is active** (idle → no fetch); the SSE
  handler preserves the last-known phases across job-level updates.

## Alternatives

- *Drive "applied" off `pending_operations` status instead of a new column* —
  rejected: `review_item → op` is a JSON-array mapping, awkward to query; a
  persisted per-item stamp is cleaner and survives reloads.
- *Move `applied_at` itself to sync-confirm time* — rejected: it's the enqueue
  dedup key; splitting enqueue (applied_at) from confirm (synced_at) keeps both
  meanings intact.
- *Push phases to the indicator via the "connection"/per-item SSE* — deferred;
  a 2s while-active poll of the existing `/active` endpoint is simpler.

## Consequences

- The UI distinguishes "queued/syncing" from "confirmed on CatDV"; the count is
  honest about partial syncs.
- The slow upload phase is visible during a batch, not hidden behind "Annotating".
- **Follow-up (requested):** real upload **percentage** (proxy-download +
  GCS-upload byte progress) is not yet wired — it needs progress callbacks
  through `proxy_resolver`/`catdv_client` download and `ai_store` upload, plus a
  `pct` on the per-item events. Phase-level visibility ships now; byte-level % is
  a separate change.
