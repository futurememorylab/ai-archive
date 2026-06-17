# 0097. Write-back accumulates multiple appends to the same note/field within one ChangeSet

**Date:** 2026-06-17
**Status:** Accepted

## Context

`build_put_payload` translates a clip's queued `ChangeOp`s into one CatDV PUT
body. For each `AppendNote` it read the *live* clip text via
`_existing_text(current, target)` and wrote the joined result into a dict keyed
by target (`payload["notes"]` / `payload["bigNotes"]`, or `field_changes[target]`
for a note-mode user field).

The `SyncEngine` merges **every** eligible pending op for a clip into a **single**
`ChangeSet` (`_tick` groups by `(provider_id, clip_id)`), so two `AppendNote` ops
with the same target arrive in one `build_put_payload` call. This happens
normally — two annotation runs on the same clip, or one prompt whose `target_map`
routes two outputs to `notes` — each becomes its own `AppendNote`
(`write_queue._build_ops` emits one per accepted note review item).

Because each op recomputed from the **unchanged** `current` snapshot and wrote the
**same** dict key, the second append overwrote the first: only the last note
survived. Accepted user content was dropped silently, with no error — the exact
failure mode "dead-reliable write-back" must not have. This was not a rare
crash-window race; it happened on every drain that batched 2+ appends to one
target.

## Alternatives

- **De-dupe to one AppendNote per target upstream (in `write_queue`).** Rejected:
  it loses the independent `applied_at`/dedup identity of each review item and
  pushes join semantics into the wrong layer; the payload builder is where the
  CatDV body shape is owned.
- **Refetch `current` between ops.** Rejected: there is one snapshot per
  `ChangeSet` by design (one GET, one PUT); refetching per op would multiply
  seat-limited CatDV calls and still race.

## Decision

`build_put_payload` keeps a per-target **running text** (`note_text`), seeded
lazily from the live clip on first touch and updated by each emit. Subsequent
appends to the same target chain off the running value instead of re-reading the
stale snapshot, so N appends produce `existing ⧉ a ⧉ b ⧉ …` in enqueue order.
The append idempotency guard (ADR 0091) now reads the running value; for a single
op that equals `_existing_text`, so single-op behaviour — including the
idempotent re-drain skip — is unchanged.

## Consequences

- Multiple appends to the same note or note-mode field in one drain now chain
  instead of clobbering; no accepted content is lost.
- Single-op idempotent re-drain (crash recovery / lost PUT response) still skips.
- **Residual limitation:** the idempotency check still inspects only the last
  separated segment, so a crash-replay of a *multi-append* batch after a PUT the
  server applied but whose response was lost (live note already ends with the
  full chained result) can re-append the earlier segments. That window is far
  narrower than the every-drain clobber fixed here and is left for a later change.
- Covered by `test_two_appends_*` in `tests/unit/test_catdv_payload.py`.
