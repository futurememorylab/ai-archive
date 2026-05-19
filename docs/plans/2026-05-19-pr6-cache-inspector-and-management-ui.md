# PR 6: Cache inspector + cache management UI + LRU eviction — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the three-layer cache the user sees (`metadata` / `media-local` / `media-ai`) inspectable and reclaimable. PR 6 introduces two new services — read-only `CacheInspector` and mutating `CacheActions` — backed by a single new audit table (`cache_actions_log`); a background `LruEviction` task that respects workspace pins; one new HTML page (`/cache`) and one new inline HTMX partial (per-clip three-glyph badge `[● ▣ ▲]`) wired into the existing CatDV list; and the matching JSON routes so the badge and page refresh without page reloads. The `proxy_cache` and `ai_store_files` tables are unchanged (they already have provider-aware columns from PR 3 and the store-id from migration 0002); only `cache_actions_log` is new. Eviction enforces the four invariants from spec §9.5 (workspace-pinned local media is sticky; pending ops keep AI uploads and metadata pinned; `evict_clip_everywhere(force=True)` is the only path that crosses both); the LRU task always logs to `cache_actions_log` with `who='system'`. This is the sixth of seven PRs implementing the design in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (§9 cache state & management, §13 PR 6).

**Architecture:** A single new migration `0006_cache_actions_log.sql` creates `cache_actions_log` per spec §9.3 (the same spec also shows `ai_store_files` but that table already exists in migration 0002 — leave it alone). `backend/app/repositories/cache_actions_log.py` is a thin raw-SQL `aiosqlite` repo with one method, `append(row)`, plus list helpers for the audit page. `backend/app/services/cache_inspector.py` builds frozen `LayerStatus` / `ClipCacheStatus` / `CacheSummary` records by joining `clip_cache`, `proxy_cache`, `ai_store_files`, and (for the pin column) `workspace_clips`. Last-used for `media-local` is `proxy_cache.last_accessed_at` (existing column written by `ProxyCacheRepo.touch()`); for `media-ai` it's `ai_store_files.last_used_at`; for `metadata` it's `clip_cache.fetched_at` (best signal — see decision 1). `backend/app/services/cache_actions.py` is the mutating service: each method consults the inspector for the relevant invariant before performing the action, appends a `cache_actions_log` row regardless of outcome (`ok|skipped|partial|error`), and only the `media-local` and `media-ai` evictions actually touch storage (delete the proxy file + row; call `ai_store.evict()` respectively). `backend/app/services/lru_eviction.py` is a `start()` / `stop()` background task in the same shape as `SyncEngine` / `ConnectionMonitor`: every `lru_tick_interval_s` it walks `proxy_cache` ordered by `last_accessed_at ASC`, skips any clip pinned in `workspace_clips`, and evicts via `CacheActions.evict_local_media(force=False)` until total non-pinned local-media size is under `media_cache_cap_gb`. Settings gains `media_cache_cap_gb` and `lru_tick_interval_s`. New routes under `backend/app/routes/cache.py` mirror the inspector / actions surface as both JSON (`/api/cache/...`) and HTMX-friendly HTML (`/cache` page, `/ui/cache-badge/{provider}/{clip_id}` partial). The existing `workspace_switcher.html` and the clip-list partial both gain the new cache badge. `AppContext` gains `cache_inspector`, `cache_actions`, `lru_eviction`, and `cache_actions_log_repo`.

