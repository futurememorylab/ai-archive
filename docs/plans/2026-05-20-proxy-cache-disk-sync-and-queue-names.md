# Plan — proxy_cache ↔ disk sync at startup, and clip names in the queue

Date: 2026-05-20
Branch: `fix/cache-disk-sync-and-queue-names`

Two related fixes surfaced during manual testing:

1. **`proxy_cache` drifts from disk.** Files in `data/cache/proxies/` could exist without a row in `proxy_cache`, so the cache view reports "metadata only" even though the video is on disk. Today's safeguard is a *backfill on resolve* in `RestProxyResolver.path_for_clip_id()` — but that only runs when someone asks for the path. Orphans sit indefinitely until you happen to open the clip.
2. **The queue table is unreadable.** Rows render as `catdv/888915` — provider/id only, no clip name. You can't tell what's downloading without a separate lookup.

## Invariant we want

> Every file in `data/cache/proxies/` has a row in `proxy_cache`, and every row in `proxy_cache` points to a real file — from the first moment the process is up.

## Fix 1 — startup reconciliation

New service `ProxyCacheReconciler` (`backend/app/services/proxy_cache_reconciler.py`). One async `reconcile()` method called once from `AppContext.build()`, right after `apply_migrations()`. Runs unconditionally (no `init_external` gate — it only touches local disk + the SQLite that's already open).

Two passes, both idempotent:

**Pass A — files → rows.** Walk `data/cache/proxies/`. For each `*.mov`:
- `clip_id = int(stem)` (skip files where stem isn't an integer).
- `existing = await proxy_cache_repo.get(conn, clip_id)`.
- If `existing is None`: `await repo.record(...)` with `size_bytes` from `stat()`, `etag=None`, `provider_id="catdv"`, `provider_clip_id=str(clip_id)`. Log INFO with the file path.
- If `existing["size_bytes"] != stat().st_size`: `repo.record(...)` again to update size + `last_used_at`. Log INFO "size drift".

**Pass B — rows → files.** Walk `proxy_cache`. For each row whose `file_path` is missing or zero-byte:
- `await repo.delete(conn, clip_id)`. Log WARNING with the path (phantom row).

Keep the existing backfill block in `RestProxyResolver.path_for_clip_id()` as defense-in-depth — once reconciliation has run at startup, normal-operation writes go through `repo.record()` after every download, so the resolver's backfill should be a no-op in steady state.

### Why not just trust resolver-backfill?

Because the resolver only runs when someone asks for that path. The whole point of the cache view is to *survey* what's on disk without poking each clip; an orphan can't be surveyed if it has no row. Startup reconciliation makes the table the source of truth for the inspector.

## Fix 2 — clip names in the queue

Modify `PrefetchQueueRepo.list_active()` and `.list_recent()` to LEFT JOIN `clip_cache` and surface `clip_name` (null when metadata isn't cached yet). One join per call, not N+1. The `count_by_status()` call stays unchanged.

```sql
SELECT q.id, q.provider_id, q.provider_clip_id, q.status,
       q.requested_by, q.requested_at, q.started_at, q.finished_at,
       q.error, q.bytes_downloaded,
       cc.name AS clip_name
  FROM prefetch_queue q
  LEFT JOIN clip_cache cc
    ON cc.provider_id = q.provider_id
   AND cc.provider_clip_id = q.provider_clip_id
 WHERE q.status IN ('queued', 'downloading')
 ORDER BY q.requested_at ASC
```

Then in `_cache_queue_table.html`, render a two-line cell matching the inventory table style:

```html
<td>
  <div class="clip-cell">
    <span class="clip-name">{{ r.clip_name or r.provider_clip_id }}</span>
    <span class="clip-id mono muted-2">{{ r.provider_id }}/{{ r.provider_clip_id }}</span>
  </div>
</td>
```

When `r.clip_name` is null (metadata not yet cached), fall back to the id. CSS may already have `.clip-name`; add `.clip-cell` (flex column) if missing.

## Files

| Path | Action |
|---|---|
| `backend/app/services/proxy_cache_reconciler.py` | new — `ProxyCacheReconciler.reconcile()` |
| `backend/app/context.py` | call `reconcile()` once in `AppContext.build()` after migrations |
| `backend/app/repositories/prefetch_queue.py` | LEFT JOIN clip_cache in `list_active` + `list_recent` |
| `backend/app/templates/pages/_cache_queue_table.html` | two-line clip cell, same in active + history tables |
| `backend/app/static/app.css` | `.clip-cell` rule (if missing) |
| `tests/unit/test_proxy_cache_reconciler.py` | new — covers both passes + idempotence |
| `tests/integration/test_routes_cache.py` | assert queue partial contains the clip name |

## Tests

1. **`test_reconciler_creates_row_for_orphan_file`**: drop a file in `cache_dir`, no row in `proxy_cache`, reconcile → row exists with correct size.
2. **`test_reconciler_deletes_phantom_row`**: row in `proxy_cache` pointing to a missing file → row gone after reconcile.
3. **`test_reconciler_updates_drifted_size`**: row size != file size → reconcile aligns row to file.
4. **`test_reconciler_is_idempotent`**: run reconcile twice → second pass writes nothing.
5. **`test_reconciler_skips_non_integer_filenames`**: `weird.mov` and `README.txt` in cache_dir → silently ignored.
6. **`test_prefetch_queue_list_active_returns_clip_name`**: insert clip_cache + queue rows, `list_active()` row dict has `clip_name` populated.
7. **`test_prefetch_queue_list_recent_clip_name_null_when_metadata_absent`**: queue row exists, no matching clip_cache → `clip_name is None`.
8. **`test_cache_queue_partial_renders_clip_name`**: GET `/ui/cache/queue` after seeding → response body contains the clip name string.

## Verification (manual)

1. Stop the server gracefully.
2. Remove ARNOLD's proxy_cache row to simulate the drift state: `sqlite3 data/app.db "DELETE FROM proxy_cache WHERE provider_clip_id='888839';"`
3. Confirm the file is still on disk.
4. Start the server.
5. The reconciler should log `INFO: backfilled proxy_cache for data/cache/proxies/888839.mov`.
6. `sqlite3 data/app.db "SELECT * FROM proxy_cache WHERE provider_clip_id='888839';"` shows the row.
7. Open `/cache` → ARNOLD shows media-local present with no manual hit.
8. Queue the clip (or any) and switch to the Queue tab → row shows the readable name, with `catdv/<id>` below as a small mono subtitle.

## Risks

- **Big cache directories**: if someone scales the cache to thousands of files, the startup stat-storm becomes visible. Today the cap is 20 GB (~30-40 clips) — irrelevant. Future-proofing not in scope here; add a skip-if-mtime-unchanged optimization later if it bites.
- **Phantom-row deletion**: deleting a row that no longer points to a file is the right call, but it removes pin/workspace links if any. Mitigate with WARNING log and a `cache_actions_log` entry so the action is auditable. (Reuse the existing log_repo from `AppContext`.)

## Out of scope

- Workspace pin migration (not affected — pinning is a workspace_clips relationship, not a `proxy_cache` field).
- Renaming "Cache video" / "Evict local" buttons on the clip detail page (UI copy unchanged).
- LRU eviction-of-orphans logic — separate concern.
