# 0126. Write-transaction lock + `_handle_result` hardening

**Date:** 2026-06-30
**Status:** Accepted
**Lifespan:** Invariant

## Context

The app hands one shared `aiosqlite.Connection` to every worker and every
route (`CoreCtx.db`, consumed via `db_provider`). In aiosqlite's default
`isolation_level` mode the first DML opens a connection-scoped transaction
that stays open until *some* `commit()` lands — and there was no
`asyncio.Lock` serializing writers.

Two failure modes followed:

1. **Atomicity break.** Five `commit=False`→`commit()` write windows
   (`WriteQueue.enqueue_apply`, `EnumService.reconcile_seeds` +
   `_ensure_materialised`, `PricingService.reconcile_seeds`,
   `backfill_clip_versions`) could have their transaction prematurely
   committed by any other writer's `commit()` on the same connection. A
   crash in the yield window left half-finished state committed → duplicate
   writes to the seat-limited CatDV upstream on resume.

2. **`in_flight` stranding.** An exception in `SyncEngine._handle_result`
   (e.g. `SQLITE_BUSY` from Litestream checkpoint contention) bubbled to
   `_loop`'s catchall, leaving rows `in_flight` with no reset until
   boot-time `reset_in_flight_to_pending`.

## Alternatives

- **Hold the lock across the SyncEngine network apply call.** Rejected: it
  would block every DB writer for the duration of each CatDV round-trip
  AND does not fix stranding (SQLITE_BUSY comes from external Litestream
  contention, not in-process interleaving on the single shared connection).
- **Connection-per-writer pool.** Rejected: conflicts with invariant #21
  ("a single write connection on the request critical path"), brings
  file-level SQLITE_BUSY, huge refactor.
- **Wrap in a `WriteGate` service.** Rejected: YAGNI — a bare `asyncio.Lock`
  on `CoreCtx` is sufficient today.

## Decision

1. **`CoreCtx.write_lock: asyncio.Lock`** is held across every
   `commit=False`→`commit()` window. Repos stay lock-free (no reentrancy);
   the calling service wraps. Single-statement `commit=True` writers do
   not take the lock (one execute+commit is indivisible at the event-loop
   level). No network awaits inside the lock.

2. **AST guard test** (`tests/unit/test_write_lock_guard.py`) enforces
   that every call passing `commit=False` is inside an
   `async with …write_lock:` block. Escape hatch: `# write-lock-ok`
   pragma.

3. **`_handle_result` exceptions route through `_retry_or_fail`.** A
   `try/except Exception` around the `_handle_result` call in `_tick`
   resets rows to `pending` (retryable) or `failed` (at ceiling) instead
   of stranding them `in_flight`. Boot-time `reset_in_flight_to_pending`
   stays as the SIGKILL safety net.

## Consequences

- Multi-statement DB writes are atomic under concurrent access — no
  premature commits, no duplicate CatDV writes on resume.
- A new `commit=False` site added without the lock fails CI (guard test).
- `_handle_result` exceptions no longer strand rows — the topbar count
  stays honest and the rows retry on the next tick.
- Single-statement writers are unaffected (no lock, no latency cost).
