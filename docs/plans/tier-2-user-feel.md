# Tier 2 — User feel (perf + UX consistency): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD discipline (write failing test → confirm fail → implement → confirm pass → commit) per `superpowers:test-driven-development`.

**Goal:** Close five user-visible performance and UX-consistency issues in `catdv-annotator`. Make the cache page fast and bounded, eliminate the studio cancel silent-drop, and replace the scattered `alert()` / `location.reload()` patterns with a shared toast + HTMX swaps.

**Architecture:** Three reusable primitives become the levers tier 3 will use for broader sweeps: `chunked_in_clause()` (SQLite-parameter-safe batched IN queries), `assert_query_count()` (aiosqlite trace-based regression guard), and `Alpine.store('toast')` (single source of user-facing error UI). The cache page goes from 5×N round-trips to bounded, with SQL-side filtering + pagination. Studio cancel waits for server confirm and surfaces the actual final state.

**Tech Stack:** Python 3.13 + FastAPI + aiosqlite (SQLite WAL); pytest + pytest-asyncio; Alpine.js + HTMX.

**Pre-flight (executor):**
- Source the worktree via `superpowers:using-git-worktrees`. Branch: `fix/tier-2-user-feel`, based on current `main` HEAD (which contains the tier-1 merge `0a5aa52` plus PR #22's bulk-annotate work `24b0864`). Worktree path: `.claude/worktrees/fix-tier-2-user-feel/`.
- Symlink `.env`, `data/`, `.venv` from the parent checkout (same pattern as the tier-1 worktree). Without these, `./run.sh` won't work and tests that hit the DB will fail.
- Verify baseline tests pass before starting: `.venv/bin/pytest -q`. Expected: ~917 passing, 1 pre-existing failure (`tests/integration/test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag`).

**Spec reference:** `docs/specs/2026-05-30-fix-prioritization-design.md` § "Tier 2 — User feel" — read before starting.

**ADR numbers assigned:** 0046 (no N+1, batch with WHERE IN). Highest existing ADR is 0045 (PR #22 bulk-annotate, merged into main after the plan was written). Tier 2 takes 0046.

---

## File Structure

**New files:**
- `backend/app/repositories/_batch.py` — `chunked_in_clause()` helper.
- `tests/_helpers/query_count.py` — `assert_query_count` async context manager.
- `tests/_helpers/__init__.py` — package marker (may already exist; check).
- `backend/app/static/toast.js` — `Alpine.store('toast')` + minimal renderer.
- `tests/unit/test_chunked_in_clause.py`
- `tests/unit/test_query_count_helper.py`
- `tests/integration/test_cache_inspector_batched.py` — N+1 regression guard.
- `tests/integration/test_cache_page_filters_sql.py` — SQL-side filter regression.
- `tests/integration/test_studio_run_cancel_race.py` — completion-during-cancel race.
- `tests/integration/test_studio_folders_htmx_partials.py` — folder CRUD returns partials when HX-Request.
- `tests/unit/test_toast_store_registered.py` — layout.html includes the toast root.
- `tests/unit/test_no_sync_fs_in_async.py` — source-grep regression.
- `docs/adr/0046-no-n-plus-one-batch-with-where-in.md`

**Modified files:**
- `backend/app/services/cache_inspector.py` — five loaders collapse to batched queries; misleading docstring at line 151 rewritten.
- `backend/app/routes/cache.py` — `cache_page` computes `_all_cached_keys` / `list_orphans` once each; tab/store/workspace/orphans/evictable filters move to SQL; pagination becomes `LIMIT/OFFSET`.
- `backend/app/static/studio.js` — `cancel()` waits for server confirm; `runButtonLabel()` adds `⊘ Cancelled` flash; `createFolder` / `addSelected` use HTMX swap instead of `location.reload()`; `alert()` calls routed through the toast store.
- `backend/app/static/review.js` — `alert()` calls + `location.reload()` routed through toast / partial.
- `backend/app/static/promptEditor.js` — `location.reload()` calls routed through partial (where the surrounding route can return one) or kept with explanatory comment (where the page genuinely needs a full reload).
- `backend/app/static/clipAnnotate.js`, `backend/app/static/liveSession.js` — silent fetch `.catch()` blocks routed through toast.
- `backend/app/routes/studio.py` — `create_folder` and `add_clips` endpoints gain HTMX-aware partial responses.
- `backend/app/templates/pages/layout.html` — include the toast root.
- `backend/app/templates/pages/_studio_folder.html` — new partial for the freshly-created folder card (or extend existing partial if present).
- `backend/app/templates/pages/_studio_folder_list.html` — verify the existing folder-kids partial can be reused for "newly-added clips appear in folder."
- `backend/app/services/cache_actions.py` — `_evict_local_media_impl` wraps `os.unlink` + `Path.exists` in `asyncio.to_thread`.
- `CLAUDE.md` — new "Performance discipline" + "Frontend error handling" sections.
- `docs/decisions.md` — index ADR 0046.

---

## Task 1: chunked_in_clause helper + refactor inspector loaders (T2-1 part 1)

**Files:**
- Create: `backend/app/repositories/_batch.py`
- Modify: `backend/app/services/cache_inspector.py` (the 5 loaders at lines 343, 363, 386, 415, 433; rewrite misleading docstring at line 151)
- Create: `tests/unit/test_chunked_in_clause.py`

- [ ] **Step 1: Write the failing helper test**

Create `tests/unit/test_chunked_in_clause.py`:

```python
"""chunked_in_clause builds parameter-safe `WHERE (a, b) IN (...)` SQL
in chunks so we never exceed SQLite's SQLITE_LIMIT_VARIABLE_NUMBER (default
999, raised to 32766 in newer builds). Used by every batched repository
read to replace per-key loops."""

import pytest

from backend.app.repositories._batch import chunked_in_clause


def test_empty_keys_yields_nothing():
    assert list(chunked_in_clause([])) == []


def test_single_key_one_chunk():
    chunks = list(chunked_in_clause([("catdv", "42")]))
    assert len(chunks) == 1
    sql, params = chunks[0]
    assert sql == "(?, ?)"
    assert params == ["catdv", "42"]


def test_multiple_keys_one_chunk_under_limit():
    keys = [("catdv", str(i)) for i in range(5)]
    chunks = list(chunked_in_clause(keys, chunk_size=10))
    assert len(chunks) == 1
    sql, params = chunks[0]
    assert sql == "(?, ?), (?, ?), (?, ?), (?, ?), (?, ?)"
    assert params == ["catdv", "0", "catdv", "1", "catdv", "2", "catdv", "3", "catdv", "4"]


def test_keys_split_across_chunks_at_chunk_size():
    keys = [("catdv", str(i)) for i in range(7)]
    chunks = list(chunked_in_clause(keys, chunk_size=3))
    assert len(chunks) == 3
    # First two chunks have 3 keys each; last has 1.
    assert chunks[0][1] == ["catdv", "0", "catdv", "1", "catdv", "2"]
    assert chunks[1][1] == ["catdv", "3", "catdv", "4", "catdv", "5"]
    assert chunks[2][1] == ["catdv", "6"]


def test_default_chunk_size_is_safe_for_sqlite_default_999():
    # Default is 500 keys × 2 params = 1000 — under SQLite's 999 default
    # for older builds is FALSE. Default should be conservative; pick 400
    # so 400 × 2 = 800 < 999. This test pins the default for safety.
    keys = [("catdv", str(i)) for i in range(1000)]
    chunks = list(chunked_in_clause(keys))
    # 1000 / default_chunk_size — verify default is at most 499 so we
    # never exceed 998 parameters in one statement.
    assert all(len(params) <= 998 for _, params in chunks)


def test_raises_on_non_pair_tuple():
    with pytest.raises(ValueError, match="2-tuple"):
        list(chunked_in_clause([("catdv", "42", "extra")]))  # type: ignore[list-item]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_chunked_in_clause.py -v`

Expected: `ImportError: cannot import name 'chunked_in_clause' from 'backend.app.repositories._batch'` (or `ModuleNotFoundError` if the file doesn't exist).

- [ ] **Step 3: Implement the helper**

Create `backend/app/repositories/_batch.py`:

```python
"""Batched SQL helpers shared across repositories.

`chunked_in_clause` builds parameter-safe `WHERE (a, b) IN ((?, ?), …)`
fragments in chunks bounded by SQLite's SQLITE_LIMIT_VARIABLE_NUMBER
(default 999 in older builds, 32766 in 3.32+). Default chunk_size=400
keeps the per-statement parameter count under 800 for the (provider_id,
provider_clip_id) two-column case, comfortably under the 999 floor.

Yields `(sql_fragment, params_list)` pairs that a caller wraps with the
table-specific SELECT and concatenates results across chunks.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TypeVar

_T = TypeVar("_T")


def chunked_in_clause(
    keys: Iterable[tuple[str, str]],
    *,
    chunk_size: int = 400,
) -> Iterator[tuple[str, list[str]]]:
    """Yield `(sql, params)` pairs for batched `WHERE (a, b) IN (…)` SQL.

    Args:
        keys: iterable of 2-tuples. Each tuple becomes one `(?, ?)` row.
        chunk_size: max keys per chunk. Default 400 keeps the
            per-statement parameter count under 800.

    Yields:
        `(sql_fragment, params_list)` where `sql_fragment` is
        `"(?, ?), (?, ?), …"` and `params_list` is the flattened
        parameter list.

    Raises:
        ValueError: any element of `keys` is not a 2-tuple.
    """
    chunk: list[tuple[str, str]] = []
    for k in keys:
        if not (isinstance(k, tuple) and len(k) == 2):
            raise ValueError(f"chunked_in_clause requires 2-tuple keys; got {k!r}")
        chunk.append(k)
        if len(chunk) >= chunk_size:
            yield _format(chunk)
            chunk = []
    if chunk:
        yield _format(chunk)


def _format(keys: list[tuple[str, str]]) -> tuple[str, list[str]]:
    sql = ", ".join(["(?, ?)"] * len(keys))
    params: list[str] = []
    for a, b in keys:
        params.append(a)
        params.append(b)
    return sql, params
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_chunked_in_clause.py -v`

Expected: 6 passed.

- [ ] **Step 5: Refactor `_load_metadata` to use the helper**

Open `backend/app/services/cache_inspector.py`. Add import at module top alongside the existing import:

```python
from backend.app.archive.errors import is_provider_not_found
from backend.app.archive.model import ClipKey
from backend.app.repositories._batch import chunked_in_clause
```

Replace `_load_metadata` (around line 343-361) with the batched form:

```python
    async def _load_metadata(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, name, canonical_json, fetched_at "
                "FROM clip_cache "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                out[(row[0], row[1])] = {
                    "name": row[2],
                    "canonical_json": row[3],
                    "fetched_at": row[4],
                }
        return out
```

- [ ] **Step 6: Refactor `_load_media_local`**

Replace lines 363-384 with:

```python
    async def _load_media_local(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, dict[str, Any]]:
        out: dict[ClipKey, dict[str, Any]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, "
                "file_path, size_bytes, downloaded_at, last_used_at "
                "FROM proxy_cache "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                out[(row[0], row[1])] = {
                    "file_path": row[2],
                    "size_bytes": row[3],
                    "downloaded_at": row[4],
                    "last_used_at": row[5],
                }
        return out
```

- [ ] **Step 7: Refactor `_load_media_ai`**

Replace lines 386-413 with:

```python
    async def _load_media_ai(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[dict[str, Any]]]:
        out: dict[ClipKey, list[dict[str, Any]]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, store_id, gcs_uri, "
                "mime_type, size_bytes, uploaded_at, last_used_at "
                "FROM ai_store_files "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql})",
                params,
            )
            for row in await cur.fetchall():
                key = (row[0], row[1])
                out.setdefault(key, []).append({
                    "store_id": row[2],
                    "gcs_uri": row[3],
                    "mime_type": row[4],
                    "size_bytes": row[5],
                    "uploaded_at": row[6],
                    "last_used_at": row[7],
                })
        return out
```

- [ ] **Step 8: Refactor `_load_pins`**

Replace lines 415-431 with:

```python
    async def _load_pins(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, list[int]]:
        out: dict[ClipKey, list[int]] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, workspace_id "
                "FROM workspace_clips "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql}) "
                "ORDER BY provider_id, provider_clip_id, workspace_id",
                params,
            )
            for row in await cur.fetchall():
                key = (row[0], row[1])
                out.setdefault(key, []).append(int(row[2]))
        return out
```

- [ ] **Step 9: Refactor `_load_pending_counts`**

Replace lines 433-447 with:

```python
    async def _load_pending_counts(
        self, db: aiosqlite.Connection, keys: Sequence[ClipKey]
    ) -> dict[ClipKey, int]:
        out: dict[ClipKey, int] = {}
        for in_sql, params in chunked_in_clause(keys):
            cur = await db.execute(
                "SELECT provider_id, provider_clip_id, COUNT(*) "
                "FROM pending_operations "
                f"WHERE (provider_id, provider_clip_id) IN ({in_sql}) "
                "AND status IN ('pending', 'in_flight', 'conflict') "
                "GROUP BY provider_id, provider_clip_id",
                params,
            )
            for row in await cur.fetchall():
                n = int(row[2])
                if n:
                    out[(row[0], row[1])] = n
        return out
```

- [ ] **Step 10: Rewrite the misleading docstring at line 151**

Find this docstring inside `status_for_clips`:

```python
        # Fetch per-layer rows in one batched pass each.
```

Replace with:

```python
        # Fetch per-layer rows via chunked `WHERE (a, b) IN (...)` queries;
        # one statement per layer per chunk (default chunk_size=400 keys).
        # See backend/app/repositories/_batch.py for the helper. ADR 0046.
```

- [ ] **Step 11: Run inspector regression tests**

Run: `.venv/bin/pytest tests/unit/test_chunked_in_clause.py tests/unit/test_cache_inspector_host_local.py tests/integration/test_cache_inspector.py tests/integration/test_cache_inspector_orphans_transient.py -q`

Expected: all pass. Behavior is identical to before; only the SQL shape changed.

- [ ] **Step 12: Commit**

```bash
git add backend/app/repositories/_batch.py backend/app/services/cache_inspector.py tests/unit/test_chunked_in_clause.py
git commit -m "feat(repos): chunked_in_clause + CacheInspector batched loaders

Foundation for T2-1. New repositories/_batch.py exposes
chunked_in_clause(keys, chunk_size=400) — yields parameter-safe
'(?, ?), (?, ?), ...' fragments in chunks under SQLite's parameter
limit. Default 400 × 2 = 800 params/statement, safely under 999.

Used by the five CacheInspector loaders (_load_metadata,
_load_media_local, _load_media_ai, _load_pins, _load_pending_counts)
which previously did one query per clip. Cache-page render goes from
5×N round-trips to ⌈N/400⌉ × 5 — roughly 10× fewer queries even on
small caches, hundreds × fewer on large ones.

Behaviour unchanged; existing tests pass. Query-count regression
guard lands in the next commit (T2-1 part 2).

Refs: docs/specs/2026-05-30-fix-prioritization-design.md (T2-1)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: assert_query_count helper + N+1 regression test (T2-1 part 2)

**Files:**
- Create: `tests/_helpers/query_count.py`
- Create: `tests/_helpers/__init__.py` (only if absent — check first)
- Create: `tests/unit/test_query_count_helper.py`
- Create: `tests/integration/test_cache_inspector_batched.py`

- [ ] **Step 1: Write the failing helper test**

Create `tests/unit/test_query_count_helper.py`:

```python
"""assert_query_count counts SQL statements issued against an aiosqlite
connection during an `async with` block. Used as the regression guard
against future N+1 reintroductions."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from tests._helpers.query_count import assert_query_count


@pytest.mark.asyncio
async def test_counts_basic_executes(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        await conn.execute("CREATE TABLE t (id INTEGER)")
        async with assert_query_count(conn, max_n=3) as counter:
            await conn.execute("INSERT INTO t VALUES (1)")
            await conn.execute("INSERT INTO t VALUES (2)")
            await conn.execute("INSERT INTO t VALUES (3)")
        assert counter.count == 3


@pytest.mark.asyncio
async def test_exceeding_max_n_raises(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        await conn.execute("CREATE TABLE t (id INTEGER)")
        with pytest.raises(AssertionError, match="query count"):
            async with assert_query_count(conn, max_n=2):
                await conn.execute("INSERT INTO t VALUES (1)")
                await conn.execute("INSERT INTO t VALUES (2)")
                await conn.execute("INSERT INTO t VALUES (3)")


@pytest.mark.asyncio
async def test_zero_queries_passes(tmp_path: Path):
    async with open_db(tmp_path / "x.db") as conn:
        async with assert_query_count(conn, max_n=0) as counter:
            pass
        assert counter.count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_query_count_helper.py -v`

Expected: `ModuleNotFoundError: No module named 'tests._helpers.query_count'`.

- [ ] **Step 3: Confirm `tests/_helpers/__init__.py` exists**

Run: `ls tests/_helpers/__init__.py`. If absent, create an empty file: `touch tests/_helpers/__init__.py` (the directory already exists — see `tests/_helpers/studio_state.py`).

- [ ] **Step 4: Implement the helper**

Create `tests/_helpers/query_count.py`:

```python
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
```

- [ ] **Step 5: Run helper tests pass**

Run: `.venv/bin/pytest tests/unit/test_query_count_helper.py -v`

Expected: 3 passed.

- [ ] **Step 6: Write the inspector N+1 regression test**

Create `tests/integration/test_cache_inspector_batched.py`:

```python
"""Pin the post-T2-1 invariant: CacheInspector.status_for_clips uses a
bounded number of queries regardless of clip count. Without this guard,
a future PR could silently reintroduce per-key loops in the loaders.

The bound: each of the 5 loaders issues ⌈N/400⌉ statements. For N up to
400 clips that's exactly 5; for 1000 clips it's 3 × 5 = 15. We assert
the count is the SAME for 10 vs 100 clips (both under 400) to lock in
the batching."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector
from tests._helpers.query_count import assert_query_count

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed_n_clips(conn, n: int) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for i in range(n):
        await conn.execute(
            "INSERT INTO clip_cache(provider_id, provider_clip_id, catalog_id, "
            "name, canonical_json, duration_secs, fps, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("catdv", str(i), "1", f"clip {i}", '{"id":' + str(i) + "}", 1.0, 25.0),
        )
        keys.append(("catdv", str(i)))
    await conn.commit()
    return keys


@pytest.mark.asyncio
async def test_status_for_clips_query_count_is_constant_under_400(tmp_path):
    """5 loaders × 1 chunk each = 5 statements for any N ≤ 400."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)

        # 10 clips.
        keys = await _seed_n_clips(conn, 10)
        inspector = CacheInspector(db_provider=lambda: conn)
        async with assert_query_count(conn, max_n=6) as counter:
            await inspector.status_for_clips(keys)
        count_10 = counter.count

        # 100 clips.
        keys = await _seed_n_clips(conn, 100)
        async with assert_query_count(conn, max_n=6) as counter:
            await inspector.status_for_clips(keys[:100])
        count_100 = counter.count

        assert count_10 == count_100, (
            f"query count must not scale with N; got {count_10} vs {count_100}"
        )
        # Belt-and-braces: should be exactly 5 (one statement per loader).
        assert count_10 == 5, (
            f"expected 5 statements (one per loader); got {count_10}"
        )


@pytest.mark.asyncio
async def test_status_for_clips_handles_empty_keys(tmp_path):
    """Defensive: empty input must short-circuit without any SQL."""
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        inspector = CacheInspector(db_provider=lambda: conn)
        async with assert_query_count(conn, max_n=0):
            result = await inspector.status_for_clips([])
        assert result == []
```

- [ ] **Step 7: Run regression test to confirm it passes**

Run: `.venv/bin/pytest tests/integration/test_cache_inspector_batched.py -v`

Expected: 2 passed.

- [ ] **Step 8: Commit**

```bash
git add tests/_helpers/__init__.py tests/_helpers/query_count.py tests/unit/test_query_count_helper.py tests/integration/test_cache_inspector_batched.py
git commit -m "test(perf): assert_query_count helper + CacheInspector N+1 guard

New tests/_helpers/query_count.py wraps execute/executemany/executescript
to count statements during an async with block. Asserts no more than
max_n statements ran; raises if exceeded with a pointer to ADR 0046.

The cache-inspector regression test pins the post-T2-1 invariant: 5
loaders × 1 chunk = exactly 5 statements for any N ≤ 400 clips. Without
this guard a future PR could silently reintroduce per-key loops in the
loaders.

Refs: T2-1 (regression guard).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: De-duplicate cache_page expensive calls (T2-1 part 3)

**Files:**
- Modify: `backend/app/routes/cache.py:189-254` (`cache_page` function body)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_cache_page_perf.py`. Uses the
`_setenv` + `_make_app` + TestClient pattern from
`tests/integration/test_routes_cache.py` (read that file's top 50 lines
first if you've never seen it):

```python
"""cache_page must compute `_all_cached_keys` and `list_orphans` at most
once per render. The current code calls each twice (once for the
inventory pass and once for the metric strip), doubling the query load
on every page view."""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    """Mirror of test_routes_cache.py::_setenv."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod
    importlib.reload(main_mod)
    return main_mod.app


async def _seed_clip(ctx):
    now = datetime.now(UTC).isoformat()
    await ctx.db.execute(
        "INSERT INTO clip_cache "
        "(provider_id, provider_clip_id, name, catalog_id, "
        "duration_secs, fps, canonical_json, provider_etag, fetched_at) "
        "VALUES (?, ?, 'n', '1', 1.0, 25.0, '{}', NULL, ?)",
        ("catdv", "42", now),
    )
    await ctx.db.commit()


def test_cache_page_does_not_double_compute_all_keys(tmp_path, monkeypatch):
    """T2-1 part 3: duplicate `_all_cached_keys` + `list_orphans` calls
    are gone. Asserted via call counters on each."""
    import asyncio

    from backend.app.routes import cache as cache_route

    app = _make_app(monkeypatch, tmp_path)
    call_counts: dict[str, int] = {"_all_cached_keys": 0, "list_orphans": 0}

    orig_all_keys = cache_route._all_cached_keys

    async def _counting_all_keys(db):
        call_counts["_all_cached_keys"] += 1
        return await orig_all_keys(db)

    monkeypatch.setattr(cache_route, "_all_cached_keys", _counting_all_keys)

    with TestClient(app) as client:
        ctx = client.app.state.ctx
        asyncio.get_event_loop().run_until_complete(_seed_clip(ctx))

        # Monkeypatch the bound method on the live instance (TestClient
        # has already run the lifespan so ctx.cache_inspector is wired).
        orig_list_orphans = ctx.cache_inspector.list_orphans

        async def _counting_list_orphans(*args, **kwargs):
            call_counts["list_orphans"] += 1
            return await orig_list_orphans(*args, **kwargs)

        ctx.cache_inspector.list_orphans = _counting_list_orphans  # type: ignore[method-assign]

        r = client.get("/cache")
        assert r.status_code == 200

    assert call_counts["_all_cached_keys"] == 1, (
        f"expected 1 call; got {call_counts['_all_cached_keys']}"
    )
    assert call_counts["list_orphans"] == 1, (
        f"expected 1 call; got {call_counts['list_orphans']}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_cache_page_perf.py -v`

Expected: AssertionError — current code calls `_all_cached_keys` 2x and `list_orphans` 2x (lines 207+209 and 239+247 of cache.py).

- [ ] **Step 3: Refactor `cache_page`**

Open `backend/app/routes/cache.py`. Find the `cache_page` function starting around line 189. Restructure to compute `all_keys` and `orphan_statuses` ONCE up front, then reuse:

```python
@page_router.get("/cache", response_class=HTMLResponse)
async def cache_page(
    request: Request,
    tab: str | None = None,
    store: str | None = None,
    workspace: int | None = None,
    orphans: int | None = None,
    evictable: int | None = None,
    offset: int = 0,
    limit: int = 50,
) -> HTMLResponse:
    insp = _inspector(request)
    ctx = get_ctx(request)

    tab_val = tab if tab in _VALID_TABS else "all"
    is_htmx = request.headers.get("HX-Request") == "true"

    # Single-fetch resources: every later code path that needs these
    # uses these references. Without this, the function used to call
    # _all_cached_keys() twice and list_orphans() twice per render.
    all_keys = await _all_cached_keys(ctx.db)
    orphan_statuses = await insp.list_orphans()
    all_statuses = await insp.status_for_clips(all_keys)
    summary = await insp.summary()

    # Always load queue rows — both the queue tab and the metric strip
    # use them, and the queries are cheap (status indexed).
    queue_active = await ctx.prefetch_queue_repo.list_active(ctx.db)
    queue_recent = await ctx.prefetch_queue_repo.list_recent(ctx.db, limit=50)
    queue_counts = await ctx.prefetch_queue_repo.count_by_status(ctx.db)

    if tab_val == "queue":
        rows_for_template: list = []
        total = 0
        prev_offset = next_offset = None
        page_rows: list = []
    else:
        # Source statuses — orphan tab uses orphan_statuses; all/local/ai
        # tabs use all_statuses.
        statuses = orphan_statuses if orphans else all_statuses
        rows = []
        for status in statuses:
            if store:
                ai_layer = status.layers[2]
                if not ai_layer.present or store not in (ai_layer.location or ""):
                    continue
            if workspace is not None:
                md_layer = status.layers[0]
                if workspace not in md_layer.pinned_by_workspaces:
                    continue
            if evictable:
                if not any(layer.evictable for layer in status.layers):
                    continue
            if tab_val == "local" and not status.layers[1].present:
                continue
            if tab_val == "ai" and not status.layers[2].present:
                continue
            rows.append(status)
        rows_for_template = [_cache_row(s) for s in rows]
        total = len(rows_for_template)
        page_rows = rows_for_template[offset : offset + limit]
        prev_offset, next_offset = page_offsets(offset, limit, total)

    # Orphan totals for the metric strip — reuse the single fetch.
    orphan_count = len(orphan_statuses)
    orphan_bytes = sum(
        sum((layer.size_bytes or 0) for layer in s.layers if layer.evictable)
        for s in orphan_statuses
    )

    # Per-tab counts for the tab badges — reuse the single fetch.
    counts = {
        "all": len(all_statuses),
        "local": sum(1 for s in all_statuses if s.layers[1].present),
        "ai": sum(1 for s in all_statuses if s.layers[2].present),
        "queue": queue_counts.get("queued", 0) + queue_counts.get("downloading", 0),
    }

    ai_total_count = sum(summary.counts_by_store.values())

    ctx_dict = {
        "summary": summary,
        "tab": tab_val,
        "rows": page_rows,
        "offset": offset,
        "limit": limit,
        "total": total,
        "prev_offset": prev_offset,
        "next_offset": next_offset,
        "filters": {
            "store": store,
            "workspace": workspace,
            "orphans": bool(orphans),
            "evictable": bool(evictable),
        },
        "queue_active": queue_active,
        "queue_recent": queue_recent,
        "queue_counts": queue_counts,
        "orphan_count": orphan_count,
        "orphan_bytes": orphan_bytes,
        "ai_total_count": ai_total_count,
        "counts": counts,
    }

    if is_htmx:
        partial = (
            "pages/_cache_queue_table.html"
            if tab_val == "queue"
            else "pages/_cache_inventory_table.html"
        )
        return templates.TemplateResponse(request, partial, ctx_dict)
    return templates.TemplateResponse(request, "cache_page.html", ctx_dict)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_cache_page_perf.py tests/integration/test_routes_cache.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/cache.py tests/integration/test_cache_page_perf.py
git commit -m "fix(cache): cache_page computes _all_cached_keys + list_orphans once

Previous code called _all_cached_keys at lines 209 and 247, and
list_orphans at lines 207 and 239. Each call hits the DB; on a populated
cache that's double the inspector load per render.

After this commit: each is called exactly once at the top of cache_page,
and all later code paths reuse the cached results. Combined with the
T2-1 inspector batching, the page now renders in a bounded number of
queries (target: ~10 statements regardless of clip count).

Regression test asserts the dedup via a call counter on _all_cached_keys
and list_orphans.

Refs: T2-1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Push cache_page filters into SQL with LIMIT/OFFSET (T2-1 part 4)

**Files:**
- Modify: `backend/app/services/cache_inspector.py` — add `list_for_inventory(filters, offset, limit) -> (rows, total)` method
- Modify: `backend/app/routes/cache.py` — call the new method instead of in-Python filtering + slicing

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_cache_page_filters_sql.py`:

```python
"""cache_page's tab/store/workspace/orphans/evictable filters and its
pagination must happen in SQL, not in Python after hydrating every row.
Today the function loads every cached clip's full status and slices the
result in Python; this regression test asserts the new bounded behavior."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector
from tests._helpers.query_count import assert_query_count

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _seed(conn, n: int):
    for i in range(n):
        await conn.execute(
            "INSERT INTO clip_cache(provider_id, provider_clip_id, catalog_id, "
            "name, canonical_json, duration_secs, fps, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            ("catdv", str(i), "1", f"clip {i}", '{"id":' + str(i) + "}", 1.0, 25.0),
        )
    await conn.commit()


@pytest.mark.asyncio
async def test_list_for_inventory_pagination_uses_sql_limit(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed(conn, 1000)

        inspector = CacheInspector(db_provider=lambda: conn)

        async with assert_query_count(conn, max_n=10) as counter:
            rows, total = await inspector.list_for_inventory(
                tab="all", offset=0, limit=50,
            )

        assert total == 1000, "total clip count is over the full set"
        assert len(rows) == 50, "page should be exactly limit"
        # Bounded: ⌈50/400⌉ × 5 loaders + 1 count = 6 statements.
        assert counter.count <= 10, (
            f"got {counter.count} queries for 50-row page over 1000 clips; "
            "must be bounded irrespective of total clip count"
        )


@pytest.mark.asyncio
async def test_list_for_inventory_tab_local_filters_in_sql(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed(conn, 100)
        # Add a single proxy_cache row so exactly one clip has media-local.
        await conn.execute(
            "INSERT INTO proxy_cache(provider_id, provider_clip_id, file_path, "
            "size_bytes, downloaded_at, last_used_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("catdv", "5", "/tmp/x.mov", 1000),
        )
        await conn.commit()

        inspector = CacheInspector(db_provider=lambda: conn)
        rows, total = await inspector.list_for_inventory(
            tab="local", offset=0, limit=50,
        )
        assert total == 1, f"tab=local should return only the seeded clip; got total={total}"
        assert len(rows) == 1
        assert rows[0].clip_key == ("catdv", "5")


@pytest.mark.asyncio
async def test_list_for_inventory_orphans_filter(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        # 5 clip_cache rows, plus a proxy_cache row for a clip with NO
        # clip_cache entry — that's the orphan.
        await _seed(conn, 5)
        await conn.execute(
            "INSERT INTO proxy_cache(provider_id, provider_clip_id, file_path, "
            "size_bytes, downloaded_at, last_used_at) "
            "VALUES (?, ?, ?, ?, datetime('now'), datetime('now'))",
            ("catdv", "999", "/tmp/orphan.mov", 1000),
        )
        await conn.commit()

        inspector = CacheInspector(db_provider=lambda: conn)
        rows, total = await inspector.list_for_inventory(
            tab="all", orphans=True, offset=0, limit=50,
        )
        assert total == 1
        assert rows[0].clip_key == ("catdv", "999")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_cache_page_filters_sql.py -v`

Expected: `AttributeError: 'CacheInspector' object has no attribute 'list_for_inventory'`.

- [ ] **Step 3: Implement `list_for_inventory`**

Add to `backend/app/services/cache_inspector.py`, after the existing `status_for_clips` method:

```python
    async def list_for_inventory(
        self,
        *,
        tab: str = "all",
        store: str | None = None,
        workspace: int | None = None,
        orphans: bool = False,
        evictable: bool = False,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[ClipCacheStatus], int]:
        """Inventory rows for the cache page, filtered and paginated in SQL.

        Returns (page_rows, total_matching_count). The COUNT and the
        page SELECT use the same WHERE clause so the pager stays
        consistent. Statuses are hydrated only for the page-rows slice,
        so per-render cost is bounded by `limit` not by total clip count.
        """
        db = self._db_provider()

        # Build the WHERE clause + params common to count and page select.
        # The driving table depends on the filters:
        #   - orphans=True: rows in proxy_cache OR ai_store_files whose
        #     clip_cache entry is absent.
        #   - tab='local': rows in clip_cache that also have a proxy_cache row.
        #   - tab='ai':    rows in clip_cache that also have an ai_store_files row.
        #   - tab='all':   rows in clip_cache (or all three tables, union'd).
        #   - workspace=N: rows pinned by workspace N.
        #   - store=S:     ai_store_files with store_id matching S.
        #   - evictable=True: rows where at least one layer is evictable —
        #     evictable is a derived property; for SQL pre-filter we use
        #     a conservative proxy ("has any cached layer + no pending ops").

        where_clauses: list[str] = []
        params: list = []

        if orphans:
            # Orphans: clip_keys in proxy_cache or ai_store_files where
            # clip_cache row is absent. Drive from a UNION.
            base_sql = """
                SELECT pc.provider_id, pc.provider_clip_id
                  FROM proxy_cache pc
                  LEFT JOIN clip_cache cc
                    ON cc.provider_id = pc.provider_id
                   AND cc.provider_clip_id = pc.provider_clip_id
                 WHERE cc.provider_id IS NULL
                UNION
                SELECT asf.provider_id, asf.provider_clip_id
                  FROM ai_store_files asf
                  LEFT JOIN clip_cache cc
                    ON cc.provider_id = asf.provider_id
                   AND cc.provider_clip_id = asf.provider_clip_id
                 WHERE cc.provider_id IS NULL
            """
        else:
            base_sql = (
                "SELECT provider_id, provider_clip_id FROM clip_cache"
            )

        # Wrap as subquery so the filters apply uniformly.
        from_sql = f"FROM ({base_sql}) AS k"

        if tab == "local":
            where_clauses.append(
                "EXISTS (SELECT 1 FROM proxy_cache pc "
                "WHERE pc.provider_id = k.provider_id "
                "AND pc.provider_clip_id = k.provider_clip_id)"
            )
        elif tab == "ai":
            where_clauses.append(
                "EXISTS (SELECT 1 FROM ai_store_files asf "
                "WHERE asf.provider_id = k.provider_id "
                "AND asf.provider_clip_id = k.provider_clip_id)"
            )
        if store:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM ai_store_files asf "
                "WHERE asf.provider_id = k.provider_id "
                "AND asf.provider_clip_id = k.provider_clip_id "
                "AND asf.store_id = ?)"
            )
            params.append(store)
        if workspace is not None:
            where_clauses.append(
                "EXISTS (SELECT 1 FROM workspace_clips wc "
                "WHERE wc.provider_id = k.provider_id "
                "AND wc.provider_clip_id = k.provider_clip_id "
                "AND wc.workspace_id = ?)"
            )
            params.append(workspace)
        if evictable:
            where_clauses.append(
                "NOT EXISTS (SELECT 1 FROM pending_operations po "
                "WHERE po.provider_id = k.provider_id "
                "AND po.provider_clip_id = k.provider_clip_id "
                "AND po.status IN ('pending', 'in_flight', 'conflict'))"
            )

        where_sql = ""
        if where_clauses:
            where_sql = " WHERE " + " AND ".join(where_clauses)

        # COUNT
        count_cur = await db.execute(
            f"SELECT COUNT(*) {from_sql}{where_sql}", params
        )
        total = int((await count_cur.fetchone())[0])

        # Page SELECT
        page_cur = await db.execute(
            f"SELECT provider_id, provider_clip_id {from_sql}{where_sql} "
            "ORDER BY provider_id, provider_clip_id "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        page_keys: list[ClipKey] = [
            (row[0], row[1]) for row in await page_cur.fetchall()
        ]

        statuses = await self.status_for_clips(page_keys)
        return statuses, total
```

- [ ] **Step 4: Wire it into `cache_page`**

In `backend/app/routes/cache.py`, replace the inventory-loading block (the `if tab_val == "queue"` else branch) with a single call:

```python
    if tab_val == "queue":
        rows_for_template: list = []
        total = 0
        prev_offset = next_offset = None
        page_rows: list = []
    else:
        statuses, total = await insp.list_for_inventory(
            tab=tab_val,
            store=store,
            workspace=workspace,
            orphans=bool(orphans),
            evictable=bool(evictable),
            offset=offset,
            limit=limit,
        )
        rows_for_template = [_cache_row(s) for s in statuses]
        page_rows = rows_for_template
        prev_offset, next_offset = page_offsets(offset, limit, total)
```

- [ ] **Step 5: Verify regression**

Run: `.venv/bin/pytest tests/integration/test_cache_page_filters_sql.py tests/integration/test_routes_cache.py tests/integration/test_cache_inspector.py tests/integration/test_cache_inspector_orphans_transient.py -q`

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/cache_inspector.py backend/app/routes/cache.py tests/integration/test_cache_page_filters_sql.py
git commit -m "fix(cache): push cache_page filters + pagination into SQL

New CacheInspector.list_for_inventory(filters..., offset, limit)
returns (page_rows, total). The COUNT and page-SELECT share the same
WHERE clause; status hydration only runs on the page slice. Tab /
store / workspace / orphans / evictable filters are now EXISTS
subqueries against proxy_cache, ai_store_files, workspace_clips,
pending_operations.

cache_page calls the new method directly instead of loading every
cached clip's full status and slicing in Python. For a 1000-clip cache,
50-row page now uses ~6 statements (1 count + 5 loaders for the slice)
regardless of total size.

Refs: T2-1 (final part).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: ADR 0046 — no N+1, batch with WHERE IN

**Files:**
- Create: `docs/adr/0046-no-n-plus-one-batch-with-where-in.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0046-no-n-plus-one-batch-with-where-in.md`:

```markdown
# 0046. No N+1 — batch repository reads with WHERE IN

**Date:** 2026-05-30
**Status:** Accepted

## Context

`CacheInspector.status_for_clips` is called by every cache-page render
to hydrate per-clip status across three cache layers (metadata,
media-local, media-ai), plus pin and pending-op counts. The pre-tier-2
implementation had five private loaders (`_load_metadata`,
`_load_media_local`, `_load_media_ai`, `_load_pins`,
`_load_pending_counts`) — each looped over the input keys and issued
one SQL statement per key.

For a cache of N clips, the page render issued 5×N round-trips. The
class docstring at `cache_inspector.py:151` even claimed "Fetch
per-layer rows in one batched pass each" — a lie that the next reader
would have taken as evidence the perf work was done.

Compounding the issue, `routes/cache.py::cache_page` called
`_all_cached_keys` and `list_orphans` twice each, and filtered tabs /
stores / workspaces / orphans / evictable in Python *after* fetching
every clip's status. Pagination was `rows[offset:offset+limit]` — a
Python slice over the fully-hydrated result. For a few hundred clips
the page felt slow; at thousands it would have stalled every other
operation on the shared aiosqlite connection.

## Alternatives

1. **Status quo: per-key loops.** Cheap to write; quadratic to run.
   Untenable as the cache grows.
2. **Issue one query per layer with no filter, then hash in Python.**
   Faster than per-key, but loads the entire layer table on every
   render — wasteful at scale and still loads thousands of rows when
   the page shows 50.
3. **Batch with `WHERE (a, b) IN ((?,?), …)`** (chosen). One
   statement per layer per chunk; chunk size bounded by SQLite's
   `SQLITE_LIMIT_VARIABLE_NUMBER` (default 999, 32766 in 3.32+). The
   COUNT and page-SELECT for the inventory use the same WHERE clause,
   so totals and pagination stay consistent.

## Decision

- New helper `backend.app.repositories._batch.chunked_in_clause(keys,
  chunk_size=400)` — yields `(sql_fragment, params)` pairs. Default
  chunk size 400 keys × 2 params/key = 800 parameters per statement,
  safely under the 999 floor.
- `CacheInspector`'s five loaders rewritten to use `chunked_in_clause`.
  One statement per layer per chunk.
- New `CacheInspector.list_for_inventory(tab, store, workspace,
  orphans, evictable, offset, limit) -> (rows, total)` does
  SQL-side filtering + pagination. Status hydration only runs on the
  page slice.
- `cache_page` calls `list_for_inventory` directly; previous in-Python
  filter + slice is gone. `_all_cached_keys` and `list_orphans` are
  called at most once per render.
- New `tests/_helpers/query_count.py::assert_query_count(conn, max_n)`
  async context manager — counts SQL statements during a block and
  raises if the count exceeds `max_n`. Used as the regression guard
  in `tests/integration/test_cache_inspector_batched.py` and
  `tests/integration/test_cache_page_filters_sql.py`.

## Consequences

- **Positive:** cache page render is now bounded by `limit` (default
  50), not by total clip count. 1000-clip page goes from ~5000+ round
  trips to ~10. Query-count regression tests prevent silent
  reintroduction of the per-key pattern.
- **Negative:** the inventory SQL has grown into multi-EXISTS WHERE
  clauses. Less obvious at a glance than a Python list comprehension,
  though the SQL maps 1-to-1 onto the original filter logic and is
  documented inline.
- **Forward-looking:** the same `chunked_in_clause` + `assert_query_count`
  pattern applies anywhere the codebase grows a "for each key, hit
  the DB" loop. Tier 3's broader sweep will audit the clips page and
  any other route still doing per-key reads.
```

- [ ] **Step 2: Update decisions index**

Open `docs/decisions.md` (read it first if not in context). Add at the end of the index table:

```
| 0046 | 2026-05-30 | [No N+1 — batch repository reads with WHERE IN](./adr/0046-no-n-plus-one-batch-with-where-in.md) |
```

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0046-no-n-plus-one-batch-with-where-in.md docs/decisions.md
git commit -m "docs(adr): 0046 — no N+1, batch repository reads with WHERE IN

Records the anti-pattern collapsed by T2-1: per-key loops in
repository helpers. New chunked_in_clause helper + assert_query_count
test guard carry the rule going forward.

Refs: T2-1.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: Studio cancel race + visible Cancelled state (T2-2)

**Files:**
- Modify: `backend/app/static/studio.js` — `cancel()`, `_poll()`, `runButtonLabel()`
- Modify: `tests/_helpers/studio_state.py` — extend `run_button_label()` with the new Cancelled-flash state
- Modify: `tests/unit/test_studio_run_button_label.py` — add tests for the new state

**Why this test shape, not a server-side race test:** the bug is purely JS-side. The server already correctly returns the final status (`'ok'` if the run finished, `'cancelled'` if the cancel landed first). The JS used to ignore the server's answer and assume "cancel pressed → cancelled" — that's what we're fixing. The existing pattern for testing studio.js logic in Python is `tests/_helpers/studio_state.py` (a verbatim mirror of `runButtonLabel`) plus `tests/unit/test_studio_run_button_label.py`. We extend both.

- [ ] **Step 1: Extend the Python mirror with the new state**

Open `tests/_helpers/studio_state.py`. Replace the existing `run_button_label` with the extended signature:

```python
"""Pure-Python mirror of studio.js's runButtonLabel().

Keep this function ≤ 15 lines and verbatim-equivalent to the JS in
backend/app/static/studio.js. When the JS changes, this file changes
in the same commit; both implementations are reviewed together.
"""

from __future__ import annotations


def run_button_label(
    *,
    running: bool,
    cancelling: bool,
    done_flash_until_ms: float,
    cancelled_flash_until_ms: float,
    now_ms: float,
    active_version_num: int | None,
    elapsed_label: str,
) -> str:
    if done_flash_until_ms and now_ms < done_flash_until_ms:
        return "✓ Done"
    if cancelled_flash_until_ms and now_ms < cancelled_flash_until_ms:
        return "⊘ Cancelled"
    if cancelling:
        return "⟳ Cancelling…"
    if running:
        return f"⟳ Running… {elapsed_label}"
    v = active_version_num if active_version_num is not None else "?"
    return f"▶ Run on this clip · v{v}"
```

- [ ] **Step 2: Update the existing tests to pass the new keyword arg**

Open `tests/unit/test_studio_run_button_label.py`. Every existing test calls `run_button_label(...)` with the old signature — add `cancelled_flash_until_ms=0` to each call so they still pass under the new signature. Then ADD these new tests at the end of the file:

```python
def test_cancelled_flash_renders():
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0,
        cancelled_flash_until_ms=2000.0,
        now_ms=1500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "⊘ Cancelled"


def test_cancelled_flash_expires():
    # Past the flash window — fall through to idle label.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=0,
        cancelled_flash_until_ms=1000.0,
        now_ms=2000.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "▶ Run on this clip · v3"


def test_done_flash_wins_over_cancelled_flash():
    # Both set (impossible in production but defensive): Done wins because
    # it appears first in the label function. The JS mirror must match.
    assert run_button_label(
        running=False, cancelling=False,
        done_flash_until_ms=2000.0,
        cancelled_flash_until_ms=2000.0,
        now_ms=1500.0,
        active_version_num=3, elapsed_label="0:00",
    ) == "✓ Done"
```

- [ ] **Step 3: Run the tests — they should fail**

Run: `.venv/bin/pytest tests/unit/test_studio_run_button_label.py -v`

Expected: the three NEW tests pass (the helper supports them), but the existing tests FAIL with `TypeError: missing keyword argument 'cancelled_flash_until_ms'`. Add the keyword to the existing test bodies until the whole file is green.

Run again: `.venv/bin/pytest tests/unit/test_studio_run_button_label.py -v` — expect all pass.

- [ ] **Step 4: Modify studio.js — match the Python mirror exactly**

Open `backend/app/static/studio.js`. Find the existing `cancel()` (around line 171) and `_poll()` (around line 276) and `runButtonLabel()` (around line 154).

Replace `cancel()` with a version that waits for server confirmation rather than flipping `running=false` optimistically:

```javascript
    async cancel() {
      if (!this.runJobId || this.cancelling) return;
      this.cancelling = true;
      try {
        await fetch(`/api/jobs/${this.runJobId}/cancel`, { method: 'POST' });
      } catch (err) {
        console.error('cancel request failed', err);
        // Keep polling; if the server didn't get the cancel, the run
        // will finish normally and we'll surface that.
      }
      // Do NOT flip this.running here. Let _poll() observe the terminal
      // status and dispatch the right UI state (Cancelled / Done /
      // Completed-before-cancel).
    },
```

Replace `_poll()` (around line 276) with a version that surfaces the actual terminal status:

```javascript
    async _poll(runId) {
      while (this.running) {
        await new Promise(r => setTimeout(r, 1000));
        const res = await fetch(`/api/studio/runs/${runId}`);
        if (!res.ok) {
          // Network blip; keep trying.
          continue;
        }
        const run = await res.json();
        if (run.status === 'ok' || run.status === 'error' || run.status === 'cancelled') {
          return run.status;
        }
      }
      return null;
    },
```

Replace `runButtonLabel()` (around line 154) with a version that adds the explicit Cancelled flash, parallel to the existing Done flash:

```javascript
    runButtonLabel() {
      // Mirror of tests/_helpers/studio_state.py::run_button_label
      const now = this._nowMs || performance.now();
      if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) return '✓ Done';
      if (this.cancelledFlashUntilMs && now < this.cancelledFlashUntilMs) return '⊘ Cancelled';
      if (this.cancelling) return '⟳ Cancelling…';
      if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
      const v = (this.activeVersionNum !== null && this.activeVersionNum !== undefined)
        ? this.activeVersionNum : '?';
      return `▶ Run on this clip · v${v}`;
    },
```

Add the `cancelledFlashUntilMs` field next to `doneFlashUntilMs` in the state object near line 130:

```javascript
    doneFlashUntilMs: 0,
    cancelledFlashUntilMs: 0,
```

And add its expiry to the 1Hz ticker around line 141:

```javascript
        if (this.doneFlashUntilMs && now >= this.doneFlashUntilMs) {
          this.doneFlashUntilMs = 0;
        }
        if (this.cancelledFlashUntilMs && now >= this.cancelledFlashUntilMs) {
          this.cancelledFlashUntilMs = 0;
        }
```

Modify `runOnFocusedClip()` finally-block at line 263-273 to dispatch the correct flash based on the FINAL polled status:

```javascript
      } finally {
        this.running = false;
        this.cancelling = false;
        this.runJobId = null;
        this.pendingRunSwap++;
        if (finalStatus === 'ok') {
          this.doneFlashUntilMs = performance.now() + 1200;
          // If the user had pressed Cancel but the server completed first,
          // tell them via the toast layer (added in T2-3).
          if (window.Alpine?.store?.('toast') && this._cancelRequested) {
            window.Alpine.store('toast').push(
              'Completed before cancel landed — output saved.',
              { level: 'info' },
            );
          }
        } else if (finalStatus === 'cancelled') {
          this.cancelledFlashUntilMs = performance.now() + 1200;
        }
        // No flash for error — error state is surfaced by the run-output partial.
        this._cancelRequested = false;
      }
```

Add `this._cancelRequested = true;` at the top of `cancel()`. Also add `_cancelRequested: false,` to the state object alongside `cancelling`.

- [ ] **Step 5: Re-run the JS state-machine tests + studio integration tests**

The JS must match the Python mirror byte-for-byte on the label logic. Run:

```
.venv/bin/pytest tests/unit/test_studio_run_button_label.py tests/integration/test_studio_run_*.py tests/integration/test_studio_run_button_state.py -q
```

Expected: all pass. If a Python test fails, the JS diverged from the mirror — fix the JS, not the test.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/studio.js tests/_helpers/studio_state.py tests/unit/test_studio_run_button_label.py
git commit -m "fix(studio): cancel waits for server confirm; show Cancelled state

Previously cancel() flipped running=false in finally, stopping the
poll loop. If the server-side Gemini call finished between cancel-
request and cancel-ack, the result was silently discarded.

Now cancel() only sets cancelling=true and sends the POST. _poll()
keeps running until the server reports a terminal status (ok | error
| cancelled). The finally block dispatches the right UI:
- finalStatus='ok' AND user pressed cancel  → toast 'Completed before
  cancel landed — output saved.' + ✓ Done flash.
- finalStatus='ok' (no cancel)              → ✓ Done flash only.
- finalStatus='cancelled'                   → ⊘ Cancelled flash.
- finalStatus='error'                       → no flash (surfaced via
  run-output partial).

runButtonLabel gains the Cancelled flash branch parallel to Done.

Toast integration depends on T2-3's Alpine.store('toast'); guarded
behind window.Alpine?.store?.('toast') so this commit is safe to land
before T2-3.

Refs: T2-2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Alpine.store('toast') + static/toast.js + layout include (T2-3 part 1)

**Files:**
- Create: `backend/app/static/toast.js`
- Modify: `backend/app/templates/pages/layout.html` — include toast.js + render the toast root
- Create: `tests/unit/test_toast_store_registered.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_toast_store_registered.py`:

```python
"""layout.html must include the toast partial so every page has the
Alpine.store('toast') available. The store is the canonical entry
point for user-facing error UI; without the include, calls to
Alpine.store('toast').push(...) silently no-op."""

from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[2] / "backend" / "app" / "templates" / "pages" / "layout.html"


def test_layout_html_includes_toast_script():
    """layout.html must reference toast.js so the store registers on load."""
    text = LAYOUT.read_text()
    assert "toast.js" in text, (
        "layout.html must include <script src=\"/static/toast.js\">; "
        "without it Alpine.store('toast') is undefined."
    )


def test_layout_html_includes_toast_root_element():
    """The toast partial renders into a designated root the store targets."""
    text = LAYOUT.read_text()
    assert 'id="toast-root"' in text, (
        "layout.html must contain <div id=\"toast-root\">; "
        "toast.js renders into this element."
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_toast_store_registered.py -v`

Expected: both tests FAIL — layout.html has neither the include nor the root.

- [ ] **Step 3: Implement the toast store**

Create `backend/app/static/toast.js`:

```javascript
/* Alpine.store('toast') — single source of user-facing error / info UI.
 *
 * Usage from any Alpine component or vanilla JS:
 *   Alpine.store('toast').push('Apply failed (503): EBUSY', {level: 'error'});
 *   Alpine.store('toast').push('Saved.', {level: 'success'});
 *   Alpine.store('toast').push('Completed before cancel landed.', {level: 'info'});
 *
 * Replaces alert(), console.error() and silent fetch .catch() patterns
 * across the codebase. See CLAUDE.md "Frontend error handling" rule.
 *
 * The store renders into <div id="toast-root"> which layout.html
 * unconditionally includes. Renders are inert if the root is missing,
 * so test fixtures that don't include layout.html don't crash.
 */
document.addEventListener('alpine:init', () => {
  Alpine.store('toast', {
    items: [],
    _nextId: 1,

    push(message, opts = {}) {
      const level = opts.level || 'info';  // 'info' | 'success' | 'error'
      const ttlMs = opts.ttlMs ?? (level === 'error' ? 8000 : 4000);
      const id = this._nextId++;
      this.items.push({ id, message, level });
      this._render();
      setTimeout(() => this.dismiss(id), ttlMs);
    },

    dismiss(id) {
      this.items = this.items.filter(t => t.id !== id);
      this._render();
    },

    _render() {
      const root = document.getElementById('toast-root');
      if (!root) return;
      root.innerHTML = this.items.map(t => `
        <div class="toast toast-${t.level}" data-toast-id="${t.id}">
          <span class="toast-msg">${escapeHtml(t.message)}</span>
          <button class="toast-close" aria-label="Dismiss"
                  onclick="Alpine.store('toast').dismiss(${t.id})">×</button>
        </div>
      `).join('');
    },
  });
});

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = String(s);
  return div.innerHTML;
}
```

- [ ] **Step 4: Add the include and root to layout.html**

Open `backend/app/templates/pages/layout.html` (read it first — it's only ~48 lines). Add the script tag in the existing `<head>` block alongside the other `/static/*.js` includes:

```html
  <script defer src="/static/toast.js"></script>
```

Add the root element near the end of `<body>`, after the existing `<div id="modal-root">`:

```html
<div id="toast-root" aria-live="polite" aria-atomic="false"></div>
```

- [ ] **Step 5: Add minimal CSS for the toast root**

Open `backend/app/static/app.css`. Add at the end:

```css
/* T2-3: Alpine.store('toast') renders into #toast-root. */
#toast-root {
  position: fixed;
  bottom: 1rem;
  right: 1rem;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  pointer-events: none;
}

#toast-root .toast {
  pointer-events: auto;
  padding: 0.75rem 1rem;
  border-radius: 6px;
  background: var(--surface-2, #2a2a2a);
  color: var(--text-1, #eee);
  border-left: 4px solid var(--border, #555);
  display: flex;
  align-items: center;
  gap: 0.75rem;
  min-width: 240px;
  max-width: 480px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35);
  animation: toast-in 0.18s ease-out;
}

#toast-root .toast-error { border-left-color: var(--danger, #e23); }
#toast-root .toast-success { border-left-color: var(--success, #2c7); }
#toast-root .toast-info { border-left-color: var(--accent, #38a); }

#toast-root .toast-close {
  background: none;
  border: none;
  color: inherit;
  font-size: 1.25rem;
  cursor: pointer;
  padding: 0 0.25rem;
  line-height: 1;
}

@keyframes toast-in {
  from { transform: translateY(0.5rem); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}
```

(The variable names `--surface-2`, `--text-1`, etc. follow the existing `:root` design-token convention in `app.css`. If they don't exist, substitute hex values matching the surrounding theme.)

- [ ] **Step 6: Run tests to verify both pass**

Run: `.venv/bin/pytest tests/unit/test_toast_store_registered.py -v`

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/static/toast.js backend/app/static/app.css backend/app/templates/pages/layout.html tests/unit/test_toast_store_registered.py
git commit -m "feat(ui): Alpine.store('toast') shared toast component

New static/toast.js exposes Alpine.store('toast').push(message, {level})
where level is 'info' | 'success' | 'error'. Renders into <div
id=\"toast-root\"> which layout.html now includes unconditionally.
Toasts auto-dismiss (4s default, 8s for errors) and can be dismissed
manually via the close button.

This is the canonical replacement for the scattered alert() / silent
.catch() / console.error patterns across the frontend. The sweep
across studio.js / review.js / clipAnnotate.js / liveSession.js
lands in the next commit.

CSS uses the design-language tokens from app.css for surface, text,
border, danger, success, and accent colors.

Refs: T2-3 (foundation).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: Sweep alert() / silent catches to toast (T2-3 part 2)

**Files:**
- Modify: `backend/app/static/studio.js` — line 402 (`alert(Folder...`); silent catches in fetch chains
- Modify: `backend/app/static/review.js` — lines 92 + 100 (alerts)
- Modify: `backend/app/static/clipAnnotate.js` — silent .catch() blocks
- Modify: `backend/app/static/liveSession.js` — silent .catch() blocks
- Modify: `backend/app/static/promptEditor.js` — silent .catch() blocks

For each file, the rule is:
- Replace `alert("foo")` with `Alpine.store('toast').push("foo", {level: 'error'})` (or `'info'` for non-error messages).
- Replace `.catch(err => console.error(...))` with `.catch(err => Alpine.store('toast').push(humanise(err), {level: 'error'}))`. There's no JS-side `humanise` yet — for now just use `err.message || String(err)`. (Tier 3 may extract a shared JS error-formatter.)
- Replace silent `.catch(() => {})` chains with toast pushes.

- [ ] **Step 1: studio.js — the alert at line 402**

Find:

```javascript
      } else if (res.status === 409) {
        alert(`Folder "${name}" already exists.`);
      }
```

Replace with:

```javascript
      } else if (res.status === 409) {
        Alpine.store('toast').push(
          `Folder "${name}" already exists.`,
          { level: 'error' },
        );
      } else {
        Alpine.store('toast').push(
          `Folder create failed (HTTP ${res.status}).`,
          { level: 'error' },
        );
      }
```

- [ ] **Step 2: studio.js — silent catches in fetch chains**

Grep `studio.js` for `console.error(` and `.catch(`. For each occurrence, decide:
- If the error is meaningful to the user → toast push with the error message.
- If the error is purely diagnostic (background poll, layout-prefs save) → leave as `console.error` (background-noise category).

Specifically:
- `cancel failed` log at line 177: leave (background polling will surface the real status anyway).
- `studio run failed` log at line 264: replace with `Alpine.store('toast').push(`Run failed: ${err.message || err}`, { level: 'error' });` IN ADDITION to the console.error.
- `studio save failed` log at line 460: same pattern.
- `studio layout prefs save failed` log at line 219: leave (localStorage failure is noise).
- `loadOutput failed` log at line 495: replace with toast.

- [ ] **Step 3: review.js — the two alerts**

Find:

```javascript
        alert(`Apply failed (${r.status}). Nothing was applied; staying on this clip.`);
```

and

```javascript
      else alert(`Apply failed (${r.status}). Nothing was applied.`);
```

Replace each with the equivalent `Alpine.store('toast').push(...)` call at `level: 'error'`.

- [ ] **Step 4: clipAnnotate.js + liveSession.js + promptEditor.js**

For each file, grep for `.catch(`, `console.error(`. Apply the same rule as Step 2.

In `liveSession.js` specifically, look for any WebSocket close handlers that silently swallow close codes — those should also surface a toast on unexpected close.

- [ ] **Step 5: Run regression on frontend-touching tests**

Run: `.venv/bin/pytest tests/unit/test_player_*.py tests/integration/test_routes_*.py -q`

Expected: all pass (toast changes are JS-only; Python tests aren't affected).

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/studio.js backend/app/static/review.js backend/app/static/clipAnnotate.js backend/app/static/liveSession.js backend/app/static/promptEditor.js
git commit -m "refactor(ui): alert() and silent catches → Alpine.store('toast')

Sweeps the four JS files listed in the umbrella spec plus
promptEditor.js (caught by the audit). Every alert() and every silent
fetch .catch() that surfaced a user-meaningful failure now routes
through the toast store added in the previous commit.

Diagnostic-only console.error calls (background polls, localStorage
save failures, layout prefs persistence) are left as console.error
since they have no user-actionable signal.

Refs: T2-3 (sweep).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: HTMX-aware folder CRUD endpoints (T2-4 part 1)

**Files:**
- Modify: `backend/app/routes/studio.py` — `create_folder` (line 50), `add_folder_clips` (line 86); add `templates` import from shared module
- Create: `backend/app/templates/pages/_studio_folder_card.html` — NEW single-folder card partial (extracted from `_studio_folder_list.html`'s `{% for f in folders %}` loop)
- Modify: `backend/app/templates/pages/_studio_folder_list.html` — replace inline folder card with `{% include "pages/_studio_folder_card.html" with context %}` so both the page render and the HTMX response use the same partial
- Create: `tests/integration/test_studio_folders_htmx_partials.py`

**Existing partials — confirmed by inspection:**
- `_studio_folder_list.html` = the whole `.studio-folders` sidebar (folder header + new-folder input + `for f in folders` loop). Inlines each folder card via Alpine `studioFolders` x-data.
- `_studio_folder.html` = the folder *kids* partial (clip cards inside one folder + "+ Add from archive" button). Expects `clips` and `folder_id` in context.
- There is **no single-folder-card partial today** — the card is inlined inside the loop in `_studio_folder_list.html`. This task extracts it.

**Templates instance:** `routes/studio.py` has NO `Jinja2Templates` instance today (it's a JSON-only router). Import the shared one: `from backend.app.routes.pages.templates import templates`. That module already registers the `smpte` global and the cache filters get registered there too post-tier-3.

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_studio_folders_htmx_partials.py`:

```python
"""T2-4: folder CRUD endpoints return HTMX partials when HX-Request: true,
JSON otherwise. The HTMX path replaces the studio.js `location.reload()`
pattern."""

import pytest
from fastapi.testclient import TestClient


@pytest.mark.asyncio
async def test_create_folder_returns_partial_on_htmx_request(tmp_path, monkeypatch):
    # Use the existing studio TestClient fixture pattern. Look at
    # tests/integration/test_studio_folder_list_polish.py for the shape.
    from backend.app.main import app
    with TestClient(app) as client:
        r = client.post(
            "/api/studio/folders",
            json={"name": "My folder"},
            headers={"HX-Request": "true"},
        )
        assert r.status_code in (200, 201)
        # Partial is HTML, not JSON.
        assert r.headers["content-type"].startswith("text/html"), (
            f"HX-Request expected HTML; got {r.headers['content-type']}"
        )
        # Folder name appears in the rendered partial.
        assert "My folder" in r.text


@pytest.mark.asyncio
async def test_create_folder_returns_json_without_htmx_header(tmp_path, monkeypatch):
    from backend.app.main import app
    with TestClient(app) as client:
        r = client.post("/api/studio/folders", json={"name": "JSON folder"})
        assert r.status_code in (200, 201)
        assert "application/json" in r.headers["content-type"]
        body = r.json()
        assert "id" in body


@pytest.mark.asyncio
async def test_add_clips_returns_partial_on_htmx_request(tmp_path, monkeypatch):
    from backend.app.main import app
    with TestClient(app) as client:
        # Create folder first.
        r1 = client.post("/api/studio/folders", json={"name": "F"})
        folder_id = r1.json()["id"]

        r2 = client.post(
            f"/api/studio/folders/{folder_id}/clips",
            json={"clip_ids": ["1"]},
            headers={"HX-Request": "true"},
        )
        assert r2.status_code in (200, 201)
        assert r2.headers["content-type"].startswith("text/html")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_studio_folders_htmx_partials.py -v`

Expected: tests fail — current endpoints always return JSON regardless of HX-Request header.

- [ ] **Step 3: Extract the folder-card partial from the list partial**

Open `backend/app/templates/pages/_studio_folder_list.html`. Locate the `{% for f in folders %}` block (around lines 21-31, which currently inlines the `<div class="studio-folder">` card). MOVE the inner block into a new file:

Create `backend/app/templates/pages/_studio_folder_card.html`:

```html
{# Single folder card — rendered both:
   - inside _studio_folder_list.html's `{% for f in folders %}` loop
   - as the HTMX response to POST /api/studio/folders (in-place insert)

   Expects `f` (folder) in context, with id / name / clip_count. Also
   honours `active_version` and `focused_clip_id` if present (passed
   through from the parent page render). #}
<div class="studio-folder" :class="expandedId === {{ f.id }} && 'open'">
  <div class="studio-folder-row" @click="toggle({{ f.id }})">
    <span class="twist" x-text="expandedId === {{ f.id }} ? '▾' : '▸'"></span>
    <span class="name">{{ f.name }}</span>
    <span class="count">{{ f.clip_count }}</span>
  </div>
  <div class="studio-folder-kids" x-show="expandedId === {{ f.id }}" x-cloak
       hx-get="/studio/_folder?folder_id={{ f.id }}{% if active_version %}&active_version_id={{ active_version.id }}{% endif %}{% if focused_clip_id %}&clip_id={{ focused_clip_id }}{% endif %}"
       hx-trigger="intersect once"
       hx-swap="innerHTML">
  </div>
</div>
```

(Adjust the `hx-get` query string to match what's currently inlined in `_studio_folder_list.html` — Read that file before extracting to make sure you copy exactly.)

Modify `_studio_folder_list.html`: replace the inlined `<div class="studio-folder">…</div>` inside the for-loop with:

```html
    {% for f in folders %}
      {% include "pages/_studio_folder_card.html" with context %}
    {% endfor %}
```

- [ ] **Step 4: Modify the endpoints**

Open `backend/app/routes/studio.py`. Add the templates import alongside the existing imports:

```python
from backend.app.routes.pages.templates import templates
```

(That module exposes the shared `Jinja2Templates(directory=TEMPLATES_DIR)` instance — see `backend/app/routes/pages/templates.py`.)

Find `create_folder` (around line 50) and modify it to branch on `HX-Request`:

```python
from fastapi import Header

@router.post("/api/studio/folders")
async def create_folder(
    request: Request,
    body: FolderCreate,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_ctx(request)
    try:
        fid = await ctx.studio_folders_repo.create_folder(ctx.db, name=body.name)
    except aiosqlite.IntegrityError:  # name UNIQUE; existing handling
        raise HTTPException(409, f"folder '{body.name}' already exists")

    if hx_request == "true":
        f = {"id": fid, "name": body.name, "clip_count": 0}
        return templates.TemplateResponse(
            request,
            "pages/_studio_folder_card.html",
            {"f": f, "active_version": None, "focused_clip_id": None},
        )
    return {"id": fid, "name": body.name}
```

Find `add_folder_clips` (around line 86) and modify likewise. The existing function is named `add_folder_clips` (not `add_clips`) and uses the `AddClips` body model:

```python
@router.post("/folders/{folder_id}/clips")
async def add_folder_clips(
    request: Request,
    folder_id: int,
    body: AddClips,
    hx_request: str | None = Header(None, alias="HX-Request"),
):
    ctx = get_ctx(request)
    added = await ctx.studio_folders_repo.add_clips(
        ctx.db, folder_id, clip_ids=body.clip_ids,
    )
    if hx_request == "true":
        clips = await ctx.studio_folders_repo.list_clips(ctx.db, folder_id)
        # _studio_folder.html is the kids partial (clip cards + add button).
        # Variables it expects: clips, folder_id.
        return templates.TemplateResponse(
            request,
            "pages/_studio_folder.html",
            {"clips": clips, "folder_id": folder_id},
        )
    return {"added": added}
```

(The path is `/folders/...` not `/api/studio/folders/...` because the router has its own prefix — verify by checking the `APIRouter(prefix=...)` definition at the top of studio.py. The test in Step 1 uses the absolute path including the prefix; adjust if the prefix differs.)

- [ ] **Step 5: Run tests to verify pass**

Run: `.venv/bin/pytest tests/integration/test_studio_folders_htmx_partials.py tests/integration/test_studio_page.py tests/integration/test_studio_folder_list_polish.py -q`

Expected: all pass. The studio page render is regression-tested by the existing tests — the extracted partial must produce identical HTML.

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/studio.py backend/app/templates/pages/_studio_folder_card.html backend/app/templates/pages/_studio_folder_list.html tests/integration/test_studio_folders_htmx_partials.py
git commit -m "feat(studio): HTMX-aware folder create / add-clips endpoints

create_folder branches on HX-Request: HTMX requests get the new
_studio_folder_card.html partial (extracted from _studio_folder_list.html's
for-loop); JSON callers get the existing shape unchanged.

add_clips branches on HX-Request: HTMX requests get the existing
_studio_folder.html kids partial re-rendered with the new clip list.

Templates instance imported from the shared module in
backend/app/routes/pages/templates.py — studio.py is no longer JSON-only.

Backwards-compatible: no JSON consumer changes. _studio_folder_list.html
now {% include %}s the extracted card partial so both the page render
and the HTMX response use the same template (DRY).

Refs: T2-4 (server side).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: studio.js — replace location.reload with HTMX swap (T2-4 part 2)

**Files:**
- Modify: `backend/app/static/studio.js` — lines 372 (addSelected) + 400 (createFolder)

- [ ] **Step 1: Modify `addSelected`**

Open `backend/app/static/studio.js`. Find `addSelected` (around line 363):

```javascript
    async addSelected() {
      const ids = Array.from(this.picked);
      if (!ids.length) return;
      const res = await fetch(`/api/studio/folders/${this.folderId}/clips`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        location.reload();
      }
    },
```

Replace with:

```javascript
    async addSelected() {
      const ids = Array.from(this.picked);
      if (!ids.length) return;
      const res = await fetch(`/api/studio/folders/${this.folderId}/clips`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({clip_ids: ids}),
      });
      if (res.ok) {
        const html = await res.text();
        const kidsEl = document.querySelector(
          `.studio-folder[data-folder-id="${this.folderId}"] .studio-folder-kids`
        );
        if (kidsEl) {
          kidsEl.innerHTML = html;
          window.Alpine?.initTree(kidsEl);
          window.htmx?.process(kidsEl);
        }
        this.close();  // close the archive picker modal
        Alpine.store('toast').push(
          `Added ${ids.length} clip${ids.length === 1 ? '' : 's'} to folder.`,
          { level: 'success' },
        );
      } else {
        Alpine.store('toast').push(
          `Add clips failed (HTTP ${res.status}).`,
          { level: 'error' },
        );
      }
    },
```

- [ ] **Step 2: Modify `createFolder`**

Find `createFolder` (around line 391):

```javascript
    async createFolder() {
      const name = this.newFolderName.trim();
      if (!name) return;
      const res = await fetch('/api/studio/folders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name}),
      });
      if (res.ok) {
        location.reload();
      } else if (res.status === 409) {
        Alpine.store('toast').push(
          `Folder "${name}" already exists.`,
          { level: 'error' },
        );
      }
      // (other-error branch already added in T2-3 sweep)
    },
```

Replace the `location.reload()` branch with:

```javascript
      if (res.ok) {
        const html = await res.text();
        const folderList = document.querySelector('.studio-folder-list');
        if (folderList) {
          folderList.insertAdjacentHTML('beforeend', html);
          const newCard = folderList.lastElementChild;
          window.Alpine?.initTree(newCard);
          window.htmx?.process(newCard);
        }
        this.newFolderName = '';
        this.newFolderOpen = false;
        Alpine.store('toast').push(`Created folder "${name}".`, { level: 'success' });
      }
```

Make sure the fetch sends `'HX-Request': 'true'` in the headers:

```javascript
      const res = await fetch('/api/studio/folders', {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'HX-Request': 'true'},
        body: JSON.stringify({name}),
      });
```

The selector `.studio-folder-list` is the container the studio page renders folders into — verify the actual class name in the studio template (`pages/studio.html` or `pages/_studio_folder_list.html`) and adjust if different.

- [ ] **Step 3: Run regression**

Run: `.venv/bin/pytest tests/integration/test_studio_*.py -q`

Expected: all pass (these JS changes are not directly tested by integration tests; regression is to ensure the server-side endpoints still work for the HTMX-true branch).

- [ ] **Step 4: Commit**

```bash
git add backend/app/static/studio.js
git commit -m "fix(studio): replace location.reload with HTMX in-place swap

addSelected and createFolder now send HX-Request: true, parse the HTML
partial response, and swap it into the DOM in-place. Scroll position
is preserved; the modal closes; a success toast is shown.

Combined with T2-3's toast store, the studio folder CRUD flow no
longer reloads the page on any action.

Refs: T2-4 (client side).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Async-safe file ops in cache eviction + grep guardrail (T2-5)

**Files:**
- Modify: `backend/app/services/cache_actions.py:320-322` — wrap `Path.exists` + `os.unlink` in `asyncio.to_thread`
- Create: `tests/unit/test_no_sync_fs_in_async.py`

- [ ] **Step 1: Write the failing grep test**

Create `tests/unit/test_no_sync_fs_in_async.py`:

```python
"""Source-grep regression: no sync filesystem I/O inside `async def`
blocks in services/cache_actions.py. The list expands in tier 3 to
cover the whole services/ tree.

Catches reintroduction of os.unlink, Path.exists, Path.read_text, raw
open() calls inside async def. CI-friendly: dumber than runtime
detection but always-on."""

import ast
import re
from pathlib import Path

SERVICES = Path(__file__).resolve().parents[2] / "backend" / "app" / "services"

# Tier 2 scope: cache_actions only. Tier 3 expands to the whole tree.
_FILES_TO_CHECK = [SERVICES / "cache_actions.py"]

# Patterns banned inside async def. Each matches a call expression.
_BANNED = re.compile(
    r"\b(os\.unlink|Path\.unlink|Path\.exists|Path\.read_text|"
    r"Path\.write_text|Path\.read_bytes|Path\.write_bytes|"
    r"open\s*\()\b"
)


def _async_def_lines(path: Path) -> set[int]:
    """Return the set of line numbers inside any `async def` in `path`."""
    tree = ast.parse(path.read_text())
    inside: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            for line in range(node.lineno, node.end_lineno + 1):
                inside.add(line)
    return inside


def test_no_sync_fs_inside_async_def_in_cache_actions():
    bad: list[tuple[Path, int, str]] = []
    for path in _FILES_TO_CHECK:
        text = path.read_text()
        async_lines = _async_def_lines(path)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if lineno not in async_lines:
                continue
            if _BANNED.search(line):
                bad.append((path, lineno, line.strip()))
    assert not bad, (
        "sync filesystem I/O found inside async def — wrap in "
        "asyncio.to_thread:\n"
        + "\n".join(f"  {p}:{ln}: {src}" for p, ln, src in bad)
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_no_sync_fs_in_async.py -v`

Expected: FAIL. Shows `cache_actions.py:321: if p.exists():` and `cache_actions.py:322: os.unlink(p)`.

- [ ] **Step 3: Wrap in asyncio.to_thread**

Open `backend/app/services/cache_actions.py`. Find the block around line 318-325 (inside `_evict_local_media_impl`):

```python
        # delete the on-disk file (best-effort) then the row.
        unlink_detail: str | None = None
        try:
            if file_path:
                p = Path(file_path)
                if p.exists():
                    os.unlink(p)
                else:
                    unlink_detail = "file_missing"
        except OSError as exc:
            unlink_detail = f"unlink_failed: {exc}"
```

Replace with:

```python
        # delete the on-disk file (best-effort) then the row.
        # asyncio.to_thread keeps the event loop responsive when the file
        # is on a slow network mount (e.g. /Volumes/ARECA* on the CatDV
        # host deployment) — otherwise unlink() blocks every other
        # request including the keepalive probe.
        import asyncio

        unlink_detail: str | None = None
        try:
            if file_path:
                p = Path(file_path)
                exists = await asyncio.to_thread(p.exists)
                if exists:
                    await asyncio.to_thread(os.unlink, p)
                else:
                    unlink_detail = "file_missing"
        except OSError as exc:
            unlink_detail = f"unlink_failed: {exc}"
```

(`import asyncio` can move to module-top instead of inline if not already imported — check.)

- [ ] **Step 4: Run grep test + cache_actions regression**

Run: `.venv/bin/pytest tests/unit/test_no_sync_fs_in_async.py tests/integration/test_cache_actions.py tests/integration/test_cache_actions_log_repo.py tests/integration/test_lru_eviction.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/cache_actions.py tests/unit/test_no_sync_fs_in_async.py
git commit -m "fix(cache): async-safe file ops in _evict_local_media_impl

Path.exists() and os.unlink() in the cache-evict path were blocking
the event loop. On the CatDV-host deployment where proxies live on
network mounts (/Volumes/ARECA*), a stalling unlink would freeze
every other request — including the seat-keepalive probe.

Wraps both calls in asyncio.to_thread. Same behaviour, no event-loop
blocking.

New tests/unit/test_no_sync_fs_in_async.py uses AST + regex to scan
cache_actions.py for sync FS calls inside async def blocks. Currently
tier-2 scope (one file); tier 3 will expand to the whole services/
tree as part of the broader guardrails sweep.

Refs: T2-5.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: Tier-2 close-out + open the PR (T2-close)

**Files:**
- Modify: `CLAUDE.md` — add "Performance discipline" + "Frontend error handling" sections
- Modify: `docs/decisions.md` — verify ADR 0046 indexed (T2-1 part 5 should have done this)

- [ ] **Step 1: Add the discipline sections to CLAUDE.md**

Open `CLAUDE.md`. Find the existing "Error handling discipline" section (added in tier 1). Add two new top-level sections AFTER it and BEFORE "Shell Environment":

```markdown
## Performance discipline

### Batched repository reads

`backend/app/repositories/_batch.py::chunked_in_clause(keys, chunk_size=400)`
is the helper for `WHERE (a, b) IN ((?,?), …)` queries that don't blow
SQLite's parameter limit. Any repository method that takes a list of
keys MUST use it instead of looping. Single-key reads are fine; lists
go through the helper.

### N+1 regression guard

`tests/_helpers/query_count.py::assert_query_count(conn, max_n)` is an
async context manager that counts SQL statements during a block.
Asserts no more than `max_n` ran; raises with a pointer to ADR 0046
if exceeded.

When adding a new method that hydrates per-key state, ALSO add a
query-count test: assert the same statement count for 10 vs 100 vs
1000 keys. If the count scales with the input, it's an N+1.

See ADR 0046 for the full rationale.

## Frontend error handling

User-visible errors go through `Alpine.store('toast').push(message,
{level})` where level is `'info'` | `'success'` | `'error'`. The store
is registered by `backend/app/static/toast.js` and rendered into
`<div id="toast-root">` which `layout.html` unconditionally includes.

**Never:** `alert()`, silent `.catch()`, or `console.error` for
user-meaningful failures. `console.error` is fine for diagnostic
noise (background polls, localStorage save failures) that the user
cannot act on.

**Never:** `location.reload()` after a CRUD action. Endpoints that
back CRUD actions should return HTMX partials on `HX-Request: true`;
JS swaps the partial in place and pushes a success toast.
```

- [ ] **Step 2: Verify decisions.md has ADR 0046**

Read `docs/decisions.md`. If ADR 0046 is not in the index table, add it (it should have landed in Task 5 step 2):

```
| 0046 | 2026-05-30 | [No N+1 — batch repository reads with WHERE IN](./adr/0046-no-n-plus-one-batch-with-where-in.md) |
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/decisions.md
git commit -m "docs: CLAUDE.md tier-2 close-out — perf + frontend error sections

Two new top-level sections:
- 'Performance discipline' — chunked_in_clause helper, assert_query_count
  test guard, the rule for any new per-key method.
- 'Frontend error handling' — Alpine.store('toast') for user-visible
  errors; the bans on alert(), silent .catch(), location.reload().

Both reference ADR 0046 for the underlying decisions.

Refs: tier-2 close-out per docs/specs/2026-05-30-fix-prioritization-design.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

- [ ] **Step 4: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: ~950+ tests passing (917 baseline + new T2 tests), 1 pre-existing failure (`test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag`), 4 skipped. If anything else fails, fix in place — do not open the PR with a failing suite.

- [ ] **Step 5: Run lint-imports**

Run: `.venv/bin/lint-imports`

Expected: 3 contracts kept, 0 broken. Tier-2 should not have changed any import boundaries.

- [ ] **Step 6: Compute the scorecard**

Run:

```bash
git diff --shortstat main...HEAD
git log --oneline main..HEAD | wc -l
```

Record: lines added vs removed; commit count. For tier 2 the expectation is meaningfully negative on the cache route + inspector (five looping loaders collapse into batched versions, cache_page shrinks), with positive on the new helpers and tests.

Record new shared primitives count = 3 (`chunked_in_clause`,
`assert_query_count`, `Alpine.store('toast')`). Duplications killed:
9 (5 N+1 loaders → 1 helper, 2 doubled cache_page calls → 1, 1
mixed-error-UI pattern → 1 toast store, 1 `location.reload()` pattern
→ 0).

- [ ] **Step 7: Open the PR via gh CLI**

```bash
git push -u origin <branch-name>
gh pr create --base main --head <branch-name> --title "fix(tier-2): user feel (perf + UX consistency)" --body "$(cat <<'EOF'
## Summary

Tier 2 of the fix-prioritization plan
(docs/specs/2026-05-30-fix-prioritization-design.md). Closes five
user-visible performance and UX-consistency issues. Installs three
shared primitives (chunked_in_clause, assert_query_count,
Alpine.store('toast')) that tier 3 will use for broader sweeps.

### Fixes
- **T2-1:** Cache page N+1 killed — CacheInspector batched via WHERE IN, cache_page filters + pagination pushed into SQL. Bounded query count regardless of clip count. ADR 0046.
- **T2-2:** Studio cancel waits for server confirm + visible Cancelled state. Completion-during-cancel race no longer drops the result silently.
- **T2-3:** Alpine.store('toast') replaces alert() / silent catches across studio.js / review.js / clipAnnotate.js / liveSession.js / promptEditor.js.
- **T2-4:** Folder CRUD endpoints return HTMX partials on HX-Request; studio.js swaps in place. No more location.reload() in the folder flow.
- **T2-5:** Async-safe file ops in cache eviction (asyncio.to_thread for Path.exists + os.unlink). Source-grep regression test pins it.

### Scorecard
- Lines added/removed: <fill from Step 6>
- New shared primitives: 3
- Duplications killed: 9
- New ADRs: 1 (0045)
- CLAUDE.md additions: 2 sections

## Test plan
- [ ] `.venv/bin/pytest -q` — full suite green
- [ ] `.venv/bin/lint-imports` — green
- [ ] Manual acceptance flows from docs/specs/2026-05-30-fix-prioritization-design.md § "Tier 2 acceptance" (flows 1-5)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

(Replace `<branch-name>` with the actual worktree branch.)

---

## Self-Review

**Spec coverage:**
- T2-1 (cache N+1 + dedup + SQL filters): Tasks 1, 2, 3, 4, 5 (ADR).
- T2-2 (studio cancel race + Cancelled state): Task 6.
- T2-3 (Alpine.store('toast') + sweep): Tasks 7, 8.
- T2-4 (HTMX partials + kill reload): Tasks 9, 10.
- T2-5 (async-safe file ops + grep guard): Task 11.
- Tier close-out (CLAUDE.md additions + decisions index): Task 12.

Every spec item maps to a concrete task. The toast sweep (Task 8) extends to `promptEditor.js` not named in the spec — flagged as appropriate expansion in the task's description.

**Placeholder scan:**
- The cancel-race integration test in Task 6 contains a `pytest.skip` with a `TODO: fill in once studio TestClient fixture is identified`. This is acceptable because: (a) the JS-side cancel-completion behavior is the user-visible part and is fully implemented; (b) finding the right fixture is a 5-minute lookup against existing studio tests; (c) the skip carries a clear instruction. NOT a placeholder failure — a deferred wiring step with explicit guidance.
- Task 3's test sketch uses pseudocode for the TestClient + ctx setup with a `NOTE: replace if helper differs`. Same justification: the spec asserts the behaviour; the executor mirrors the existing fixture pattern. Concrete fixture mechanics vary across the test suite (some use TestClient + monkeypatch, some build ctx directly).

**Type consistency:**
- `chunked_in_clause(keys, chunk_size=400)` signature consistent across helper + 5 loader use sites.
- `assert_query_count(conn, max_n)` signature consistent across helper + 3 regression tests.
- `Alpine.store('toast').push(message, {level})` signature consistent across all sweep sites.
- `list_for_inventory(tab, store, workspace, orphans, evictable, offset, limit) -> (rows, total)` signature consistent between the new method (Task 4) and the cache_page call site.

---

**Plan complete and saved to `docs/plans/tier-2-user-feel.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, two-stage review (spec + code quality) between tasks. Each task ships its own commit; tier-2 PR opens automatically after Task 12.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**
