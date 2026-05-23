# CONTEXT — domain glossary

One sentence per noun. If reading a source file sends you bouncing between
four others, the missing definition probably belongs here.

## Archive identity

- **Clip** — a CatDV (or FS-sidecar) media asset; uniquely identified by
  the pair `(provider_id, provider_clip_id)` and projected into the
  app as `CanonicalClip` in `backend/app/archive/model.py`.
- **ClipKey** — the `(provider_id, provider_clip_id)` tuple that every
  cache row, write queue row, and AI-store upload is keyed by.
- **Archive Provider** — port for "where clips and their metadata live,"
  satisfied today by the CatDV REST adapter
  (`archive/providers/catdv/`) or the filesystem-sidecar adapter
  (`archive/providers/fs/`); the Protocol is `ArchiveProvider` in
  `archive/provider.py`.
- **Provider Capabilities** — declarative flags (`media_is_local`,
  `supports_markers`, `write_atomicity`, …) the rest of the app
  branches on instead of doing `isinstance` checks against an adapter.
- **AI Input Store** — port for "where Gemini reads the media bytes
  from," defined in `archive/ai_store.py`; today GCS (production) and
  a Gemini Files stub, selected via the `ai_input_store` setting.
- **Field Definition** — the upstream CatDV custom-field schema
  (`FieldDef` in `archive/model.py`); cached locally in
  `field_def_cache` so the UI can render forms while offline.

## Local state

- **Workspace** — a user-curated, named pinned subset of clips plus
  their proxies; lifecycle (`create → add_clips → prepare() →
  release()`) is driven by `WorkspaceManager`, persisted via
  `WorkspacesRepo` and the `workspaces` / `workspace_clips` tables.
- **Pin** — a row in `workspace_clips` plus the *primary*
  `clip_cache.pinned_to_workspace_id` FK; a pin means LRU eviction
  may not delete the clip's bytes.
- **Proxy Cache** — local on-disk H.264 `.mov` copies of CatDV web
  proxies under `data/cache/proxies/`; `ProxyCacheRepo`
  (`repositories/proxy_cache.py`) is the index, `proxy_resolver`
  (`services/proxy_resolver.py`) is the read API, and
  `ProxyCacheReconciler` (`services/proxy_cache_reconciler.py`)
  reconciles index against disk at every startup.
- **Clip Cache** — local SQLite mirror of canonical clip JSON, used
  for offline reads and as the input to the cache inspector;
  `ClipCacheRepo` / table `clip_cache`.
- **AI Store Files** — per-clip rows recording an `AIInputStore`
  upload (bucket URI, ETag, last_used_at); index for the
  media-ai cache layer.

## Writes and connectivity

- **Write Queue** — durable journal of pending mutations to the
  archive (add-markers, set-field, append/replace-note);
  `WriteQueue` (`services/write_queue.py`) enqueues `ChangeOp` rows
  into `pending_operations`, `SyncEngine` drains them.
- **ChangeSet / ChangeOp** — the canonical write payload the adapter
  applies (`archive/model.py`); ops are grouped per-clip into one
  `ChangeSet` per drain tick.
- **Sync Engine** — background task (`services/sync_engine.py`) that
  drains the write queue when `ConnectionMonitor` says we're online;
  honours per-row retry backoff, marks `conflict` / `failed`
  terminally, and is paused while state is not `online`.
- **Connection Monitor** — periodic provider health probe with a
  small state machine (`online` / `degraded` / `offline` / `syncing`)
  plus two manual-override flags; lives in
  `services/connection_monitor.py` and broadcasts transitions on the
  `EventBus` so the header pill updates live.
- **Forced offline** — boot-time flag (`CATDV_OFFLINE=true`) that
  prevents a CatDV login at all (no seat taken); distinct from
  **manual offline**, which is a runtime toggle from the connection
  pill.
- **Pending Operation** — one row in `pending_operations`; the smallest
  unit the SyncEngine reads, attempts, and resolves. Status flows
  `pending → in_flight → applied | conflict | failed`, or back to
  `pending` on a retryable error.

## AI annotation pipeline

- **Prompt** — long-lived identity (name + description) for an
  annotation template; one Prompt has many Versions.
- **Prompt Version** — snapshot of editable content
  (`body + target_map + output_schema + model`) plus a state
  (`draft` / `production` / `archived`); at most one version per
  prompt is `production`, and `production` / `archived` versions are
  immutable (enforced by `PromptsRepo` + a partial unique index).
- **Target Map** — per-version routing table that says "field X in the
  Gemini JSON output becomes a marker / a CatDV field / a note
  target"; defined in `models/prompt.py`.
- **Annotation** — a single Gemini run against one clip with one
  prompt version; produces a row in `annotations` plus N
  `review_items` rows the user can accept/reject.
- **Review Item** — one proposed marker / field / note edit, awaiting
  user accept/reject; once accepted and "applied," it becomes one
  or more `ChangeOp`s on the write queue.
- **Live Session** — a single browser-direct Gemini Live conversation
  about one clip; audio bytes go straight from the browser to
  Google over WSS (no backend bridge), and we persist a row in
  `live_sessions` plus a transcript JSON when the session ends.

## Caches, evictions, and prefetch

- **Cache Inspector** — read-only, per-clip view across the three
  cache layers (metadata / media-local / media-ai);
  `services/cache_inspector.py` is the single entry point the UI
  and the inline badges use.
- **Cache Actions** — write side of the cache layers (evict-local,
  evict-ai, refresh-metadata); enforces the spec §9.5 invariants
  (e.g. "won't evict a pinned clip without `force=True`") and writes
  every attempt to `cache_actions_log`.
- **LRU Eviction** — periodic background sweep
  (`services/lru_eviction.py`) that evicts the least-recently-used
  *unpinned* local-media rows once total non-pinned size exceeds
  `media_cache_cap_gb`; never crosses a pin.
- **Media Prefetcher** — single-flight background download worker
  (`services/media_prefetcher.py`) that drains `prefetch_queue` in
  FIFO order; designed for the WireGuard pipe to Pragafilm, so
  parallelism is deliberately not a knob.
- **Cache Actions Log** — audit table for every cache mutation
  (who, action, result, reason); the source of truth for the
  `/cache` page activity timeline.
