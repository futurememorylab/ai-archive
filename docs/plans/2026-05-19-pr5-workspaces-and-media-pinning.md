# PR 5: Workspaces + media pinning + offline-cycle UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the user-visible offline workflow on top of the PR 4 plumbing. PR 5 introduces *workspaces* — named pinned subsets of an archive's clips with their metadata and proxy media cached locally — plus the four UI surfaces the spec calls out (connection pill, workspace switcher, sync drawer, per-clip queued badge) as plain Jinja2 + HTMX partials. The migration adds the `workspaces` and `workspace_clips` tables and tightens `clip_cache.pinned_to_workspace_id` from a bare INTEGER into a real foreign key (the `ON DELETE SET NULL` link to `workspaces(id)` PR 3 promised). A new `WorkspaceManager` service drives the lifecycle (`create_workspace` → `add_clips` → `prepare()` → `release()`) and pins both the metadata row (`clip_cache.pinned_to_workspace_id`) and the proxy file (via the existing `proxy_resolver`). New routes expose workspace CRUD/prep/release, the sync drawer (pending_operations CRUD: retry / discard), and the manual `online`/`offline` connection-monitor override. An end-to-end test exercises the full cycle: create workspace, prep two cached clips against a fake CatDV, flip to offline, enqueue applies, assert the engine doesn't drain, flip back online, run a drain, assert the fake CatDV got PUTs and `write_log` got entries. This is the fifth of seven PRs implementing the design in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (§7.1 workspaces tables, §7.3 manual override, §8 workspaces lifecycle, §8.3 UI surfaces, §13 PR 5).

**Architecture:** A single new migration `0005_workspaces.sql` creates `workspaces` and `workspace_clips` and rebuilds `clip_cache` to attach the `pinned_to_workspace_id` FK (the SQLite table-rebuild idiom: rename old → create new with FK → copy data → drop old → rename, all in one migration). A new `backend/app/repositories/workspaces.py` exposes `WorkspacesRepo` with CRUD on both tables plus two views the cache layer will use: `pinned_clip_keys(ws_id)` and `workspaces_pinning(clip_key)`. A new `backend/app/services/workspace_manager.py` provides the lifecycle: `create_workspace` writes the row + initial `workspace_clips` rows; `add_clips` / `remove_clips` adjust membership and the primary-pin column on `clip_cache`; `prepare()` walks pending workspace_clips, calls `provider.get_clip()` (write-through caches), then — if `provider.capabilities.media_is_local` is False — drives the existing `proxy_resolver.path_for_clip_id()` to materialise the proxy on disk, transitioning the row through `pending → metadata → media → ready` (or `error`); `release()` clears pins from `workspace_clips` (and the row from `workspaces` if requested) but **does not auto-evict cache** (per spec §9.5 rule 5). New routes under `backend/app/routes/workspaces.py` and `backend/app/routes/sync.py` cover workspace CRUD/prep/release and the sync drawer. The connection-toggle endpoints (`POST /api/connection/offline` / `online`) ride the existing `connection_monitor.set_manual_offline(bool)`. A minimal HTMX UI lives under a new `backend/app/templates/` directory and is served by a new `backend/app/routes/ui.py` for the four surfaces the spec calls out; the templates are server-rendered Jinja2 + HTMX, no JS framework. `AppContext` gains `workspaces_repo`, `workspace_manager` and a `templates_dir` resolver. The CatDV adapter and proxy_resolver are unchanged.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, dataclasses (frozen), `asyncio`, `aiosqlite`, Jinja2 (already a transitive dep of FastAPI), `pytest` + `pytest-asyncio`. No new third-party deps. Migration is a plain SQL file applied by `migrations_runner.apply_migrations`.

