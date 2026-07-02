"""SQLite connection helper and the connection-level write serializer.

`open_db` returns an aiosqlite connection with WAL, foreign keys, and a 5s
busy timeout. `WriteSerializedConnection` wraps that connection so writes are
serialized on a single lock — see issue #115 and ADR 0126.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite
from aiosqlite.context import Result


@asynccontextmanager
async def open_db(path: Path) -> AsyncIterator[aiosqlite.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    try:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=5000")
        yield conn
    finally:
        await conn.close()


class WriteSerializedConnection:
    """Serializes writes on the single shared aiosqlite connection (issue #115).

    The connection uses *implicit transactions*: the first write opens a
    transaction that stays open until *some* ``commit()`` lands, and a
    connection has exactly one transaction at a time. Shared across concurrent
    async tasks, one task's ``commit()`` would otherwise flush another task's
    half-written transaction. This wrapper serializes access with a single lock
    held from the statement that opens a transaction through the matching
    ``commit()`` / ``rollback()``, so two write transactions never interleave.

    The rule is uniform and needs no SQL parsing:

    * **Every statement takes the lock**, runs, then consults ``in_transaction``
      — SQLite's own ground truth. A read opened no transaction, so it releases
      immediately; a write opened one, so it *holds* the lock until its
      ``commit()`` / ``rollback()``.
    * **Reentrant per task.** A task already mid-write re-enters without waiting
      (its own second statement must not block on a lock it already holds).
    * **Cancellation-safe.** If a task is cancelled while holding an open
      transaction, the *next* writer reclaims the orphaned lock (a synchronous
      check → no double-release race) and rolls the partial transaction back. A
      leaked lock can never wedge every writer.

    The lock is created lazily per running event loop, so the wrapper keeps
    working if a test drives the same connection from more than one loop; in
    production there is one loop for the app's lifetime and thus one lock.

    (No ``__slots__``: the query-count test harness monkeypatches ``execute`` /
    ``executemany`` on the instance, which needs a ``__dict__``.)
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._owner: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------ #
    # delegation for everything we do not explicitly wrap
    # ------------------------------------------------------------------ #
    def __getattr__(self, name: str) -> Any:
        # Only fires for attributes missing on the wrapper itself. Guard the
        # private names so a lookup before __init__ finishes cannot recurse.
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._conn, name)

    @property
    def raw(self) -> aiosqlite.Connection:
        """The underlying aiosqlite connection (tests / teardown only)."""
        return self._conn

    def locked(self) -> bool:
        """True while a write transaction is being held (tests / diagnostics)."""
        return self._lock is not None and self._lock.locked()

    # ------------------------------------------------------------------ #
    # lock plumbing (all synchronous → no interleaving)
    # ------------------------------------------------------------------ #
    def _write_lock(self) -> asyncio.Lock:
        """The lock for the running event loop, created on first use. A loop
        change (only ever happens in tests) mints a fresh lock — the previous
        loop is dead, so its lock/owner state is irrelevant."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
            self._owner = None
        return self._lock

    def _reclaim_if_stale(self, lock: asyncio.Lock) -> None:
        owner = self._owner
        if owner is not None and owner.done():
            # Owner was cancelled/crashed mid-transaction without releasing.
            self._owner = None
            if lock.locked():
                lock.release()

    def _release_if_owner(self, lock: asyncio.Lock) -> None:
        if self._owner is asyncio.current_task():
            self._owner = None
            if lock.locked():
                lock.release()

    async def _safe_rollback(self) -> None:
        """Roll back an open transaction best-effort, never masking the
        exception already in flight."""
        if self._conn.in_transaction:
            try:
                await self._conn.rollback()
            except BaseException:
                pass

    # ------------------------------------------------------------------ #
    # statement execution
    # ------------------------------------------------------------------ #
    def execute(self, sql: str, parameters: Any = None) -> Result[aiosqlite.Cursor]:
        # Returned object is awaitable AND an async context manager, mirroring
        # aiosqlite.Connection.execute (so `async with conn.execute(...)` works).
        return Result(self._run(self._conn.execute, sql, parameters))

    def executemany(self, sql: str, parameters: Any) -> Result[aiosqlite.Cursor]:
        return Result(self._run(self._conn.executemany, sql, parameters))

    async def _run(self, method: Any, sql: str, parameters: Any) -> aiosqlite.Cursor:
        lock = self._write_lock()
        task = asyncio.current_task()
        acquired = False
        if self._owner is not task:
            # Only meaningful when acquiring: a live owner-task can't be stale.
            self._reclaim_if_stale(lock)
            await lock.acquire()
            acquired = True
            # A still-open transaction here is an orphan from a reclaimed
            # (cancelled) owner — roll it back so we start clean.
            if self._conn.in_transaction:
                await self._conn.rollback()
        try:
            if parameters is None:
                cursor = await method(sql)
            else:
                cursor = await method(sql, parameters)
        except BaseException:
            # Our statement failed. Abort any partial transaction we opened and
            # release the lock if this call acquired / holds it.
            if acquired or self._owner is task:
                await self._safe_rollback()
                self._owner = None
                if lock.locked():
                    lock.release()
            raise
        if self._conn.in_transaction:
            # A write opened (or continued) a transaction — keep holding the
            # lock, owned by this task, until commit()/rollback().
            self._owner = task
        elif acquired:
            # A read opened no transaction — release immediately.
            self._owner = None
            lock.release()
        return cursor

    async def executescript(self, sql_script: str) -> Any:
        # executescript implicitly COMMITs any pending transaction, so it must
        # own the connection exclusively. Boot-only in practice.
        task = asyncio.current_task()
        if self._owner is task:
            return await self._conn.executescript(sql_script)
        lock = self._write_lock()
        self._reclaim_if_stale(lock)
        async with lock:
            if self._conn.in_transaction:
                await self._conn.rollback()
            return await self._conn.executescript(sql_script)

    async def commit(self) -> None:
        await self._finish(commit=True)

    async def rollback(self) -> None:
        await self._finish(commit=False)

    async def _finish(self, *, commit: bool) -> None:
        """Shared commit()/rollback(): the owner ends its own window and
        releases the lock. A call from a task that owns no window is a harmless
        no-op when nothing is open, or serializes to roll back an orphan it does
        not own — it can never flush a live writer's window."""
        lock = self._write_lock()
        if self._owner is asyncio.current_task():
            try:
                if commit:
                    await self._conn.commit()
                else:
                    await self._conn.rollback()
            finally:
                self._release_if_owner(lock)
            return
        if not self._conn.in_transaction:
            return
        self._reclaim_if_stale(lock)
        async with lock:
            if self._conn.in_transaction:
                await self._conn.rollback()  # orphan cleanup; not our data
            if commit:
                await self._conn.commit()

    async def close(self) -> None:
        await self._conn.close()
