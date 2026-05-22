# Offline Fallback Mode — Design Spec

**Date:** 2026-05-22
**Status:** Draft, awaiting review
**Author:** Peter Hora (with Claude)
**Builds on:** `2026-05-19-archive-abstraction-and-offline-mode-design.md`
(port-adapter + write-queue foundation). This spec finalises the user-facing
behavior: when does the app go offline, what does it serve, and how does the
user come back online.

---

## 1. Motivation

The 2026-05-19 spec introduced the building blocks for offline operation
(`WriteQueue`, `pending_operations`, `SyncEngine`, local clip cache, proxy
cache on disk). What's missing is the runtime glue:

- **Read paths still raise on CatDV failure.** `CatdvArchiveAdapter` raises
  `RetryableError` / `FatalProviderError` when CatDV is unreachable, even
  when stale rows exist in `clip_cache`. The user sees a hard 5xx instead
  of a usable, cached view.
- **Startup forces a CatDV login.** Running the app without VPN crashes in
  `CatdvClient.__aenter__()` — there is no way to boot in offline mode.
- **`ConnectionMonitor` probes forever.** Once offline, the monitor keeps
  hammering CatDV every 30s in the background and `SyncEngine` keeps
  retrying queued writes. The user has no agency over reconnect.

The goal: a single deployable that runs fully usable without CatDV when the
VPN is down, with an explicit user-controlled reconnect.

### Non-goals

- Multi-user offline conflict resolution (already out-of-scope per 2026-05-19).
- Allowing the app to **boot** GCP / Vertex AI offline — annotation runs
  still need the internet; we just hide the Annotate UI when offline.
- Re-architecting the cache schema. All work fits the existing tables.

---

## 2. User-visible behavior

### 2.1 Three connection states

| State | How entered | Background probing? | UI affordance |
|---|---|---|---|
| `online` | Initial startup probe succeeds. | Yes — every `health_probe_interval_s`. | Green chip. No action. |
| `offline` | Initial probe fails, **or** a periodic probe fails mid-session, **or** CatDV auth fails at startup. | **No.** Probe loop halts. | Yellow chip *"Offline — click to reconnect"*. Click → one-shot probe. |
| `forced_offline` | `CATDV_OFFLINE=true` in `.env`. | No. Login never attempted. | Red chip *"Offline (forced)"*. Static; no reconnect action. |

Auth failure at startup (wrong password, expired account) is treated as
`offline` rather than crashing — the app boots, the user sees the chip,
fixes `.env` and reconnects. The crash-on-bad-creds behavior was a
deployment papercut, not a safety property.

### 2.2 What the user sees while offline

- **Clip list:** shows only clips already present in `clip_cache` for the
  configured catalogue, paginated and searchable (substring on `name` +
  `notes`). Uncached clips are hidden — direct URLs to them return 404
  with a "not available offline" page.
- **Clip detail:** served from `clip_cache` when present; 404 otherwise.
- **Banner above clip list:** *"Showing cached clips only — N clips
  available."*
- **Hidden actions:** Annotate dropdown, "Cache locally" / "Remove from
  local cache", per-clip Evict buttons, the "Not cached" Cache filter
  option. Same hiding pattern as the existing host-local proxy mode
  (`PROXY_SOURCE=filesystem`).
- **Proxy playback:** works if the proxy file is on disk; the player
  surfaces "not cached locally" otherwise.

### 2.3 Writes while offline

Writes (marker add/edit, field changes) submitted via the UI continue to
flow into `WriteQueue` → `pending_operations` as today. The behavioral
change: `SyncEngine.tick()` checks `connection_monitor.is_online()` and
**skips entirely** when offline. No CatDV call is attempted in the
background. On reconnect, the next tick drains the accumulated queue.

The annotation run UI is hidden offline (Gemini needs the internet
anyway), so no draft writes are produced while disconnected.

### 2.4 Reconnect

The yellow chip is a `POST /api/connection/retry` button. Clicking it:
1. Issues a single CatDV health probe.
2. On success → flips state to `online`, restarts the probe loop,
   `SyncEngine` resumes on its next tick and drains queued writes.
3. On failure → stays `offline`, returns the error detail in a tooltip.

For `forced_offline`, the endpoint returns `409 forced offline` and the
chip is non-interactive.

---

## 3. Architecture

### 3.1 Settings

`backend/app/settings.py` gains one field:

```python
catdv_offline: bool = False  # env: CATDV_OFFLINE
```

`.env.example` documents it as: *"Set to true to skip CatDV login at
startup and run from local cache only."*

### 3.2 Startup wiring (`backend/app/context.py`)

```
if settings.catdv_offline:
    ctx.catdv = None  # no login, no seat
    adapter = build with client=None, is_online_provider=lambda: False
    proxy_resolver = LocalCacheOnlyResolver(...)
    connection_monitor = ConnectionMonitor in "forced_offline" mode
                         (never probes, always reports offline)
else:
    ctx.catdv = await CatdvClient(...).__aenter__()
    # If login raises, catch CatdvAuthError → log warning → treat as offline
    # (ctx.catdv stays None, adapter built without client, monitor enters
    # offline state).
    adapter = build with client=ctx.catdv,
              is_online_provider=lambda: monitor.last_status_is_ok()
    proxy_resolver = build_resolver(...) as today
    connection_monitor = ConnectionMonitor in "auto" mode
```

