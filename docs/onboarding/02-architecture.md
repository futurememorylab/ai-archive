# 02 — Architecture

The app is a single FastAPI process serving HTML (Jinja + HTMX), plus
a handful of long-running background tasks. State is local: one SQLite
file plus a proxy cache directory. External I/O goes through two ports
— **ArchiveProvider** and **AIInputStore** — so the rest of the code
never talks to httpx, GCS, or the filesystem directly.

This page is the visual companion to
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) and
[`docs/CONTEXT.md`](../CONTEXT.md). Read those when you need precise
definitions; come back here when you want the picture.

## Layer map

The four code layers and what each owns. **Layer rules are enforced by
`import-linter` on every commit** (see
[`06-coding-standards.md`](./06-coding-standards.md)).

```mermaid
flowchart TB
    subgraph Browser["Browser at localhost:8765"]
        UI["Jinja + HTMX + Alpine + Tailwind<br/>vanilla JS player widget"]
    end

    subgraph Backend["FastAPI process"]
        R["routes/<br/>HTTP + Jinja templates<br/>(no SQL, no httpx)"]
        S["services/<br/>orchestration, queues,<br/>monitors, prefetchers"]
        Repo["repositories/<br/>raw SQL over aiosqlite<br/>(one module per table)"]
        Arch["archive/<br/>ports + adapters:<br/>ArchiveProvider, AIInputStore<br/>+ canonical dataclasses"]
        Mdl["models/<br/>Pydantic models<br/>for the app's own state"]
    end

    subgraph External
        DB[("SQLite app.db")]
        FSCache[("data/cache/proxies/")]
        CatDV["CatDV REST<br/>(VPN or loopback)"]
        GCS["GCS bucket"]
        Vertex["Vertex AI / Gemini"]
    end

    UI -- "HTML, SSE" --> R
    R --> S
    R -.allowed.-> Repo
    S --> Repo
    S --> Arch
    S --> Mdl
    Repo --> DB
    Arch --> CatDV
    Arch --> FSCache
    Arch --> GCS
    S --> Vertex
```

### Layer rules (from `.importlinter`)

| Rule | Forbidden direction |
|---|---|
| Routes must not reach into archive adapter internals | `routes → archive.providers / .registry / .ai_stores / .provider / .ai_store / .ai_store_model / .change_set_json` |
| Services must not import routes | `services → routes` |
| Models stay pure | `models → services / repositories / routes` |

Routes **may** call services or repositories directly (current practice
in `routes/jobs.py` and `routes/live.py`) — that's a deliberate "looser"
contract from the architecture plan. What routes must not do is talk
to a specific adapter; they go through the port instead.

## AppContext — the composition root

Everything stateful is wired once at startup into a single `AppContext`
dataclass (`backend/app/context.py`) and stashed on `app.state.ctx`.
Routes pull it via the typed `get_ctx` dependency
([ADR 0020](../adr/0020-typed-get-ctx-accessor.md)).

```mermaid
flowchart LR
    L["FastAPI lifespan"] --> B["AppContext.build()"]
    B --> M["apply migrations"]
    B --> Repos["instantiate repos"]
    B --> Ports["build ArchiveProvider<br/>+ AIInputStore via registries"]
    B --> Svc["instantiate services"]
    B --> St["start background tasks<br/>(connection_monitor, sync_engine,<br/>lru_eviction, media_prefetcher)"]
    St --> Y["yield to request loop"]
    Y --> C["ctx.aclose()<br/>(releases CatDV seat!)"]
```

The shutdown path is load-bearing: `aclose()` is what calls
`DELETE /catdv/api/9/session` and frees the seat. **Never SIGKILL the
process** — see [`05-catdv-license-discipline.md`](./05-catdv-license-discipline.md).

## End-to-end flow: annotate one clip

This is what happens when the operator clicks **Annotate ▾** on the
clip-detail page.

```mermaid
sequenceDiagram
    autonumber
    participant U as Browser
    participant R as routes/jobs
    participant A as services/annotator
    participant P as ArchiveProvider (CatDV)
    participant Pr as services/proxy_resolver
    participant G as services/gcs (AIInputStore)
    participant V as services/gemini (Vertex AI)
    participant D as repositories/annotations + review_items

    U->>R: POST /clips/:id/annotate (prompt_version_id)
    R->>A: start annotation
    A->>Pr: locate proxy
    alt rest mode
        Pr->>P: download via Range
        Pr-->>A: data/cache/proxies/<id>.mov
    else filesystem mode
        Pr-->>A: /Volumes/ARECA/.../<id>.mov
    end
    A->>G: upload once, get gs:// URI (idempotent by sha256)
    A->>V: generateContent(gs://..., output_schema)
    V-->>A: structured JSON
    A->>D: insert annotation + N review_items
    A-->>U: SSE status "Done", reload Draft tab
```

