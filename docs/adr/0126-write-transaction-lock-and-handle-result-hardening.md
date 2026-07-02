# 0126. Connection-level write serialization + `_handle_result` hardening

**Date:** 2026-06-30 (revised 2026-07-01)
**Status:** Accepted
**Lifespan:** Invariant

## Context

The app hands one shared `aiosqlite.Connection` to every worker and every
route (`CoreCtx.db`, consumed via `db_provider`). In aiosqlite's default
`isolation_level` mode the first DML opens a connection-scoped transaction
that stays open until *some* `commit()` lands — and there was no lock
serializing writers.

Two failure modes followed:

1. **Atomicity break.** Multi-statement `commit=False`→`commit()` windows
   (`WriteQueue.enqueue_apply`, `EnumService.reconcile_seeds` +
   `_ensure_materialised`, `PricingService.reconcile_seeds`,
   `backfill_clip_versions`) could have their transaction prematurely
   committed by *any* other writer's `commit()` on the same connection. A
   crash in the yield window left half-finished state committed → duplicate
   writes to the seat-limited CatDV upstream on resume.

2. **`in_flight` stranding / duplicate re-apply.** An exception in
   `SyncEngine._handle_result` (e.g. `SQLITE_BUSY` from Litestream checkpoint
   contention) bubbled to `_loop`'s catchall, leaving rows `in_flight`.

The first shot at (1) wrapped each `commit=False` window in a per-service
`asyncio.Lock`. That was **insufficient**: the lock only serialized the five
wrapped windows against each other. Every *other* writer — every repo call
with the default `commit=True` (a separate `executemany` + `commit`, ~100
call sites), most notably `SyncEngine._handle_result`'s `mark_applied` — still
committed on the shared connection **without** taking the lock, and its
`commit()` would flush a wrapped window mid-write. "One execute+commit is
indivisible" is false: they are two separate `await` points, and the harm is
not the writer's own divisibility but that its `commit()` lands *someone
else's* open transaction. Serializing every writer at ~100 call sites is not
enforceable.

## Alternatives

- **Per-`commit=False`-window `asyncio.Lock` (first attempt).** Rejected: does
  not serialize the ~100 single-statement `commit=True` writers, so windows
  are still splittable. See Context.
- **Hold a lock across the SyncEngine network apply call.** Rejected: blocks
  every DB writer for each CatDV round-trip and doesn't fix stranding.
- **Connection-per-writer pool.** Rejected: conflicts with invariant #21 ("a
  single write connection on the request critical path"), brings file-level
  `SQLITE_BUSY`, huge refactor.

## Decision

1. **Serialize at the connection, not the call site.** `open_db`'s connection
   is wrapped in **`WriteSerializedConnection`** (`backend/app/db.py`), created
   once in `CoreCtx.create` and handed to every service as `ctx.db`. It holds
   a single lock from the statement that opens a transaction until the matching
   `commit()`/`rollback()`, so no two write transactions ever interleave —
   automatically, for *every* writer, with **zero changes at the ~100 call
   sites**. Properties:
   - **Uniform rule, no SQL parsing.** Every statement takes the lock, runs,
     then consults `in_transaction` — SQLite's own ground truth. A read opened
     no transaction → release immediately; a write opened one → hold until its
     `commit()`/`rollback()`. (An earlier revision guessed read-vs-write from
     the SQL verb to keep reads lock-free; that heuristic was dropped for a
     simpler, guess-free rule. Reads now briefly serialize behind an in-flight
     write window, which is fine — windows hold no network awaits and are
     short.)
   - **Reentrant per task.** A task already mid-window re-enters freely.
   - **Cancellation-safe.** If a task is cancelled mid-transaction, the *next*
     writer reclaims the orphaned lock (a synchronous check → no double-release
     race) and rolls the partial transaction back. A leaked lock can never
     wedge every writer.
   - **Lock owned by the wrapper, created lazily per event loop.** No lock is
     threaded through `CoreCtx`; a single loop in production means a single
     lock, while a test that drives the connection from a second loop simply
     gets a second lock (the first loop is dead, so its state is moot).
   The per-service `write_lock` wrapping is removed; those windows are now
   plain `commit=False`→`commit()` sequences made atomic by the connection.

2. **Guard test** (`tests/unit/test_write_lock_guard.py`) enforces that
   `aiosqlite.connect(...)` lives only in `db.py` and that `context.py` wraps
   the connection — so no module can reintroduce a raw, unserialized
   connection. Escape hatch: `# raw-conn-ok`.

3. **`_handle_result` never re-applies a succeeded write.** The `try/except`
   around `_handle_result` in `_tick` branches on `result.status`: for an
   **ok** result the upstream write already landed, so a bookkeeping failure
   is logged and the op is finalized `applied` best-effort — it is **never**
   routed through `_retry_or_fail` (which would reset it to `pending` and
   duplicate the CatDV write). Non-ok results (no upstream write) still reset
   for another attempt. Boot-time `reset_in_flight_to_pending` stays as the
   SIGKILL safety net.

## Consequences

- Multi-statement DB writes are atomic under concurrent access against **all**
  writers — no premature commits, no duplicate CatDV writes on resume.
- A module that opens its own connection (bypassing the serializer) fails CI.
- A bookkeeping failure after a successful apply no longer duplicates the
  upstream write nor marks a succeeded op failed.
- Reads briefly serialize behind an in-flight write window; the single shared
  connection already serialized all work on one worker thread, so the net cost
  is one uncontended lock acquire/release per statement.