`SyncEngine`, `WriteQueue`, `LruEviction`, `MediaPrefetcher` are unchanged
in their construction — `SyncEngine` gains a guard inside its tick (§3.5).

### 3.3 Adapter changes (`backend/app/archive/providers/catdv/adapter.py`)

Constructor gains an optional `is_online_provider: Callable[[], bool] |
None = None`. When `None`, behaves exactly as today (preserves existing
tests).

Each of the three read methods (`list_clips`, `get_clip`,
`list_field_definitions`) is restructured:

```
fresh = read_cache_with_ttl()
if fresh is not None: return fresh

if is_online_provider and not is_online_provider():
    stale = read_cache_ignore_ttl()
    if stale is not None: return stale
    raise NotFound(...)   # for get_clip
    return empty_page     # for list_clips

try:
    live = call_catdv()
except RetryableError:
    stale = read_cache_ignore_ttl()
    if stale is not None: return stale
    raise
write_through_cache(live)
return live
```

`apply_changes` becomes:

```
if is_online_provider and not is_online_provider():
    raise RetryableError("offline")
# else: same as today
```

`RetryableError` is what `SyncEngine` already treats as "reschedule with
backoff" — so writes naturally accumulate in `pending_operations`.

### 3.4 List-from-cache helper (`backend/app/repositories/clip_cache.py`)

New method:

```python
async def list_by_catalog(
    db, *, provider_id: str, catalog_id: str,
    offset: int, limit: int, q: str | None,
) -> tuple[list[CanonicalClip], int]:
    """Paginate cached clips for a catalogue.

    `q` is a case-insensitive substring matched against `name` and the
    `notes` field of the cached blob. Returns reconstructed
    CanonicalClip rows and the total matching count.
    """
```

The adapter's offline `list_clips` path calls this instead of the
page-key lookup used in online mode.

### 3.5 ConnectionMonitor changes (`backend/app/services/connection_monitor.py`)

State machine becomes explicit. New methods / behavior:

- `state: Literal["online", "offline", "forced_offline"]` — replaces the
  implicit "ok/not ok" bool. `is_online()` returns `state == "online"`.
- `start()` (auto mode):
  - Run one probe.
  - On success → `state = online`, start probe loop.
  - On failure → `state = offline`, **do not start loop.**
- `start()` (forced mode, when `forced_offline=True` passed to ctor):
  - Set `state = forced_offline`. No probe, no loop.
- Probe loop body: on failure, set `state = offline` and `break` out of
  the loop — does not reschedule.
- `retry_now() -> ProbeResult`: runs one probe.
  - If `state == forced_offline` → returns `ForcedOfflineResult` without
    probing.
  - On success → `state = online`, **restart** probe loop.
  - On failure → stays `offline`, returns the error detail.

### 3.6 SyncEngine guard

Existing `SyncEngine.tick()` gains a single line near the top:

```python
if not self._connection_monitor.is_online():
    return
```

(`SyncEngine` already holds a `connection_monitor` reference per its
ctor.) On the tick after `retry_now()` flips to `online`, the engine
drains as usual.

### 3.7 Proxy resolver (`backend/app/services/proxy_resolver.py`)

New `LocalCacheOnlyResolver`:

```python
class LocalCacheOnlyResolver:
    is_host_local = False
    async def resolve(self, clip_id: str) -> Path:
        row = await self._proxy_cache_repo.get(self._db, clip_id)
        if row is None or not Path(row["local_path"]).exists():
            raise ProxyNotFound(f"clip {clip_id} not cached locally")
        return Path(row["local_path"])
```

`build_resolver(...)` gains a top-level branch:

```python
if settings.catdv_offline:
    return LocalCacheOnlyResolver(proxy_cache_repo, db_provider)
# else: existing rest/filesystem branches
```

`MediaPrefetcher` is wired only when the resolver is `not None and` the
resolver supports network fetches — in offline mode we skip its creation
(can't prefetch over a connection we don't have).

### 3.8 API + UI

**New endpoint:** `POST /api/connection/retry`
- Calls `connection_monitor.retry_now()`.
- Returns `{state, detail, latency_ms}` (200 on success-or-stays-offline)
  or `409` when `forced_offline`.

**Health endpoint:** `GET /api/health` already exists; add `mode` field
(`"online" | "offline" | "forced_offline"`) sourced from the monitor.

**Templates** — small additions:
- A shared partial `_connection_chip.html` rendered in the topbar.
  HTMX-friendly: clicking the yellow chip swaps it for a spinner, posts
  to `/api/connection/retry`, then swaps the response back in.
- Conditional hides via a single `mode_is_online` template var passed
  from the route layer.