**Scope guardrail:** This plan implements ONLY what spec §13 PR 5 lists plus the UI bits §8.3 explicitly mentions. NOT in this PR: the `/cache` page, `CacheInspector` / `CacheActions` services, `cache_actions_log` table, inline cache-layer badge (all PR 6). The per-clip *queued count* badge IS PR 5 because the sync drawer needs the same data join. NOT in scope: FS adapter (PR 7), background cron / scheduled prep (spec §16 says user-triggered only for v2), LRU eviction loop (PR 6), conflict-resolution dialog beyond the read-only "view conflict" surface (full UI is spec-pending). No multi-provider runtime selection. The proxy_resolver remains the media-fetch primitive (no new `provider.fetch_media()` method); a workspace prep job for a CatDV clip is exactly the existing `resolver.path_for_clip_id()` invocation, gated by `provider.capabilities.media_is_local`.

**Decisions to record in `docs/decisions.md`** (Task 14, if non-trivial):
1. **`clip_cache.pinned_to_workspace_id` is the *primary* pin; `workspace_clips` is the source of truth.** A clip can belong to multiple workspaces but the FK column on `clip_cache` is single-valued. The convention: the most-recent workspace to pin a clip "wins" the column, but `workspaces_pinning(clip_key)` queries `workspace_clips` to answer "is this clip pinned at all?". PR 6 uses the multi-row view for the cache-evictability invariants and the "pinned by: <names>" UI.
2. **SQLite FK migration via table rebuild.** Adding a foreign key to an existing SQLite column is not supported by `ALTER TABLE`. The migration renames `clip_cache` to `clip_cache_old`, creates `clip_cache` with the `REFERENCES workspaces(id) ON DELETE SET NULL` clause, copies rows over, drops the old table, and recreates the catalog index. Pragma `foreign_keys` is *not* enabled by aiosqlite by default — we still write the FK so that any later enabling is correct, and document this in the decision log.
3. **Workspace prep uses the existing `proxy_resolver`, not a new `provider.fetch_media()`.** The spec talks about `provider.fetch_media()`; the codebase already has `proxy_resolver.path_for_clip_id()` which does exactly the same thing and is hooked up to the right cache directory. Wrapping it again would double the surface for no benefit. The capability gate is `provider.capabilities.media_is_local`: when True the resolver is skipped.
4. **`release()` is non-destructive.** Per spec §9.5 rule 5: dropping pins is not the same as evicting bytes. The metadata row stays in `clip_cache`, the proxy stays on disk, but the `pinned_to_workspace_id` reverts to NULL (or the next-most-recent pinning workspace). LRU may evict later (PR 6).
5. **HTMX partials live under `backend/app/templates/` with no Tailwind step.** The existing decision-log entry "Python-only stack, no Node frontend" mentions Tailwind, but for PR 5 we ship plain HTML + inline CSS classes that match Tailwind's utility names so a future Tailwind build picks them up. No build step required to test PR 5; PR 7's deploy work can wire Tailwind.
6. **Sync drawer "discard" deletes the `pending_operations` row but leaves the originating `review_items.applied_at` as-is.** The user accepted the item and clicked apply; if they discard the pending op, the audit trail says "the user gave up on this write" — the review item is *not* reverted to undecided. Re-applying would no-op because `applied_at IS NOT NULL` (the same dedup PR 4's `enqueue_apply` relies on). A future "re-queue" action would have to clear `applied_at` on the named items.
7. **Per-clip badge is a single JOIN, not a denormalised counter.** A SQL view (`clip_pending_counts`) would be slightly faster but creates an invalidation problem when ops change status. A small `SELECT count(*) FILTER (WHERE status='pending') ... GROUP BY provider_clip_id` over `pending_operations` runs in <1ms for any plausible queue and the UI never paints more than ~50 rows at once.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/migrations/0005_workspaces.sql` | Creates `workspaces`, `workspace_clips`; rebuilds `clip_cache` with the FK on `pinned_to_workspace_id`. |
| `backend/app/repositories/workspaces.py` | `WorkspacesRepo`: workspace CRUD; `workspace_clips` CRUD; `pinned_clip_keys`, `workspaces_pinning`. |
| `backend/app/services/workspace_manager.py` | `WorkspaceManager` lifecycle service: `create_workspace`, `add_clips`, `remove_clips`, `prepare()` (async iterator of `PrepEvent`), `release()`, `list_workspaces`, `get`. |
| `backend/app/routes/workspaces.py` | `GET/POST /api/workspaces`, `POST /api/workspaces/{ws}/clips`, `DELETE /api/workspaces/{ws}/clips/{provider}/{clip_id}`, `POST /api/workspaces/{ws}/prepare` (SSE), `POST /api/workspaces/{ws}/release`. |
| `backend/app/routes/sync.py` | `GET /api/sync/pending`, `POST /api/sync/run`, `POST /api/sync/pending/{id}/retry`, `POST /api/sync/pending/{id}/discard`. |
| `backend/app/routes/ui.py` | Server-rendered HTMX surfaces: `GET /ui/connection-pill`, `GET /ui/workspace-switcher`, `GET /ui/sync-drawer`, `GET /ui/clip-badge/{provider}/{clip_id}`. |
| `backend/app/templates/connection_pill.html` | Connection pill HTMX partial. |
| `backend/app/templates/workspace_switcher.html` | Workspace switcher HTMX partial. |
| `backend/app/templates/sync_drawer.html` | Sync drawer HTMX partial. |
| `backend/app/templates/clip_badge.html` | Per-clip badge HTMX partial. |
| `tests/integration/test_migration_0005.py` | Asserts `workspaces` + `workspace_clips` shapes; asserts `clip_cache` rebuild preserved data and has the FK. |
| `tests/integration/test_workspaces_repo.py` | CRUD + `pinned_clip_keys` + `workspaces_pinning`. |
| `tests/integration/test_workspace_manager.py` | `create`, `add_clips`, `prepare()` events, `release()` does not evict. |
| `tests/integration/test_routes_workspaces.py` | HTTP routes — CRUD, prepare progress, release. |
| `tests/integration/test_routes_sync.py` | Pending list / run / retry / discard. |
| `tests/integration/test_offline_cycle_e2e.py` | End-to-end: workspace + prep + offline + apply + online + drain → CatDV PUT + write_log. |

### Modified files

| Path | Change |
|---|---|
| `backend/app/context.py` | Add `workspaces_repo` and `workspace_manager` fields; wire them in `build`. |
| `backend/app/main.py` | Register `workspaces`, `sync`, `ui` routers. |
| `backend/app/routes/connection.py` | Add `POST /api/connection/offline` and `POST /api/connection/online` thin wrappers over `connection_monitor.set_manual_offline(bool)`. |
| `backend/app/repositories/pending_operations.py` | Add `delete(op_id)` (discard) and `reset_for_retry(op_id)` (zero attempts, status=pending). Add `count_pending_by_clip()` for the badge join. |
| `tests/integration/test_initial_schema.py` | `EXPECTED_TABLES` gains `"workspaces"` and `"workspace_clips"`. |

### Deleted files

None.

---

## Tasks

### Task 1: Migration 0005 — workspaces + clip_cache FK rebuild

**Files:**
- Create: `backend/migrations/0005_workspaces.sql`
- Create: `tests/integration/test_migration_0005.py`
- Modify: `tests/integration/test_initial_schema.py`

- [ ] **Step 1: Failing test for migration shape**
  Write `test_migration_0005.py` asserting:
  - `workspaces` has columns `id, name UNIQUE, provider_id, catalog_id, created_at, description`.
  - `workspace_clips` has `(workspace_id FK → workspaces ON DELETE CASCADE, provider_id, provider_clip_id, added_at, cache_state, cache_error)` with PK `(workspace_id, provider_id, provider_clip_id)`.
  - `clip_cache` row count is preserved across the rebuild (insert a row before applying 0005, verify it survives — done by applying 0001–0004, inserting a `clip_cache` row, then applying 0005 explicitly).
  - `clip_cache.pinned_to_workspace_id` foreign key target is `workspaces(id)` (via `PRAGMA foreign_key_list(clip_cache)`).
- [ ] **Step 2: Write the migration**
  Use the SQLite table-rebuild idiom for clip_cache. Be careful with the catalog index.
- [ ] **Step 3: Update `test_initial_schema.py`** add the two new tables to `EXPECTED_TABLES`.
- [ ] **Step 4: Verify** — `pytest tests/integration/test_migration_0005.py tests/integration/test_initial_schema.py`.

### Task 2: `WorkspacesRepo`

**Files:**
- Create: `backend/app/repositories/workspaces.py`
- Create: `tests/integration/test_workspaces_repo.py`

Operations:
- `create(conn, *, name, provider_id, catalog_id, description=None) -> int`
- `get(conn, ws_id) -> dict | None` (joined with `workspace_clips` for cache_state counts)
- `list(conn) -> list[dict]`
- `delete(conn, ws_id) -> None`
- `add_clips(conn, ws_id, clip_keys: list[ClipKey]) -> int` — upserts `workspace_clips` rows, returns count of newly added (existing rows untouched).
- `remove_clips(conn, ws_id, clip_keys: list[ClipKey]) -> int`
- `list_clips(conn, ws_id) -> list[dict]`
- `set_cache_state(conn, ws_id, clip_key, state, error=None)`
- `pinned_clip_keys(conn, ws_id) -> list[ClipKey]`
- `workspaces_pinning(conn, clip_key) -> list[int]` — list of workspace IDs that have this clip in `workspace_clips`.

Tests cover: create/list/get; add same clip twice → upsert / no duplicate; remove returns 0 when not present; `workspaces_pinning` reflects membership.

### Task 3: `WorkspaceManager` service

**Files:**
- Create: `backend/app/services/workspace_manager.py`
- Create: `tests/integration/test_workspace_manager.py`

Surface:
```python
@dataclass(frozen=True)
class PrepEvent:
    clip_key: ClipKey
    state: str          # "metadata" | "media" | "ready" | "error"
    error: str | None = None

class WorkspaceManager:
    async def create_workspace(self, *, name, provider_id, catalog_id, description=None, clip_keys: list[ClipKey] = []) -> int: ...
    async def add_clips(self, ws_id: int, clip_keys: list[ClipKey]) -> None: ...
    async def remove_clips(self, ws_id: int, clip_keys: list[ClipKey]) -> None: ...
    async def prepare(self, ws_id: int) -> AsyncIterator[PrepEvent]: ...
    async def release(self, ws_id: int, *, delete_workspace: bool = False) -> None: ...
    async def list_workspaces(self) -> list[dict]: ...
    async def get(self, ws_id: int) -> dict | None: ...
```

Implementation notes:
- `prepare()` yields one PrepEvent per state transition per clip, in order: metadata → media (if media not local) → ready. Per-clip independence: an error on clip A doesn't stop clip B. Resumable: if `cache_state` is already `ready`, skip.
- `prepare()` writes-through the `clip_cache.pinned_to_workspace_id` column when transitioning to ready (or earlier — once we have metadata is fine).
- Media path: if `provider.capabilities.media_is_local` is False, call `proxy_resolver.path_for_clip_id(int(clip_id))`. The resolver caches to disk under `DATA_DIR/cache/proxies/...`.
- `release()`: for each `workspace_clips` row, set `pinned_to_workspace_id = NULL` on `clip_cache` IF the current value equals this workspace_id AND there's no other workspace pinning it (otherwise re-point to one of the other pinners). Then delete the `workspace_clips` rows. If `delete_workspace=True`, also delete the workspace.

Tests use a fake `ArchiveProvider` and a fake `proxy_resolver` that records calls and writes a small stub file. Cover happy path, partial error, release-with-no-evict.

### Task 4: pending_operations repo additions

**Files:**
- Modify: `backend/app/repositories/pending_operations.py`
- Modify: `tests/integration/test_pending_operations_repo.py`

Add:
- `delete(conn, op_id)` — used by sync drawer "discard".
- `reset_for_retry(conn, op_id)` — sets `status='pending'`, `attempts=0`, `last_error=NULL`, `attempted_at=NULL`. Used by "retry".
- `count_pending_by_clip(conn, *, provider_id) -> dict[str, dict[str, int]]` — returns `{clip_id: {"pending": N, "conflict": M}}` for the per-clip badge.

### Task 5: Connection routes — manual override

**Files:**
- Modify: `backend/app/routes/connection.py`

Add `POST /api/connection/offline` → `set_manual_offline(True)`; `POST /api/connection/online` → `set_manual_offline(False)`. Return current state.

### Task 6: Workspaces routes

**Files:**
- Create: `backend/app/routes/workspaces.py`
- Create: `tests/integration/test_routes_workspaces.py`

Endpoints per spec. `prepare` is SSE: stream `PrepEvent`s as `data: {...}\n\n`.

### Task 7: Sync drawer routes

**Files:**
- Create: `backend/app/routes/sync.py`
- Create: `tests/integration/test_routes_sync.py`

Endpoints:
- `GET /api/sync/pending` returns JSON list of pending_operations rows annotated with clip name (joined from `clip_cache`).
- `POST /api/sync/run` calls `sync_engine.drain_once()` and returns `{processed: N}`.
- `POST /api/sync/pending/{id}/retry` resets the row and notifies the engine.
- `POST /api/sync/pending/{id}/discard` deletes the row.

### Task 8: UI surfaces (HTMX templates)

**Files:**
- Create: `backend/app/templates/*.html`
- Create: `backend/app/routes/ui.py`

`backend/app/routes/ui.py` uses `fastapi.templating.Jinja2Templates`. Four endpoints render the four partials with the current data.

### Task 9: AppContext + main wiring

**Files:**
- Modify: `backend/app/context.py`
- Modify: `backend/app/main.py`

### Task 10: End-to-end offline cycle test

**Files:**
- Create: `tests/integration/test_offline_cycle_e2e.py`

Scenario (drives the real `CatdvArchiveAdapter` against `FakeCatdv`):
1. Seed the fake with two clips (id 1, 2) and two short proxy blobs.
2. Build a real `AppContext` against the fake CatDV (`init_external=False` plus manual wire-up of CatDV client + archive adapter + proxy_resolver + workspace_manager + sync_engine + connection_monitor with a never-firing probe interval).
3. Create a workspace and add both clips.
4. Call `prepare()`; assert both clips end at `ready` and proxy files exist on disk; assert `clip_cache` has both rows.
5. Flip `set_manual_offline(True)`.
6. Insert a fake `ReviewItem` + `Annotation` per clip and call `WriteQueue.enqueue_apply(...)`; trigger `sync_engine.drain_once()`; assert nothing was sent (fake `put_log` is empty).
7. Flip `set_manual_offline(False)`.
8. Call `sync_engine.drain_once()`; assert `len(put_log) == 2` and `write_log` has two `ok` rows.

### Task 11: Run full suite

`pytest -q --deselect tests/integration/test_proxy_resolver_fs.py::test_fs_resolver_raises_when_unreadable`. Baseline 237 must rise; no regressions.

### Task 12: Ruff

`ruff check backend tests` — no new lints above existing baseline (~90).

### Task 13: Manually exercise the E2E

Already covered by Task 10's pytest. Run it once on its own and confirm it doesn't hang.

### Task 14: Append decisions to `docs/decisions.md`

Write entries for:
- "primary pin" semantics
- SQLite FK migration via table rebuild
- Workspace prep reuses proxy_resolver (no new `fetch_media`)
- `release()` is non-destructive

### Task 15: Commit logically

One commit per task group. Style: `feat(...): ...`, `chore(migrations): ...`, `test(...): ...`, matching PR 4's commit log.
