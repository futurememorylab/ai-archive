"""Query-count regression guard for aiosqlite.

`assert_query_count(conn, max_n)` counts SQL statements executed on `conn`
within an `async with` block. Raises if the count exceeds `max_n`.

The implementation patches `conn.execute` / `conn.executemany` /
`conn.executescript` for the duration of the block so it can hook every
statement without depending on sqlite3's tracebacks (aiosqlite's worker-
thread bridge makes set_trace_callback fragile).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite


@dataclass
class _Counter:
    count: int = 0


@asynccontextmanager
async def assert_query_count(
    conn: aiosqlite.Connection,
    max_n: int,
) -> AsyncIterator[_Counter]:
    """Async context manager that asserts no more than `max_n` SQL
    statements run on `conn` during the block. Yields a counter so the
    caller can also assert the exact count if desired.

    Counts execute / executemany / executescript calls. Does NOT count
    fetchone / fetchall (those don't generate SQL).
    """
    counter = _Counter()
    orig_execute = conn.execute
    orig_executemany = conn.executemany
    orig_executescript = conn.executescript

    async def _wrapped_execute(*args, **kwargs):
        counter.count += 1
        return await orig_execute(*args, **kwargs)

    async def _wrapped_executemany(*args, **kwargs):
        counter.count += 1
        return await orig_executemany(*args, **kwargs)

    async def _wrapped_executescript(*args, **kwargs):
        counter.count += 1
        return await orig_executescript(*args, **kwargs)

    conn.execute = _wrapped_execute  # type: ignore[method-assign]
    conn.executemany = _wrapped_executemany  # type: ignore[method-assign]
    conn.executescript = _wrapped_executescript  # type: ignore[method-assign]
    try:
        yield counter
        if counter.count > max_n:
            raise AssertionError(
                f"query count {counter.count} > max_n={max_n}; "
                "an N+1 may have been reintroduced. See ADR 0046."
            )
    finally:
        conn.execute = orig_execute  # type: ignore[method-assign]
        conn.executemany = orig_executemany  # type: ignore[method-assign]
        conn.executescript = orig_executescript  # type: ignore[method-assign]