- New 404 partial for the clip detail "not available offline" case.

### 3.9 Files touched (rough)

```
backend/app/settings.py                                          (+1 field)
backend/app/context.py                                           (branch)
backend/app/archive/providers/catdv/adapter.py                   (3 reads + write + stale helpers)
backend/app/repositories/clip_cache.py                           (+list_by_catalog)
backend/app/services/connection_monitor.py                       (state machine)
backend/app/services/sync_engine.py                              (+1 guard line)
backend/app/services/proxy_resolver.py                           (+LocalCacheOnlyResolver, branch)
backend/app/routes/connection.py                                 (new — retry endpoint)
backend/app/routes/health.py                                     (+mode field)
backend/app/templates/_connection_chip.html                      (new)
backend/app/templates/clips.html, clip_detail.html               (hide actions, banner)
.env.example                                                     (+CATDV_OFFLINE doc)
docs/DEPLOY.md                                                   (offline-mode section)
```

No DB migration. No schema change.

---

## 4. Out-of-scope decisions deferred

- **Annotation queue while offline.** Currently the Annotate button is
  hidden. A future enhancement could allow queuing annotation *intent*
  for replay on reconnect, but that requires deciding what "queued
  annotation" looks like in the prompt-management UI and is non-trivial.
- **Pre-warming the cache before going offline.** A "Cache catalogue for
  offline use" bulk action would be useful but is orthogonal — the
  existing per-clip "Cache locally" action and `MediaPrefetcher` queue
  already cover the manual path.
- **Multi-catalogue offline.** The user currently sees one catalogue
  (881507 / "AI katalog"). If that changes, list-from-cache filtering
  by `catalog_id` already does the right thing.

---

## 5. Testing approach

### 5.1 Adapter (`tests/archive/providers/catdv/test_adapter_offline.py`)

- `is_online=False` + fresh cache → returns fresh.
- `is_online=False` + stale cache → returns stale.
- `is_online=False` + no cache → `NotFound` (get_clip) or empty page (list_clips).
- `is_online=True` + `RetryableError` + stale present → returns stale.
- `is_online=True` + `RetryableError` + no cache → re-raises.
- `apply_changes` while offline → raises `RetryableError`, no client call.

### 5.2 ClipCacheRepo (`tests/repositories/test_clip_cache_list_by_catalog.py`)

- Pagination (`offset`, `limit`, `total`).
- Catalogue filter (rows in other catalogue excluded).
- Substring search on `name` (case-insensitive).
- Substring search on `notes` (case-insensitive).
- Returns reconstructed `CanonicalClip` objects.

### 5.3 ConnectionMonitor (`tests/services/test_connection_monitor_manual.py`)

- Startup probe fail → state `offline`, probe loop never started (assert
  no further provider.health() calls after the first).
- `forced=True` → state `forced_offline`, no probe attempted.
- `retry_now()` from `offline` + success → flips `online`, loop running
  again.
- `retry_now()` from `offline` + failure → stays `offline`.
- `retry_now()` from `forced_offline` → returns forced result, no probe.
- Mid-session probe failure → flips `offline`, loop halts on next tick.

### 5.4 SyncEngine (extend `tests/services/test_sync_engine.py`)

- Pending op + monitor `offline` → tick returns early, op unchanged, no
  `apply_changes` call.
- Monitor flips `offline → online` → next tick drains.

### 5.5 LocalCacheOnlyResolver (`tests/services/test_local_cache_only_resolver.py`)

- DB row + file on disk → returns path.
- DB row + file missing → raises `ProxyNotFound`.
- No DB row → raises `ProxyNotFound`.
- Constructed without a CatDV client (asserts on absence).

### 5.6 Integration smoke (`tests/integration/test_offline_mode.py`)

- `catdv_offline=True` build:
  - `GET /api/health` → `{mode: "forced_offline"}`.
  - `GET /clips` renders cached subset.
  - `GET /clips/<not-cached-id>` → 404 "not available offline".
  - `POST /api/connection/retry` → 409.
- `catdv_offline=False` + CatDV stub that fails:
  - Initial probe fails → mode = `offline`.
  - `GET /clips` returns cached rows.
  - `POST /api/connection/retry` with stub now succeeding → mode = `online`.

### 5.7 Existing test adjustments

- Any test asserting `ConnectionMonitor` keeps probing after failure
  needs updating to expect halt-after-fail.
- Adapter tests constructed without `is_online_provider` continue to
  work (default `None` = always-online).

---

## 6. Rollout

1. Land settings + `LocalCacheOnlyResolver` + repo helper (no behavior
   change yet, no wire-up).
2. Land adapter `is_online_provider` plumbing (default `None`, no
   behavior change).
3. Land `ConnectionMonitor` state machine + `SyncEngine` guard + retry
   endpoint.
4. Wire `context.py` to pass the provider, gate `CATDV_OFFLINE`, swap
   the resolver. Add the UI chip + template hides.
5. Document in `docs/DEPLOY.md` and `README.md`.

Each step is independently testable and shippable.
