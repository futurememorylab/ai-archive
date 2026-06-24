# 0004. PR 4 — enqueue is atomic with mark_applied; conflict locus is the adapter

- **Date:** 2026-05-19
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

PR 4 introduces the `pending_operations` journal and turns the
"Apply accepted" route into an enqueue. Two design calls had to be made:
(a) what to do about a user double-clicking Apply (the second click must
not enqueue duplicates of ops that the first one already wrote), and (b)
where to detect conflicts — in the SyncEngine, in the WriteQueue at enqueue
time, or inside the provider adapter.

## Alternatives

(a) Filter inside `review_items_repo.list_by_clip` to
exclude rows with `applied_at IS NOT NULL`; or rely on a UNIQUE constraint
on `pending_operations` keyed by review-item-id. (b) Detect conflicts in
the engine by comparing the queued `expected_etag` against a refreshed
`clip_cache` row before calling `apply_changes`.

## Decision

(a) `ReviewItem` gains an `applied_at` attribute, repos expose
it, and `WriteQueue.enqueue_apply` filters `it.applied_at is None`
*inside its own transaction*, then writes the `pending_operations` rows
and `mark_applied` in one `commit()`. A double-click can't race because
both code paths see the same DB state. (b) Conflict detection lives only
inside the adapter (`CatdvArchiveAdapter.apply_changes`): it captures
`modifyDate` as the pseudo-etag and short-circuits with
`WriteResult(status="conflict", conflict_detail=…)` on drift. The engine
treats `WriteResult.status` opaquely.

## Consequences

(a) Putting the dedup inside the queue keeps the route ignorant
of the journal and avoids a schema-level uniqueness rule that would force
us to commit to a "one op per review-item" mapping forever (markers
already collapse N items into one op). (b) The adapter is the only thing
that knows how to compute a pseudo-etag for its backend — pushing that
knowledge into the engine would couple the engine to the CatDV-specific
`modifyDate` quirk. Engines downstream of the FS adapter (PR 7) will use
sha256-based etags through the same code path with no engine change.
