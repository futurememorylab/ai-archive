"""Unit tests for WriteSerializedConnection — the connection-level write
serializer that closes issue #115.

The wrapped aiosqlite connection runs in implicit-transaction mode and is
shared across concurrent tasks. Without serialization, one task's commit()
prematurely commits another task's in-progress multi-statement write. The
wrapper holds a single write lock from the statement that opens a transaction
until the matching commit()/rollback(), so no two write transactions ever
interleave — while leaving reads unlocked and surviving mid-transaction task
cancellation without leaking the lock.
"""

import asyncio

import aiosqlite
import pytest

from backend.app.db import WriteSerializedConnection


async def _conn() -> tuple[WriteSerializedConnection, aiosqlite.Connection]:
    raw = await aiosqlite.connect(":memory:")
    await raw.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    await raw.commit()
    return WriteSerializedConnection(raw), raw


async def test_read_does_not_hold_the_lock_after_returning():
    # A SELECT opens no transaction, so the lock is released the moment the
    # read statement returns.
    db, raw = await _conn()
    try:
        cur = await db.execute("SELECT count(*) FROM t")
        await cur.fetchone()
        assert not db.locked()
    finally:
        await raw.close()


async def test_async_with_execute_read_pattern_works():
    # poster_cache.py uses `async with conn.execute(...) as cur:`
    db, raw = await _conn()
    try:
        await db.execute("INSERT INTO t (v) VALUES ('a')")
        await db.commit()
        async with db.execute("SELECT v FROM t") as cur:
            row = await cur.fetchone()
        assert row[0] == "a"
        assert not db.locked()
    finally:
        await raw.close()


async def test_write_holds_lock_until_commit():
    db, raw = await _conn()
    try:
        await db.execute("INSERT INTO t (v) VALUES ('x')")  # opens txn
        assert db.locked()  # held across the open write transaction
        await db.commit()
        assert not db.locked()  # released on commit
    finally:
        await raw.close()


async def test_write_releases_lock_on_rollback():
    db, raw = await _conn()
    try:
        await db.execute("INSERT INTO t (v) VALUES ('x')")
        assert db.locked()
        await db.rollback()
        assert not db.locked()
        cur = await db.execute("SELECT count(*) FROM t")
        assert (await cur.fetchone())[0] == 0
    finally:
        await raw.close()


async def test_multi_statement_window_is_reentrant_same_task():
    # A commit=False window: two writes then one commit, all on one task, must
    # not deadlock and must land atomically.
    db, raw = await _conn()
    try:
        await db.execute("INSERT INTO t (v) VALUES ('a')")
        await db.execute("INSERT INTO t (v) VALUES ('b')")
        assert db.locked()
        await db.commit()
        assert not db.locked()
        cur = await db.execute("SELECT count(*) FROM t")
        assert (await cur.fetchone())[0] == 2
    finally:
        await raw.close()


async def test_concurrent_writer_cannot_split_a_window():
    """The core #115 guarantee: while task A holds an open multi-statement
    window, task B's write blocks until A commits — B can never inject a
    commit that splits A's transaction."""
    db, raw = await _conn()
    b_committed = asyncio.Event()
    a_can_finish = asyncio.Event()

    async def task_a():
        await db.execute("INSERT INTO t (v) VALUES ('a1')")  # opens A's txn
        # Let B try to run while A's window is open.
        await asyncio.sleep(0.02)
        # B must NOT have committed anything yet — it is blocked on the lock.
        assert not b_committed.is_set()
        await db.execute("INSERT INTO t (v) VALUES ('a2')")
        await db.commit()
        a_can_finish.set()

    async def task_b():
        await asyncio.sleep(0.005)  # ensure A opens its txn first
        await db.execute("INSERT INTO t (v) VALUES ('b1')")  # blocks on lock
        await db.commit()
        b_committed.set()

    try:
        await asyncio.gather(task_a(), task_b())
        assert b_committed.is_set()
        cur = await db.execute("SELECT count(*) FROM t")
        assert (await cur.fetchone())[0] == 3
    finally:
        await raw.close()


async def test_cancelled_mid_window_does_not_leak_lock_and_rolls_back():
    """If a task is cancelled while holding an open transaction, the next
    writer reclaims the orphaned lock and rolls the partial write back."""
    db, raw = await _conn()
    opened = asyncio.Event()

    async def victim():
        await db.execute("INSERT INTO t (v) VALUES ('ghost')")  # opens txn, holds lock
        opened.set()
        await asyncio.sleep(10)  # will be cancelled here, mid-transaction

    try:
        v = asyncio.create_task(victim())
        await opened.wait()
        assert db.locked()
        v.cancel()
        with pytest.raises(asyncio.CancelledError):
            await v

        # A fresh writer must be able to proceed (lock reclaimed) and must not
        # see the ghost row (partial txn rolled back).
        await db.execute("INSERT INTO t (v) VALUES ('real')")
        await db.commit()
        cur = await db.execute("SELECT v FROM t")
        rows = [r[0] for r in await cur.fetchall()]
        assert rows == ["real"]
        assert not db.locked()
    finally:
        await raw.close()


async def test_failed_statement_releases_lock_and_aborts_txn():
    db, raw = await _conn()
    try:
        await db.execute("INSERT INTO t (id, v) VALUES (1, 'a')")
        await db.commit()
        # Duplicate PK inside a fresh window → IntegrityError.
        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute("INSERT INTO t (id, v) VALUES (1, 'dup')")
        assert not db.locked()  # lock released despite the failure
        # Connection is usable and the failed write left nothing behind.
        cur = await db.execute("SELECT count(*) FROM t")
        assert (await cur.fetchone())[0] == 1
    finally:
        await raw.close()


async def test_executemany_holds_and_commits_atomically():
    db, raw = await _conn()
    try:
        await db.executemany("INSERT INTO t (v) VALUES (?)", [("a",), ("b",), ("c",)])
        assert db.locked()
        await db.commit()
        assert not db.locked()
        cur = await db.execute("SELECT count(*) FROM t")
        assert (await cur.fetchone())[0] == 3
    finally:
        await raw.close()


async def test_getattr_delegates_to_wrapped_connection():
    db, raw = await _conn()
    try:
        assert db.in_transaction == raw.in_transaction
    finally:
        await raw.close()


async def test_read_serializes_behind_a_held_write_window():
    """Under uniform locking a read waits until an in-flight write window
    commits — so it never observes the window's half-written state."""
    db, raw = await _conn()
    holder_open = asyncio.Event()
    read_started = asyncio.Event()
    read_done = asyncio.Event()

    async def holder():
        await db.execute("INSERT INTO t (v) VALUES ('w')")  # opens window, holds lock
        holder_open.set()
        # Give the reader time to attempt its read and block on the lock.
        await asyncio.sleep(0.05)
        assert read_started.is_set() and not read_done.is_set()
        await db.commit()

    async def reader():
        await holder_open.wait()
        read_started.set()
        cur = await db.execute("SELECT count(*) FROM t")  # blocks until commit
        await cur.fetchone()
        read_done.set()

    try:
        await asyncio.gather(holder(), reader())
        assert read_done.is_set()
        assert not db.locked()
    finally:
        await raw.close()