**Tech Stack:** Python 3.12, FastAPI, frozen dataclasses, `asyncio`, `aiosqlite`, Jinja2 (already in deps via FastAPI), `pytest` + `pytest-asyncio`. No new third-party deps. Migration is a single SQL file applied by `migrations_runner.apply_migrations`.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 6 lists. NOT in this PR: FS adapter (PR 7); a real GCS download/inspection of objects on evict (the `ai_store.evict()` port from PR 2 already handles the bucket-side delete); renaming of `ai_store_files` columns or PKs (deferred per PR 2's plan — the table is keyed on `(store_id, catdv_clip_id)` and we read it through `provider_id`/`provider_clip_id` columns added by PR 3); any new cache layers beyond the three the spec defines (`metadata`, `media-local`, `media-ai`); a settings UI for the cap (env var only). The cache page is plain HTMX server-rendered HTML — no client state beyond the row checkboxes the browser already maintains.

**Decisions to record in `docs/decisions.md`** (Task 14):
1. **`last_used_at` for `metadata` is `clip_cache.fetched_at`.** The cache mirror has no per-row "accessed" column. We considered adding one but rejected it: the only consumer would be the UI's "age" display, and `fetched_at` is the existing freshness clock the TTL machinery uses. The display column is labelled "Cached" / "Fetched" in the UI to match.
2. **`who` in `cache_actions_log` is the literal string `"system"` for LRU evictions, and `"request"` for user-initiated routes.** PR 6 does not introduce auth or a user model; later PRs that add identity can replace `"request"` with a stable user id without a schema change (the column is `TEXT`). Calling this out explicitly so future auth work doesn't surprise audit consumers.
3. **`evict_clip_everywhere` orders the calls as `media-ai → media-local → metadata`, terminating on first invariant failure unless `force=True`.** This matches spec §9.5 rule 4: metadata last so the inspector can still answer "what was deleted?" mid-flight. With `force=True` the order is unchanged but no invariant short-circuits.
4. **LRU eviction reads the cap as bytes from `settings.media_cache_cap_gb * 1024**3` and never crosses a pin.** If every non-pinned row would still leave the cache over cap (because pins exceed the cap), the task logs a `partial` result and emits a single warning log; it does not attempt to break a pin. This matches spec §9.5 rule 1.
5. **`CacheActions` writes a `cache_actions_log` row for every call, including skips.** "Why didn't this evict?" is itself diagnostic information; a missing log entry would force a re-run to find out. The detail column carries the invariant name (e.g. `"pinned_by_workspaces=[3,5]"`, `"pending_ops=2"`).
6. **`list_orphans()` defines an orphan as either (a) a `proxy_cache` row whose `clip_cache` row is absent OR whose `provider.get_clip()` returns not-found, or (b) an `ai_store_files` row whose `clip_cache` row is absent.** We don't call the provider for the AI-store leg because Gemini Files / GCS rows are still valid as long as the local mirror still references them; if the local mirror is gone the bucket entry is dead weight. For the local leg we use the cheap `clip_cache` check first; the `provider.get_clip()` round-trip is gated on a `deep=True` flag the route doesn't enable by default (avoids a thundering herd when offline).

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/migrations/0006_cache_actions_log.sql` | Creates `cache_actions_log` table per spec §9.3. |
| `backend/app/repositories/cache_actions_log.py` | `CacheActionsLogRepo`: `append`, `list_recent`. |
| `backend/app/services/cache_inspector.py` | Frozen dataclasses (`LayerStatus`, `ClipCacheStatus`, `CacheSummary`); `CacheInspector` with `status_for_clip`, `status_for_clips`, `summary`, `list_orphans`. |
| `backend/app/services/cache_actions.py` | `CacheActions`: `evict_local_media`, `evict_ai_media`, `evict_metadata`, `evict_clip_everywhere`, `bulk_evict`, `evict_orphans`. |
| `backend/app/services/lru_eviction.py` | `LruEviction` background task; `start()` / `stop()` / `tick_once()`. |
| `backend/app/routes/cache.py` | `GET/POST /api/cache/...` JSON routes; `GET /cache` HTML page; `GET /ui/cache-badge/{provider}/{clip_id}` HTMX partial. |
| `backend/app/templates/cache_badge.html` | The three-glyph `[● ▣ ▲]` inline badge. |
| `backend/app/templates/cache_page.html` | Server-rendered cache-management page. |
| `backend/app/templates/cache_popover.html` | Per-clip cache popover with per-layer Evict buttons. |
| `tests/integration/test_migration_0006.py` | Asserts `cache_actions_log` shape. |
| `tests/integration/test_cache_actions_log_repo.py` | `append` / `list_recent`. |
| `tests/integration/test_cache_inspector.py` | `status_for_clip[s]`, `summary`, `list_orphans`. |
| `tests/integration/test_cache_actions.py` | Invariant tests per spec §9.5 + audit-log writes. |
| `tests/integration/test_lru_eviction.py` | LRU stops at cap, respects pins, logs `lru_evict`. |
| `tests/integration/test_routes_cache.py` | JSON + HTML routes; HTMX partial render. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/context.py` | Add `cache_actions_log_repo`, `cache_inspector`, `cache_actions`, `lru_eviction`; wire in `build`; stop in `aclose`; start in main. |
| `backend/app/main.py` | Register `cache` router; start `lru_eviction` if `init_external`. |
| `backend/app/settings.py` | Add `media_cache_cap_gb: int = 50`, `lru_tick_interval_s: int = 300`. |
| `backend/app/templates/workspace_switcher.html` | Optional: leave alone; cache badge rendering is per-clip in the clip-list partial only. |
| `backend/app/routes/ui.py` | Cache badge route lives in `routes/cache.py`; this file unchanged. |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES` gains `"cache_actions_log"`. |

### Deleted files

None.

---

## Tasks

### Task 1: Migration 0006 — `cache_actions_log`

**Files:**
- Create: `backend/migrations/0006_cache_actions_log.sql`
- Create: `tests/integration/test_migration_0006.py`
- Modify: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Failing test for migration shape**
  - Columns: `id INTEGER PK, who TEXT NOT NULL, action TEXT NOT NULL, clip_keys TEXT NOT NULL, result TEXT NOT NULL, detail TEXT, bytes_freed INTEGER NOT NULL DEFAULT 0, at TEXT NOT NULL`.
  - Index on `(at)` for "recent actions" page.
- [ ] **Step 2: Write the migration** (raw SQL, single file).
- [ ] **Step 3: Update `EXPECTED_TABLES`** in `test_initial_schema.py`.
- [ ] **Step 4: Verify** — `pytest tests/integration/test_migration_0006.py tests/integration/test_initial_schema.py`.

### Task 2: `CacheActionsLogRepo`

**Files:**
- Create: `backend/app/repositories/cache_actions_log.py`
- Create: `tests/integration/test_cache_actions_log_repo.py`

Methods:
- `append(conn, *, who, action, clip_keys: list[ClipKey], result, detail=None, bytes_freed=0) -> int`
- `list_recent(conn, *, limit=100) -> list[dict]`

The `clip_keys` argument is serialised as a JSON array of `[provider_id, provider_clip_id]` pairs.

### Task 3: `CacheInspector` service

**Files:**
- Create: `backend/app/services/cache_inspector.py`
- Create: `tests/integration/test_cache_inspector.py`

Dataclasses (frozen) exactly as spec §9.2. `CacheSummary`:
```python
@dataclass(frozen=True)
class CacheSummary:
    total_local_bytes: int          # metadata + media-local sums
    total_ai_bytes: int
    counts_by_store: dict[str, int]      # store_id -> clip count
    counts_by_workspace: dict[int, int]  # ws_id -> clip count
    pending_ops_count: int
    media_cache_cap_bytes: int
```

`CacheInspector` methods:
- `status_for_clip(key) -> ClipCacheStatus`
- `status_for_clips(keys) -> list[ClipCacheStatus]` — single batched DB pass per layer.
- `summary() -> CacheSummary`
- `list_orphans(deep: bool = False) -> list[ClipCacheStatus]`

Tests cover:
- a clip with all three layers present;
- a clip with metadata only;
- pin reflection via `workspace_clips` (multiple pins);
- `summary` totals are consistent;
- `list_orphans` finds a `proxy_cache` row with no `clip_cache`.

### Task 4: `CacheActions` service

**Files:**
- Create: `backend/app/services/cache_actions.py`
- Create: `tests/integration/test_cache_actions.py`

Surface per spec §9.2; each method:
1. Loads relevant invariants (pinned-by workspaces, pending ops count).
2. If an invariant blocks and `force=False`, appends a `cache_actions_log` row with `result='skipped'`, `detail='<reason>'`, returns.
3. Else performs the action (deletes proxy file + `proxy_cache` row; calls `ai_store.evict(key)`; deletes `clip_cache` row), appends `cache_actions_log` `result='ok'`.

`bulk_evict(keys, layers, force=False) -> BulkEvictResult`:
```python
@dataclass(frozen=True)
class BulkEvictResult:
    ok: int
    skipped: int
    errors: int
    bytes_freed: int
    log_ids: list[int]
```

`evict_orphans()` calls the inspector's `list_orphans()` then iterates with appropriate per-layer evictions.

Invariant tests (spec §9.5):
1. Pinned local media not evictable without force.
2. Metadata not evictable while pending ops exist for that clip.
3. AI store entry not evictable while pending ops exist.
4. `evict_clip_everywhere(force=True)` evicts all three, logs prominently.
5. `cache_actions_log` row written for every action including skips (assert counts).

### Task 5: LRU eviction background task

**Files:**
- Create: `backend/app/services/lru_eviction.py`
- Create: `tests/integration/test_lru_eviction.py`

Class shape:
```python
class LruEviction:
    def __init__(self, *, inspector, actions, db_provider,
                 workspaces_repo, settings, clock=None) -> None: ...
    async def tick_once(self) -> int: ...   # returns rows evicted
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

`tick_once` algorithm (spec §9.6):
1. Compute total non-pinned local-media size = sum(`proxy_cache.size_bytes`) where the clip is NOT in any `workspace_clips` row.
2. If under cap, return 0.
3. Else select non-pinned proxy_cache rows ordered by `last_accessed_at ASC`, calling `actions.evict_local_media(key, force=False)` until under cap.
4. Log each eviction as `cache_actions_log` `action='lru_evict'`, `who='system'`.

Tests:
- under-cap → no-op;
- over-cap with three non-pinned rows → evicts oldest first;
- over-cap with one pin → never evicts the pinned row, logs `partial` if still over cap;
- LRU loop start/stop lifecycle (one tick fires, then stop is clean).

### Task 6: Settings additions

**Files:**
- Modify: `backend/app/settings.py`

Add:
```python
media_cache_cap_gb: int = 50
lru_tick_interval_s: int = 300
```

### Task 7: Cache routes (JSON + HTML)

**Files:**
- Create: `backend/app/routes/cache.py`
- Create: `tests/integration/test_routes_cache.py`

Endpoints:
- `GET /api/cache/clip/{provider}/{clip_id}` → JSON `ClipCacheStatus`.
- `POST /api/cache/clip/{provider}/{clip_id}/evict` → body `{layers: [...], force: bool}`; returns updated status.
- `GET /api/cache/summary` → JSON `CacheSummary`.
- `GET /api/cache/orphans` → JSON list.
- `POST /api/cache/bulk-evict` → body `{clip_keys: [[provider, id], ...], layers: [...], force: bool}`; returns `BulkEvictResult`.
- `GET /cache` → HTML cache management page.
- `GET /ui/cache-badge/{provider}/{clip_id}` → HTMX partial.

Tests cover happy paths and the invariant-blocked-evict case (returns updated status with the layer still present + skip reason).

### Task 8: Templates

**Files:**
- Create: `backend/app/templates/cache_badge.html` (the inline `[● ▣ ▲]`).
- Create: `backend/app/templates/cache_popover.html` (per-layer rows with Evict).
- Create: `backend/app/templates/cache_page.html` (summary block + table + filters + bulk-evict form).

The badge takes a `ClipCacheStatus`-like dict and renders three spans with colour classes (`fresh` / `stale` / `absent`). Each glyph has a `title` for hover, and the whole badge has `hx-get="/ui/cache-popover/{provider}/{clip_id}"` to load the popover on click.

### Task 9: AppContext + main wiring

**Files:**
- Modify: `backend/app/context.py`
- Modify: `backend/app/main.py`

Add: `cache_actions_log_repo`, `cache_inspector`, `cache_actions`, `lru_eviction`. Start/stop `lru_eviction` alongside `connection_monitor` / `sync_engine` in `main.lifespan` when `init_external`. Register the `cache` router.

### Task 10: Verify schema test + full suite

Run `pytest -q --deselect tests/integration/test_proxy_resolver_fs.py::test_fs_resolver_raises_when_unreadable`. Baseline 279 must rise; no regressions.

### Task 11: Ruff

`ruff check backend tests` — no new lints above existing baseline (~90).

### Task 12: Append decisions to `docs/decisions.md`

Write entries for the six decisions enumerated above.

### Task 13: Commit logically

One commit per task group, matching prior PRs' style:
- `chore(migrations): 0006 cache_actions_log`
- `feat(repo): CacheActionsLogRepo`
- `feat(svc): CacheInspector + CacheSummary`
- `feat(svc): CacheActions + invariant enforcement`
- `feat(svc): LruEviction background task`
- `feat(settings): media cache cap + LRU tick interval`
- `feat(routes): /api/cache/* + /cache HTML page + cache badge partial`
- `feat(templates): cache badge + popover + management page`
- `feat(context): wire CacheInspector / CacheActions / LruEviction`
- `docs(decisions): record PR 6 cache-layer signal + audit choices`
