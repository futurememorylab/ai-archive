# 0042. Narrow provider errors — never treat exceptions as "not found"

**Date:** 2026-05-30
**Status:** Accepted
**Lifespan:** Invariant

## Context

Two code paths in the codebase historically did `except Exception:` and
treated any caught exception as documented evidence that the upstream
clip was absent:

- `CacheInspector.list_orphans(deep=True)` —
  `backend/app/services/cache_inspector.py:322` (pre-tier-1).
- `WorkspaceManager.prepare` —
  `backend/app/services/workspace_manager.py:132, :160` (pre-tier-1).

`sync_engine._tick`
(`backend/app/services/sync_engine.py:202`) made an adjacent mistake:
it caught arbitrary `Exception` and called `mark_failed`, treating
unknown errors as permanent.

These patterns turn transient failures (VPN flap, transport blip,
seat-cap reached, adapter bug) into permanent side effects (orphan
marking, terminal "error" workspace state, dropped pending writes).
The next action the user takes on those records — "Evict orphans",
re-running prepare, expecting a write to land — silently destroys
recoverable state.

## Alternatives

1. **Status quo: `except Exception:` everywhere.** Cheap; relies on
   the user understanding that "orphan" might mean "transient" — they
   won't.
2. **Per-call retry loops.** Hide transience by retrying inline.
   Doesn't compose with backoff already implemented elsewhere; makes
   each call site responsible for its own retry policy.
3. **Narrow the exception type at the boundary** (chosen). Introduce
   `NotFoundError(ProviderError)`. Adapters raise it for documented
   absence; a helper `is_provider_not_found(exc)` recognises it (plus
   `httpx.HTTPStatusError(404)` for direct httpx-using paths). Every
   "evidence of absence" decision routes through the helper. Unknown
   exceptions remain unknown — they go to retryable, not terminal.

## Decision

- New exception `backend.app.archive.errors.NotFoundError(ProviderError)`.
- New helper `backend.app.archive.errors.is_provider_not_found(exc) -> bool`.
  Returns True iff `exc` is a `NotFoundError` or an
  `httpx.HTTPStatusError` with status 404; False otherwise.
- CatDV adapter translates upstream `NOT_FOUND` envelopes to
  `NotFoundError` at the boundary. Applied to all 6 `except CatdvError`
  blocks in `adapter.py` for consistency; the `health()` block remains
  a return-`ok=False` rather than a raise because its contract is to
  return a `ProviderHealth` for connection-monitor consumption.
- `CacheInspector.list_orphans(deep=True)` uses the helper. Non-
  NotFound exceptions are silently skipped (the next deep call will
  retry). Catches `Exception` not `BaseException` so
  `asyncio.CancelledError` propagates.
- `WorkspaceManager.prepare` uses the helper. Transient failures land
  the clip in a new `cache_state='transient_error'` (retryable);
  documented absence lands the clip in the existing `'error'`
  (terminal).
- `SyncEngine._tick`'s `except Exception:` defaults to
  `mark_retryable` and bumps `attempts`; only at
  `settings.sync_max_attempts` does it flip to `mark_failed` with a
  `; max_attempts=N reached` suffix. The terminal transition is a
  single atomic SQL statement (`mark_failed(bump_attempts=True)`) —
  the previous two-call sequence had a crash window where the row
  could stay `pending` past the cap.

## Consequences

- **Positive:** transient errors no longer destroy recoverable state.
  The type system carries the "evidence of absence" semantics, not
  ad-hoc try/except in every call site.
- **Negative:** adapters must remember to raise `NotFoundError` at
  their NOT_FOUND boundary. A grep test could be added if drift
  appears (out of scope for tier 1).
- **Forward-looking:** the same pattern applies to any future
  adapter / external system the codebase grows. Document and reuse.
