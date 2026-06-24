# 0046. No N+1 — batch repository reads with WHERE IN

**Date:** 2026-05-30
**Status:** Accepted
**Lifespan:** Invariant

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
  page slice. The store filter uses `LIKE %?%` against both `store_id`
  and `gcs_uri` to preserve the pre-refactor substring-match
  behaviour the UI relies on (the bucket name alone, e.g.
  `catdav-proxies`, matches both `gcs:catdav-proxies` and
  `gs://catdav-proxies/...`).
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