Accept/reject of `review_items` and pushing back to CatDV are the
follow-up step — they go through the **write queue**, below.

## Write queue (CatDV mutations)

Every accepted change becomes a `ChangeOp` row in `pending_operations`.
The `SyncEngine` background task drains them when `ConnectionMonitor`
says we're online.

```mermaid
stateDiagram-v2
    [*] --> pending: WriteQueue.enqueue(ChangeOp)
    pending --> in_flight: SyncEngine picks up
    in_flight --> applied: adapter PUT succeeded
    in_flight --> conflict: adapter detected stale state
    in_flight --> failed: non-retryable error
    in_flight --> pending: retryable error (backoff)
    applied --> [*]
    conflict --> [*]
    failed --> [*]
```

- **Enqueue is atomic with mark_applied** — the locus of conflict
  detection is the adapter itself, not the queue
  ([ADR 0004](../adr/0004-pr4-enqueue-atomic-conflict-locus-adapter.md)).
- Per-row retry backoff is configurable
  (`SYNC_RETRY_BASE_S`, `SYNC_RETRY_MAX_S`).
- Sync is paused entirely while `ConnectionMonitor` state is not
  `online`.

## Connection state machine

```mermaid
stateDiagram-v2
    [*] --> online: successful login at boot
    [*] --> offline: CATDV_OFFLINE=true (forced)
    online --> degraded: health probe failed once
    degraded --> online: probe recovered
    degraded --> offline: probes keep failing
    offline --> online: manual reconnect succeeds
    online --> syncing: SyncEngine draining
    syncing --> online: queue drained
```

The header pill in the UI reflects this state live (broadcast over the
`EventBus`). See
[ADR 0015](../adr/0015-offline-fallback-auto-degrade-manual-reconnect.md)
and [ADR 0017](../adr/0017-offline-mode-annotate-available-marker-nav-scope.md)
for the auto-degrade and offline-annotate-when-cached decisions.

## The three cache layers

The Cache Inspector (`services/cache_inspector.py`) is the single
read-side API across all three; the `/cache` UI page is its visualiser.

```mermaid
flowchart LR
    subgraph M["Metadata cache"]
        CC["clip_cache<br/>(canonical JSON)"]
        FDC["field_def_cache"]
        CLC["clip_list_cache"]
    end
    subgraph L["Media-local cache"]
        PC["proxy_cache (rows)"]
        Disk["data/cache/proxies/"]
    end
    subgraph AI["Media-AI cache"]
        ASF["ai_store_files<br/>(bucket URI + ETag)"]
        GCSb["GCS bucket"]
    end

    M -.reconciled at boot.-> PCR["ProxyCacheReconciler"]
    L --> LRU["LruEviction<br/>(unpinned only, never crosses a pin)"]
    PF["MediaPrefetcher<br/>(single-flight, FIFO)"] --> L
```

- **Pinning** lives in `workspace_clips` plus
  `clip_cache.pinned_to_workspace_id`. LRU eviction will never delete
  a pinned clip's bytes
  ([ADR 0006](../adr/0006-pr6-cache-layer-signals-audit-lru.md)).
- The **MediaPrefetcher** is intentionally single-threaded —
  parallelism is not a knob, because the slow VPN is the bottleneck
  ([ADR 0009](../adr/0009-pr8-media-prefetch-cache-ui-wiring.md)).
- Every cache mutation is audited to `cache_actions_log`.

## Where to dig deeper

| Question | Read |
|---|---|
| "What's a Workspace? What's a ClipKey?" | [`../CONTEXT.md`](../CONTEXT.md) |
| "Marker save returns 502 — where do I start?" | [`../ARCHITECTURE.md`](../ARCHITECTURE.md) symptom→file table |
| "Why is the schema shaped like this?" | The PR-N ADRs under [`../adr/`](../adr/) (0003–0007 cover the DB layout) |
| "How does the FS provider find proxies?" | [`../fs-archive-format.md`](../fs-archive-format.md) |
| "What did we learn writing Gemini Live?" | [`../gemini-live-lessons.md`](../gemini-live-lessons.md) |
