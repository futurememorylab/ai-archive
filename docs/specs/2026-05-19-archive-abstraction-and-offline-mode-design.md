# Archive Abstraction & Offline Mode — Design Spec

**Date:** 2026-05-19
**Status:** Draft, awaiting review
**Author:** Peter Hora (with Claude)
**Supersedes (partially):** sections of `2026-05-18-catdv-annotator-design.md` that
hard-wire CatDV semantics into the worker, repositories, and SQL schema.

---

## 1. Motivation

The 2026-05-18 design treats CatDV as the only possible archive backend and treats
the live CatDV server as always reachable. Two requirements force a re-think:

1. **Archive portability.** CatDV is not the only MAM in use; we want the same
   annotation engine to drive other archives (ResourceSpace, AVID Interplay,
   Adobe Bridge sidecars, a plain-filesystem archive with JSON sidecars,
   FileMaker-backed catalogues, etc.) without re-writing the worker, the review
   pane, or the data model.
2. **Offline operation.** The user must be able to connect to the archive, cache
   a working set of clips and their proxies, disconnect (no VPN, on a train,
   archive server down), continue annotation and review work, then reconnect and
   push accumulated changes back.

Both requirements are surface symptoms of the same architectural defect: the app
treats the archive as a transparent backing store rather than as a *remote
system* that may be slow, unreachable, or substitutable. The single fix is to
introduce a port–adapter boundary and a local-first write model around it.

### Non-goals

- Multi-user collaboration, conflict resolution between human users, OT/CRDT.
- General-purpose distributed sync (no e.g. CouchDB-style replication).
- A second archive adapter that is itself a full MAM (ResourceSpace, Interplay).
  v2 ships one *additional* adapter — a filesystem archive — solely to prove
  the abstraction. Further adapters are downstream projects.
- A graphical workspace/cache manager UX. The first cut is a small set of
  controls inside the existing two-pane UI.
- Re-architecting Gemini/Vertex itself. Vertex still requires connectivity;
  jobs queue while offline and run on reconnect (see §7.4). The store *into
  which* media gets uploaded for Gemini is abstracted (§6) but Vertex remains
  the inference engine.

### What this spec does NOT touch

The 2026-05-18 spec remains the authority for: the AppContext/DI pattern, the
review pane player, the GCP setup, the Gemini service shape, the testing
philosophy, and the deployment model. This spec changes the **archive boundary**
and the **write path**, and adds a **sync engine** and **workspace** concept.
Everything else inherits.

---

## 2. The core idea

> **The local SQLite store is the source of truth for the working session. The
> archive is an upstream we sync with.**

Two consequences follow:

1. **The archive sits behind a port.** All app code uses `ArchiveProvider`
   plus a canonical domain model (`Clip`, `Marker`, `FieldValue`, `ChangeSet`).
   CatDV becomes one adapter implementing that port. Adding another archive is
   writing a new adapter, not editing routes/services.
2. **Apply enqueues, never directly writes.** When the user accepts review items
   for a clip and clicks **Apply**, the app records a typed operation in a
   `pending_operations` journal. A `SyncEngine` drains the journal against the
   active provider, immediately if online, eventually if offline. The Apply code
   path is identical online and offline.

These two consequences together give us archive portability *and* offline mode
from the same machinery.

---

## 3. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│ Browser (HTMX + Alpine + player.js, as before)                       │
│   Adds: connection-state pill, sync drawer, workspace switcher       │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
┌────────────────────────────▼─────────────────────────────────────────┐
│ FastAPI app                                                          │
│                                                                      │
│  routes/  (unchanged set; payloads now use canonical Clip)           │
│                                                                      │
│  services/                                                           │
│   • annotator             — Gemini job worker (as before)            │
│   • write_queue           — enqueue typed operations                 │
│   • sync_engine           — drain queue against active provider      │
│   • connection_monitor    — health probe + state transitions         │
│   • workspace_manager     — pin clips, pre-cache media + metadata    │
│   • media_cache           — local file store, LRU                    │
│   • clip_cache            — local SQLite mirror of clip metadata     │
│   • cache_inspector       — unified per-clip view of all cache locs  │
│                                                                      │
│  archive/                                                            │
│   • ArchiveProvider (Protocol)         — port: read+write to archive │
│   • AIInputStore (Protocol)            — port: Gemini's input source │
│   • CanonicalClip, ChangeSet, etc.     — domain model                │
│   • providers/catdv/    — CatDV adapter (wraps existing CatdvClient) │
│   • providers/fs/       — Filesystem adapter (v2)                    │
│   • ai_stores/gcs/      — GCS-backed AI input store (default)        │
│   • ai_stores/gemini_files/ — Gemini Files API store (optional)      │
│                                                                      │
│  AppContext owns active archive provider, active AI input store,     │
│  write_queue, sync_engine, connection_monitor, cache_inspector.      │
└──┬───────────────────────────────────────────────────────────────┬───┘
   │                                                               │
   │ SQLite (local-first source of truth during session)           │
   │   + media_cache directory                                     │
   │                                                               │
   ▼                                                               ▼
┌──────────────────────────────────┐   ┌──────────────────────────────┐
│ Active archive provider          │   │ Active AI input store        │
│   CatDV adapter today            │   │   GCS today (default)        │
│   FS adapter v2                  │   │   Gemini Files API (option)  │
│   Other adapters in future       │   │ Vertex AI Gemini (inference) │
└──────────────────────────────────┘   └──────────────────────────────┘
```

### Why "active" provider (single, not multi)

In v1/v2 the app talks to **one** archive at a time, selected at startup by
config. Multi-archive in the same installation is explicitly deferred: it would
require cross-provider clip ID disambiguation in the UI, dual auth state, and
sync arbitration. None of that helps the immediate goals. The schema is built
so that multi-provider *can* be added later without a migration churn (see §4),
but the runtime is single-provider.

---

## 4. Canonical domain model

A small, opaque-extensible set of types the app uses everywhere. Lives in
`backend/app/archive/model.py`.

```python
ProviderId       = str   # "catdv", "fs", future: "resourcespace", etc.
ProviderClipId   = str   # opaque to the app; adapter-specific shape
ClipKey          = tuple[ProviderId, ProviderClipId]   # globally unique

@dataclass(frozen=True)
class Timecode:
    secs: float
    fps: float
    frm: int | None = None        # filled by adapter if available
    txt: str | None = None        # SMPTE, filled by adapter if available

@dataclass(frozen=True)
class Marker:
    name: str
    in_:  Timecode
    out:  Timecode | None
    description: str | None = None
    category:    str | None = None
    color:       str | None = None

@dataclass(frozen=True)
class FieldValue:
    identifier: str               # provider-namespaced when needed
    value: Any                    # scalar | list[scalar] | str
    is_multi: bool = False

@dataclass(frozen=True)
class MediaRef:
    """How to obtain the playable bytes for this clip."""
    mime_type: str
    size_bytes: int | None
    cached_path: Path | None       # set if locally cached
    upstream_handle: str           # adapter-specific (e.g. CatDV clip_id, fs abspath)

@dataclass(frozen=True)
class CanonicalClip:
    key: ClipKey
    name: str
    duration_secs: float
    fps: float
    markers: list[Marker]
    fields: dict[str, FieldValue]
    notes: dict[str, str]          # name -> text (e.g. "notes", "bigNotes")
    media: MediaRef
    provider_data: dict[str, Any]  # opaque raw upstream representation
    fetched_at: datetime

@dataclass(frozen=True)
class FieldDef:
    identifier: str
    name: str
    type: Literal["text","integer","decimal","date","picklist","multi-picklist","bool"]
    is_multi: bool
    is_editable: bool
    picklist_values: list[str] | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)
```

### Why `provider_data`

The CatDV adapter must round-trip every field of the upstream JSON when it
writes back (PUT replaces unspecified arrays). Rather than expanding the
canonical model to cover every backend's quirks, each `CanonicalClip` carries
an opaque blob. The adapter is the only thing allowed to read it; app code
treats it as bytes. New adapters define their own shape.

### ChangeSet (the write contract)

```python
@dataclass(frozen=True)
class AddMarkers:    markers: list[Marker]
@dataclass(frozen=True)
class SetField:      identifier: str; value: Any
@dataclass(frozen=True)
class AppendNote:    target: str; text: str
@dataclass(frozen=True)
class ReplaceNote:   target: str; text: str

ChangeOp = AddMarkers | SetField | AppendNote | ReplaceNote

@dataclass(frozen=True)
class ChangeSet:
    clip_key: ClipKey
    ops: list[ChangeOp]
    expected_etag: str | None     # if provider supports optimistic concurrency
```

`ChangeSet` is what the WriteQueue persists and what `apply_changes()` accepts.
It is intentionally not "the new clip state" — that would require the adapter
to diff, and would lose intent (e.g. "append" vs "replace").

---

## 5. The ArchiveProvider port

```python
class ArchiveProvider(Protocol):
    id: ProviderId                              # "catdv", "fs", ...
    capabilities: ProviderCapabilities          # see below

    async def health(self) -> ProviderHealth: ...
    async def list_catalogs(self) -> list[Catalog]: ...
    async def list_clips(
        self, catalog: CatalogId, query: ClipQuery
    ) -> ClipPage: ...
    async def get_clip(self, clip: ProviderClipId) -> CanonicalClip: ...
    async def fetch_media(
        self, clip: ProviderClipId, *, dest: Path,
        progress: Callable[[int, int | None], None] | None = None
    ) -> Path: ...
    async def list_field_definitions(self) -> list[FieldDef]: ...
    async def apply_changes(self, change_set: ChangeSet) -> WriteResult: ...
```

```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_markers:   bool
    supports_notes:     set[str]              # names of writable note fields
    supports_field_create: bool               # rare; CatDV admin-only
    supports_etag:      bool                  # optimistic concurrency
    media_is_local:     bool                  # true → fetch_media is a no-op stat
    write_atomicity:    Literal["per-clip","per-op"]
```

The annotator and review pane query `capabilities` to decide what to allow
(e.g. hide "create field definition" if the provider doesn't support it).

### CatDV adapter notes

`providers/catdv/adapter.py` wraps the existing `CatdvClient`. It maps:

- `apply_changes()` → fetch current clip, run `payload_builder.build_put_payload`
  against the requested ops, `PUT /clips/{id}`. This is exactly today's apply
  path, moved inside the adapter where it belongs (CatDV-specific quirk: PUT
  replaces markers wholesale).
- `fetch_media()` → today's `download_proxy`, into the cache directory.
- `list_field_definitions()` → today's `GET /catdv/api/9/fields`, mapped to
  `FieldDef`.
- Capabilities: markers ✓; notes = `{"notes","bigNotes", + writable
  pragafilm text fields}`; field_create ✗ (admin only); etag ✗
  (CatDV envelope has no version); media_is_local depends on `PROXY_SOURCE`
  (true in filesystem mode); write_atomicity = `"per-clip"`.

### Filesystem adapter (v2; proves the abstraction)

`providers/fs/adapter.py` walks a directory tree configured by `FS_ROOT`:

- One subdir per "catalog"; one file per clip (`.mov`, `.mp4`, ...) with a
  `<clipname>.annot.json` sidecar holding markers, fields, notes.
- `apply_changes()` writes a `<clipname>.annot.json.tmp`, fsyncs, renames
  (POSIX atomic). etag = sha256 of sidecar → real optimistic concurrency.
- Field definitions: declared in `FS_ROOT/.archive/fields.json`.
- media_is_local: true. fetch_media is a path resolution + stat.

This adapter is the v2 milestone *because* it forces every CatDV-shaped
assumption out of the app code. If it works, the port boundary is real.

---

## 6. AI input store (Gemini's view of media)

Symmetric to `ArchiveProvider`, but for the *other* direction: where Gemini
reads media bytes from. The reason this is its own port is that **the archive
adapter and the AI store have nothing to do with each other** — they answer
different questions:

- `ArchiveProvider`: where is the canonical, edited-by-humans archive that we
  read clips from and write annotations back to?
- `AIInputStore`: where do we put a copy of the media bytes so that Vertex AI
  Gemini can read them?

Conflating the two is exactly the bug we are trying to avoid. A clip's bytes
can live on a CatDV server (archive), on the annotator host's local disk
(local cache, optional), *and* in a GCS bucket (AI input). All three are
legitimate and independent.

### 6.1 The port

```python
@dataclass(frozen=True)
class UploadedRef:
    handle: str            # provider-specific: gs://bucket/clips/x.mov | files/abc123
    mime_type: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    expires_at: datetime | None   # None = no auto-expiry

class AIInputStore(Protocol):
    id: str                        # "gcs:<bucket>" | "gemini-files"
    capabilities: AIStoreCapabilities

    async def ensure_uploaded(
        self, clip_key: ClipKey, local_path: Path, mime: str
    ) -> UploadedRef: ...
    async def status(self, clip_key: ClipKey) -> UploadedRef | None: ...
    async def evict(self, clip_key: ClipKey) -> None: ...
    async def health(self) -> StoreHealth: ...
    async def reference_for_gemini(self, ref: UploadedRef) -> dict: ...
      # Returns the SDK-shaped {"file_data": {"file_uri": ...}} or
      # {"file_data": {"file_id": ...}} fragment for generate_content().

@dataclass(frozen=True)
class AIStoreCapabilities:
    persistent: bool            # GCS = True; Gemini Files = False (48h TTL)
    dedup_by_sha256: bool       # GCS adapter does this; Gemini Files does not
    max_file_bytes: int         # 2 GB for Files API; effectively unlimited for GCS
```

Why a Protocol rather than just a `GcsService` rename: the v1 spec already
hinted at the question ("could we skip GCS?"). Making `AIInputStore` a port
means switching to Gemini Files API later is one adapter, not a rewrite of
`annotator`. The Gemini SDK's `generate_content()` accepts both shapes; the
adapter knows which shape its store produces (`reference_for_gemini`).

### 6.2 Default: GcsInputStore

Today's behavior, moved behind the port. Wraps `services/gcs.py`. Persistent
storage, dedup by sha256, `gcs_files` table tracks `(provider_id,
provider_clip_id) → (gs://uri, sha256, size, uploaded_at, last_used_at)`. A
single row per clip per bucket. `ensure_uploaded()` is the existing
"upload-if-absent" path; `evict()` deletes the blob and clears the row.

### 6.3 Optional: GeminiFilesInputStore

For installs that don't want a GCS bucket (e.g. a hobbyist running locally
against the consumer Gemini API). Uses `client.files.upload(local_path)`.
Files auto-expire after 48 h, so `ensure_uploaded()` re-uploads whenever
`status()` returns expired/None. No long-term storage cost; no dedup. Capped
at 2 GB per file. **Not** wired in v2 — port is defined, adapter is a stub
with NotImplementedError. We ship it when a real user asks. The point of
defining it now is to validate the port shape.

### 6.4 What the annotator does

```python
# in services/annotator.py
local_path = await archive.fetch_or_resolve_media(clip_key)   # adapter-aware
upload     = await ai_store.ensure_uploaded(clip_key, local_path, mime)
ref        = await ai_store.reference_for_gemini(upload)
result     = await gemini.annotate(ref, prompt, schema, model)
```

No mention of GCS, no mention of `gs://`. The annotator only knows there is
an AI input store; the store knows how to produce something Gemini can read.

### 6.5 Decoupling from the archive

`AIInputStore` is per-installation, not per-provider. A CatDV install and a
filesystem-archive install can both point at the same GCS bucket; the
`(provider_id, provider_clip_id)` key keeps their entries separate. If you
re-pin the same media bytes from two different archive providers, they
upload twice (different keys), since "same sha256 across providers" is rare
and reasoning about identity collapse across providers introduces more
trouble than it saves.

---

## 7. Local-first write path

### 7.1 Tables (additions / migrations)

```sql
-- Provider-aware clip identity (replaces catdv_clip_id INTEGER everywhere).
-- Migration: keep catdv_clip_id column for now, populate provider_id='catdv'
-- and provider_clip_id=str(catdv_clip_id) for existing rows; later migration
-- drops catdv_clip_id once code is fully cut over.
ALTER TABLE annotations    ADD COLUMN provider_id TEXT;
ALTER TABLE annotations    ADD COLUMN provider_clip_id TEXT;
ALTER TABLE review_items   ADD COLUMN provider_id TEXT;
ALTER TABLE review_items   ADD COLUMN provider_clip_id TEXT;
ALTER TABLE job_items      ADD COLUMN provider_id TEXT;
ALTER TABLE job_items      ADD COLUMN provider_clip_id TEXT;
ALTER TABLE proxy_cache    ADD COLUMN provider_id TEXT;
ALTER TABLE proxy_cache    ADD COLUMN provider_clip_id TEXT;
ALTER TABLE gcs_files      ADD COLUMN provider_id TEXT;
ALTER TABLE gcs_files      ADD COLUMN provider_clip_id TEXT;
ALTER TABLE write_log      ADD COLUMN provider_id TEXT;
ALTER TABLE write_log      ADD COLUMN provider_clip_id TEXT;

-- Clip metadata cache (the local "mirror" of upstream clip state).
CREATE TABLE clip_cache (
  provider_id      TEXT NOT NULL,
  provider_clip_id TEXT NOT NULL,
  name             TEXT NOT NULL,
  catalog_id       TEXT NOT NULL,
  duration_secs    REAL NOT NULL,
  fps              REAL NOT NULL,
  canonical_json   TEXT NOT NULL,        -- serialized CanonicalClip
  provider_etag    TEXT,                  -- if provider supplies one
  fetched_at       TEXT NOT NULL,
  pinned_to_workspace_id INTEGER,         -- nullable; FK to workspaces
  PRIMARY KEY (provider_id, provider_clip_id)
);
CREATE INDEX idx_clip_cache_catalog ON clip_cache(provider_id, catalog_id);

-- Provider field definitions cache.
CREATE TABLE field_def_cache (
  provider_id   TEXT NOT NULL,
  identifier    TEXT NOT NULL,
  json          TEXT NOT NULL,            -- serialized FieldDef
  fetched_at    TEXT NOT NULL,
  PRIMARY KEY (provider_id, identifier)
);

-- The journal of pending writes.
CREATE TABLE pending_operations (
  id               INTEGER PRIMARY KEY,
  provider_id      TEXT NOT NULL,
  provider_clip_id TEXT NOT NULL,
  op_kind          TEXT NOT NULL,         -- AddMarkers | SetField | AppendNote | ReplaceNote
  op_json          TEXT NOT NULL,         -- serialized op
  origin_annotation_id INTEGER REFERENCES annotations(id),
  origin_review_item_ids TEXT,            -- JSON array, for back-link UI
  expected_etag    TEXT,
  status           TEXT NOT NULL,         -- pending | in_flight | applied | conflict | failed
  attempts         INTEGER NOT NULL DEFAULT 0,
  last_error       TEXT,
  enqueued_at      TEXT NOT NULL,
  attempted_at     TEXT,
  applied_at       TEXT
);
CREATE INDEX idx_pending_ops_status ON pending_operations(status, enqueued_at);

-- Workspaces: named pinned working sets.
CREATE TABLE workspaces (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  provider_id TEXT NOT NULL,
  catalog_id  TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  description TEXT
);

-- workspace ↔ clip membership; one clip can belong to multiple workspaces.
CREATE TABLE workspace_clips (
  workspace_id      INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  provider_id       TEXT NOT NULL,
  provider_clip_id  TEXT NOT NULL,
  added_at          TEXT NOT NULL,
  cache_state       TEXT NOT NULL,        -- pending | metadata | media | ready | error
  cache_error       TEXT,
  PRIMARY KEY (workspace_id, provider_id, provider_clip_id)
);

-- Connection-state log (audit + UI history).
CREATE TABLE connection_events (
  id          INTEGER PRIMARY KEY,
  state       TEXT NOT NULL,              -- online | offline | degraded | syncing
  detail      TEXT,
  at          TEXT NOT NULL
);
```

The existing `proxy_cache` table covers media files and gains a `provider_id` /
`provider_clip_id` pair. No new media table; the resolver code from §6 of the
2026-05-18 spec now lives behind the adapter (CatDV adapter owns its REST proxy
resolver; FS adapter has no cache layer because media is already local).

### 7.2 Apply, end to end

```
User: clicks "Apply accepted" on clip C
  │
  ▼
WriteQueueService.enqueue_apply(clip_key=C, accepted_review_items=[…])
  │ - groups items into ChangeOps:
  │     marker items   → AddMarkers(markers=[…])
  │     field items    → one SetField per identifier
  │     note items     → AppendNote/ReplaceNote per target (mode from target_map)
  │ - writes one pending_operations row per ChangeOp
  │ - returns immediately (UI shows "queued")
  │
  ▼
SyncEngine.tick()  (fires on enqueue + on connection_monitor change + every N s)
  │ - if connection_state != online: return (queue stays)
  │ - for each provider with pending ops:
  │     for each clip with pending ops (ordered by enqueued_at):
  │       1. provider.get_clip(clip) → refresh clip_cache, store etag
  │       2. Build a single ChangeSet from this clip's pending ops
  │       3. provider.apply_changes(change_set)
  │            on OK:       mark ops applied; write write_log entry; emit SSE
  │            on CONFLICT: mark ops conflict; surface in UI; user resolves
  │            on RETRYABLE:attempts++; backoff; leave pending
  │            on FATAL:    mark ops failed; surface error
```

Per-clip batching matters: CatDV PUT is per-clip and replaces arrays, so we
must collapse all queued ops for a clip into one upstream write. Other adapters
with finer atomicity get the same batch but can choose to send op-by-op.

### 7.3 ConnectionState

```python
class ConnectionState(StrEnum):
    online   = "online"     # last health probe OK and recent
    degraded = "degraded"   # provider reachable but slow / partial failures
    offline  = "offline"    # provider unreachable
    syncing  = "syncing"    # transient, set during drain
```

`ConnectionMonitor` runs a periodic `provider.health()` (cheap; CatDV → `GET
/catdv/api/info`, FS → directory stat). State changes are persisted to
`connection_events` and pushed to the UI via SSE. Manual override exists:
"Work offline" pins state to `offline` until the user toggles it back, so the
user can deliberately disconnect from VPN without the app retrying every second.

### 7.4 Annotation jobs while offline

The Gemini job worker requires connectivity to Vertex AI regardless of archive
state. Behavior:

- Job submission while offline-to-archive is allowed **only** for clips already
  cached in the active workspace (we need media + clip_snapshot locally).
- The job worker depends on connectivity to GCS/Vertex, not to CatDV. If GCS
  is reachable but CatDV is not, jobs run; resulting `review_items` queue
  normally; applies wait for archive to come back.
- A "pending jobs" view shows jobs that are waiting for GCS connectivity (this
  is a small additional state; the worker checks `gcs_health()` before
  starting an item).

Vertex AI / GCS are not abstracted in this spec. The "offline" semantics are
about the archive boundary; AI is a separate dependency with its own queue
gate. Future work: a fully-offline mode where annotations also queue and run
later is plausible but out of scope.

---

## 8. Workspaces (the user-facing "offline" surface)

A *workspace* is a named pinned subset of an archive's clips, with their
metadata and media guaranteed cached locally. Workspaces are the primitive the
user manipulates when they want to go offline.

### 8.1 Lifecycle

```
1. User creates a workspace, picks a catalog, multi-selects clips
   ("Archive 30s home movies — train trip").
2. WorkspaceManager.prepare(ws_id):
     for each clip:
       provider.get_clip()         → clip_cache
       provider.fetch_media()      → media_cache (skip if media_is_local)
     mark workspace_clips.cache_state=ready
   Progress shown via SSE; user can cancel; partial workspaces are usable.
3. User toggles "Work offline". connection_monitor pins state to offline.
4. User browses, annotates, reviews, applies. All writes queue.
5. User toggles back to "Online". sync_engine drains; UI shows progress;
   conflicts (if any) presented one by one.
6. User can release the workspace (drops pins; media/clip cache become
   subject to LRU eviction).
```

### 8.2 Cache budget

Two budgets, both configurable:

- `MEDIA_CACHE_CAP_GB` (default 50) — total media cache size.
  Pinned-to-workspace media is **exempt from LRU eviction** until released.
- `CLIP_CACHE_TTL_HOURS` (default 168, one week) — metadata is refreshed if
  older than this when the app is online. Pinned clip_cache rows are exempt
  from TTL refresh until release; you trust the snapshot you captured.

### 8.3 What the user sees in the UI

Additions to the existing two-pane layout:

- **Connection pill** top-right: `● online` / `◐ degraded` / `○ offline` /
  `↻ syncing N`. Click to "Work offline" / "Work online" / "Sync now".
- **Workspace switcher** in the CatDV pane header: `All clips ▾` opens a list
  of workspaces with cache state per workspace. "New workspace…" pushes the
  current selection into prep.
- **Sync drawer** (slide-out): list of `pending_operations` with status,
  clip, op kind, age, last error. Inline "Retry" / "Discard" / "View
  conflict" actions.
- **Per-clip badge** in the CatDV list: `↑3` = three pending ops for this
  clip; `!` = a conflict on this clip.

No new pages, no SPA routing. HTMX partials over the existing skeleton.

---

## 9. Cache state & management

A single clip can have bytes in up to three places. Users must be able to see
where, how much disk/cloud it's using, and reclaim space without breaking
in-flight work. This section defines the data model and UX for that.

### 9.1 Cache locations

There are three storage **layers** the app tracks per clip:

| Layer | Lives in | Holds | Lifecycle |
|---|---|---|---|
| `metadata` | local SQLite `clip_cache` row | canonical clip JSON, markers, fields snapshot | refreshed on TTL or workspace prep; can be deleted explicitly |
| `media-local` | local FS `DATA_DIR/cache/proxies/<provider>/<clip>.mov` + `proxy_cache` row | the proxy video file | downloaded on workspace prep (skipped when `media_is_local=True`); LRU-evictable when not workspace-pinned |
| `media-ai` | wherever the active `AIInputStore` puts it (`gcs_files` row for GCS; transient handle for Gemini Files API) | uploaded copy for Vertex/Gemini | uploaded lazily on annotation; GCS dedup means one upload per clip per bucket; Gemini Files expires after 48 h |

These are independent. A clip may have metadata only (browsed but never played
or annotated), metadata + local media (workspace-prepped but never annotated),
metadata + AI store (annotated but workspace later released), or all three.

### 9.2 Cache status, per clip

The unified query the UI uses:

```python
@dataclass(frozen=True)
class LayerStatus:
    layer: Literal["metadata", "media-local", "media-ai"]
    present: bool
    size_bytes: int | None
    location: str | None           # path | gs://uri | files/handle
    fetched_at: datetime | None
    last_used_at: datetime | None
    pinned_by_workspaces: list[int]   # workspace IDs pinning this layer
    evictable: bool                # False if pinned or in-flight

@dataclass(frozen=True)
class ClipCacheStatus:
    clip_key: ClipKey
    name: str                       # denormalized for the UI
    layers: list[LayerStatus]
    total_local_bytes: int          # sum of metadata + media-local
    total_ai_bytes: int             # sum of media-ai

class CacheInspector:
    async def status_for_clip(self, key: ClipKey) -> ClipCacheStatus: ...
    async def status_for_clips(self, keys: list[ClipKey]) -> list[ClipCacheStatus]: ...
    async def summary(self) -> CacheSummary: ...   # totals, by store, by workspace
    async def list_orphans(self) -> list[ClipCacheStatus]: ...
      # cached items whose archive entry is gone, or unreferenced gcs_files
```

`CacheInspector` is a read-only service. Mutations go through dedicated
`CacheActions`:

```python
class CacheActions:
    async def evict_local_media(self, key: ClipKey, *, force: bool = False) -> None:
        # Refuses if pinned by any workspace, unless force=True.
    async def evict_ai_media(self, key: ClipKey, *, force: bool = False) -> None:
        # Refuses if any pending_operations or recent in-flight annotation references it,
        # unless force=True.
    async def evict_metadata(self, key: ClipKey, *, force: bool = False) -> None:
        # Refuses if pinned or if pending_operations exist for this clip.
    async def evict_clip_everywhere(self, key: ClipKey, *, force: bool = False) -> None:
        # Calls the three above in order; metadata last (so other layers can verify pin state).
    async def bulk_evict(
        self, keys: list[ClipKey], layers: list[str], *, force: bool = False
    ) -> BulkEvictResult: ...
    async def evict_orphans(self) -> BulkEvictResult: ...
```

All actions go through `pending_operations`-style audit logging into a new
`cache_actions_log` table (who, what, when, success/skip reason), so undoing
a bad bulk evict is at least diagnosable.

### 9.3 Tables (additions on top of §7.1)

```sql
-- Index of GCS / Gemini Files uploads; replaces v1's gcs_files with
-- store-agnostic shape. Migration: rename gcs_files → ai_store_files,
-- add store_id column, populate with "gcs:<bucket>" for existing rows.
CREATE TABLE ai_store_files (
  store_id          TEXT NOT NULL,        -- "gcs:<bucket>" | "gemini-files"
  provider_id       TEXT NOT NULL,
  provider_clip_id  TEXT NOT NULL,
  handle            TEXT NOT NULL,        -- gs:// URI | Gemini file handle
  mime_type         TEXT NOT NULL,
  size_bytes        INTEGER NOT NULL,
  sha256            TEXT NOT NULL,
  uploaded_at       TEXT NOT NULL,
  last_used_at      TEXT NOT NULL,
  expires_at        TEXT,
  PRIMARY KEY (store_id, provider_id, provider_clip_id)
);
CREATE INDEX idx_ai_files_clip ON ai_store_files(provider_id, provider_clip_id);

CREATE TABLE cache_actions_log (
  id          INTEGER PRIMARY KEY,
  action      TEXT NOT NULL,       -- evict_local_media | evict_ai_media | ...
  clip_keys   TEXT NOT NULL,       -- JSON array of clip keys affected
  result      TEXT NOT NULL,       -- ok | skipped | partial | error
  detail      TEXT,
  bytes_freed INTEGER NOT NULL DEFAULT 0,
  at          TEXT NOT NULL
);
```

### 9.4 UI: Cache management

Two surfaces:

**Per-clip inline cache badge** (CatDV pane list and clip-detail view):

```
clip name                              ┌──── badge ───┐
Abramcukova_Anna_09     5:32   ↑3      [● ▣ ▲]
                                        │ │ │
                                        │ │ └─ media-ai (▲ green = uploaded)
                                        │ └─── media-local (▣ green = cached)
                                        └───── metadata (● green = fresh)
```

Click the badge opens the **clip cache popover** with each layer's size,
location, age, pin info, and an "Evict" button per layer (greyed out if not
evictable, with hover text explaining why).

**Cache management page** (`/cache`): one row per cached clip, sortable by
total size / last-used / workspace. Filters: by store, by workspace, by
"orphans only", by "evictable only". Bulk actions: evict selected items'
local-media / ai-media / everything. Top of page shows a `CacheSummary`:

```
Local cache    18.4 GB / 50 GB cap     (1,247 clips metadata,  47 media)
                                        [Evict all non-pinned local media]
GCS bucket     6.1 GB                  (203 clips, $0.13/mo)
                                        [Evict orphans]  [Evict all GCS]
Pending ops    12 queued (3 conflicts)  [Open sync drawer]
```

The page is plain HTMX (server-rendered table with partial swaps for bulk
actions). No client-side state beyond row selection.

### 9.5 Eviction safety rules

These are invariants enforced by `CacheActions`, not UI-side:

1. **Local media pinned by any workspace** cannot be evicted without
   `force=True`. The UI surfaces "Pinned by: <workspace names>" and offers
   "Release from workspace" as the safe path.
2. **AI store entries referenced by pending operations** cannot be evicted.
   (Pending op may reference the clip; future re-annotation may want the
   upload. Conservative.)
3. **Metadata cannot be evicted while pending operations exist for that
   clip.** The sync engine needs the snapshot to compute the change set.
4. **`evict_clip_everywhere(force=True)` is a hard delete** and is logged
   prominently. The CLI/admin equivalent for emergencies.
5. **Workspace release does not auto-evict** — it removes pins, after which
   LRU may evict over time. Explicit user action is required for immediate
   reclamation.

### 9.6 Background LRU eviction

Local media cache has a soft cap (`MEDIA_CACHE_CAP_GB`). A periodic task
(every `LRU_TICK_INTERVAL_S`, default 300 s) computes total non-pinned
local-media size; if over cap, evicts least-recently-used non-pinned entries
until under cap. Logs as `cache_actions_log` rows with `action='lru_evict'`.

GCS has no soft cap (storage is cheap and externally visible); only manual
eviction. Gemini Files expires on its own.

---

## 10. Production deployment (annotator on the archive server)

The 2026-05-18 spec described prod as "rsync + systemctl restart on the CatDV
server with `PROXY_SOURCE=filesystem`". This spec keeps that, but makes
explicit what changes and what does not when the annotator lives on the
archive machine itself.

### 10.1 What is different in prod-on-server

| Concern | Dev (Mac, VPN) | Prod (on CatDV server) |
|---|---|---|
| Archive reachability | Slow VPN (~370 KB/s), can drop | Loopback / Unix domain socket; effectively always reachable |
| Archive adapter | `catdv` with `media_is_local=False` (downloads proxies) | `catdv` with `media_is_local=True` (reads directly off disk) |
| `WorkspaceManager.prepare()` media step | Downloads proxy → `DATA_DIR/cache/proxies/` | **No-op** (resolves path, stats it; no copy) |
| `proxy_cache` table | Populated, LRU-evicting | Empty (no rows ever inserted in `filesystem` mode) |
| `media_cache` directory | 5–50 GB of `.mov` files | Empty |
| `MEDIA_CACHE_CAP_GB` | Meaningful budget | Irrelevant (still configured for the metadata-only case below) |
| Connection state | Often `degraded` or `offline` | Almost always `online` |
| Offline mode | Real workflow (VPN down, train) | Rarely used; primarily for "CatDV restart window" |
| AI input store | `gcs` (uploads required) | `gcs` (still uploads — Vertex needs `gs://`) |
| GCS upload bandwidth | Bounded by Mac WAN | Bounded by server WAN (usually much faster; data centre) |
| `/api/media/{clip}` for review pane | Streams from local cache | Streams directly from archive disk via adapter `open()` |
| Connection monitor probe target | CatDV REST `/api/info` over VPN | CatDV REST `/api/info` over loopback |
| Health probe interval | 30 s default | Can be loosened to 120 s+; loopback failures are rare |

### 10.2 What stays the same

- **The same `ArchiveProvider` adapter implementation** is used (just configured
  differently). No dev-only code branches.
- **`AIInputStore` is identical.** Vertex needs `gs://` regardless of where the
  annotator runs; the upload step happens once per clip, ever, deduped.
- **`WriteQueue` + `SyncEngine` still mediate every Apply.** In prod the queue
  drains essentially instantly (no archive latency), but the indirection
  remains so the code path is the same. This is a deliberate choice — see §10.4.
- **Workspaces still exist** but degrade to "named subsets of clips" without a
  media-prep step. Useful as scopes for batch annotation jobs and for the
  sync-drawer view.

### 10.3 Concrete prod flow (annotation cycle)

```
1. User browses clips (CatDV pane)
   → list_clips() via adapter → fast loopback call → no cache prep needed
2. User selects clips, picks template, hits "Annotate N"
   → job created; for each clip:
       archive.fetch_or_resolve_media(key)
         → adapter resolves /Volumes/ARECA/.../clip.mov; stats it; NO copy
       ai_store.ensure_uploaded(key, local_path, mime)
         → if gcs_files row exists & sha256 matches → reuse gs:// URI
         → else upload to gs://<bucket>/clips/<provider>/<clip>.mov; insert row
       gemini.annotate(ref, prompt, schema)
         → returns structured output
       annotation + review_items written to local SQLite
3. User reviews in browser, accepts items, clicks Apply
   → write_queue.enqueue(ops)
   → sync_engine.tick() (immediate, since online)
     → adapter.apply_changes(change_set) → CatDV PUT
     → write_log entry; review_items marked applied
```

End-to-end network traffic: one Vertex API call + one GCS upload per
*new* clip per *new* annotation. Re-annotating a clip on a different template
reuses the GCS upload. Re-running the same template re-uploads only if the
proxy bytes changed (sha256 mismatch).

### 10.4 Why keep the WriteQueue even in prod-on-server

Tempting to short-circuit: "we're on the box, write directly." Don't.

- Same code path dev and prod → fewer bugs, less mental load.
- Survives a CatDV restart mid-Apply (the queue retries).
- Survives the annotator restarting mid-Apply.
- Makes audit/retry/conflict surfaces uniformly available.
- The latency cost is one extra SQLite insert per op — negligible compared to
  the PUT to CatDV.

### 10.5 Service-account & filesystem permissions

- Annotator runs as a dedicated Unix user (e.g. `catdv-annotator`).
- That user must be in the group that owns the CatDV proxy directory tree.
  Read-only group access is sufficient if the adapter only ever reads.
  (Writes go through the CatDV REST API, not via the filesystem.)
- The annotator process must reach Vertex AI and GCS at the public endpoints
  (`*.googleapis.com`). If the CatDV server lives behind a NAT or firewall,
  egress to those endpoints must be allowed.
- `GOOGLE_APPLICATION_CREDENTIALS` points to `/etc/catdv-annotator/sa.json`
  (mode 600, owned by the annotator user).
- Annotator binds to `127.0.0.1:8765`. Operators reach the UI over SSH port
  forward; no exposure to the LAN.

### 10.6 Storage footprint in prod

- Local: SQLite DB (annotations, review_items, write_log, clip_cache,
  cache_actions_log) — projected ~1–2 GB at 10k clips × multiple annotations.
- Local: media cache directory — typically **empty** in prod-on-server.
- GCS: ~200 MB × number-of-annotated-clips. Cheap; lifecycle rule (Nearline
  after 30 d) is a knob if the bucket grows.
- Vertex AI: per-token / per-second billing per job; no resident storage.

### 10.7 Failure modes specific to prod

| Failure | Detection | Recovery |
|---|---|---|
| CatDV restarted on the same host | `health()` probe fails for N seconds, then returns | Connection state goes `degraded → offline → online`; queue drains automatically |
| Archive disk re-mounted at different path | adapter's `fetch_or_resolve_media()` raises | Item → `error`; configurable path template fixes it; restart annotator |
| `sa.json` rotated / removed | GCS / Vertex calls 401 | Annotator surfaces clearly in connection-state UI; ops swaps the key file in |
| Network egress blocked | GCS upload times out | AI input store reports unhealthy; jobs stop accepting; clear error in UI |
| Disk full on `DATA_DIR` | SQLite write fails | Queue persistence fails — refuses new enqueue; surface alert; operator clears space |

---

## 11. Conflict policy

The provider's `apply_changes()` returns one of:

```python
@dataclass(frozen=True)
class WriteResult:
    status: Literal["ok","conflict","retryable","fatal"]
    upstream_response: dict[str, Any]
    new_etag: str | None
    conflict_detail: ConflictDetail | None
```

Per op kind:

| Op | Conflict means | Resolution |
|---|---|---|
| `AddMarkers` | A marker we're adding overlaps with one that appeared upstream after our snapshot | Show diff in sync drawer; user accepts/edits/drops per-marker. Default: keep both (markers are additive). |
| `SetField` | The field changed upstream since our snapshot | Show both values; user picks (`local`, `remote`, `edit`). |
| `AppendNote` | Notes never conflict (append is commutative) | Always applied; documented as such. |
| `ReplaceNote` | The note changed upstream since our snapshot | Show diff; user picks. |

Conflict detection uses `expected_etag` when the provider supports it; for CatDV
(which doesn't), the adapter compares `modifyDate` from `provider_data` against
the freshly-fetched clip's `modifyDate`. Different → potential conflict; for
field/note ops the adapter further compares the specific field/note value to
decide.

`expected_etag` is captured at enqueue time, not at sync time, so the conflict
window is "from the moment the user clicked Apply until we successfully
drained." That matches user expectation.

---

## 12. Adapter packaging

```
backend/app/archive/
  __init__.py
  model.py                  # canonical types
  provider.py               # ArchiveProvider Protocol, Capabilities, results
  registry.py               # provider_id → ArchiveProvider factory
  errors.py                 # ProviderError, ConflictError, FatalError, …
  providers/
    catdv/
      __init__.py
      adapter.py            # implements ArchiveProvider; uses existing CatdvClient
      mapping.py            # canonical ↔ CatDV JSON
      payload.py            # moves payload_builder.py logic here
    fs/                     # v2
      __init__.py
      adapter.py
      sidecar.py
```

The existing `services/catdv_client.py` stays — it's the HTTP wrapper — but is
now used **only** by `archive/providers/catdv/adapter.py`. App code talks to
`ArchiveProvider`. A grep enforces this rule: nothing in `app/services/`
(other than the adapter) may import `CatdvClient`.

---

## 13. Migration plan

A single-codebase migration in seven PRs; the app remains shippable after each.

### PR 1: Canonical model + ArchiveProvider port, CatDV-only

- Add `archive/` package with model + Protocol + CatDV adapter.
- Wire `AppContext.archive` (active provider) at startup.
- Refactor `annotator`, routes, repositories to use `ArchiveProvider` and
  `CanonicalClip` instead of `CatdvClient` directly. `payload_builder` moves
  into `providers/catdv/payload.py`.
- No new tables. `catdv_clip_id` columns keep their meaning; `provider_id`
  defaults to `'catdv'` inside the adapter.
- App still talks live to CatDV on every Apply.

**Validation:** existing tests pass; one new test verifies the adapter
round-trips a recorded clip JSON.

### PR 2: AIInputStore port + GcsInputStore adapter

- Add `archive/AIInputStore` Protocol + `ai_stores/gcs/` adapter wrapping
  current `services/gcs.py`.
- `AppContext.ai_store` wired at startup.
- `annotator` calls `ai_store.ensure_uploaded()` + `reference_for_gemini()`;
  no longer references `gs://` or `GcsService` directly.
- Migration: rename `gcs_files` → `ai_store_files`; add `store_id` column;
  backfill with `'gcs:<bucket>'`.
- No user-visible change. `GeminiFilesInputStore` is a NotImplementedError stub
  that proves the port shape compiles.

### PR 3: ID columns + clip_cache + field_def_cache

- Migrations add `provider_id` / `provider_clip_id` to all clip-keyed tables.
- New `clip_cache` and `field_def_cache` tables.
- `ArchiveProvider.get_clip()` now writes through `clip_cache`. Reads consult
  cache first (with TTL).
- No behavior change visible to user. Cache is a perf and offline-readiness
  prerequisite.

### PR 4: WriteQueue + SyncEngine + ConnectionMonitor

- `pending_operations` table + service.
- Apply path now enqueues instead of writing directly. SyncEngine drains
  immediately when online. From the user's POV nothing changes except a
  half-second of "applying… done" turns into "queued… applied".
- ConnectionMonitor + connection pill UI + "Work offline" toggle.

**Validation:** Apply path covered by existing tests; new tests cover drain,
retry, conflict surfacing.

### PR 5: Workspaces + media pinning

- `workspaces`, `workspace_clips` tables; WorkspaceManager service.
- UI: workspace switcher + sync drawer.
- True offline workflow works end to end: create workspace, prep, go offline,
  annotate (with GCS reachable), review, apply, come back online, drain.

### PR 6: Cache inspector + cache management UI

- `CacheInspector` + `CacheActions` services; `cache_actions_log` table.
- Per-clip inline cache badge in CatDV pane and clip-detail view.
- `/cache` page with summary, per-clip rows, filters, bulk actions.
- LRU eviction task respects pin invariants and logs to `cache_actions_log`.

**Validation:** invariant tests — pinned media not evictable without force;
metadata not evictable while pending ops exist; bulk evict is atomic per row.

### PR 7: Filesystem archive adapter

- `archive/providers/fs/` implementation.
- New config: `ARCHIVE_PROVIDER=fs|catdv`, `FS_ROOT=…`.
- Same test suite for the worker, parameterized over both adapters.

After PR 7 both boundaries (archive *and* AI input store) are proven. Further
adapters (ResourceSpace, Gemini Files, etc.) are independent projects.

---

## 14. Config (additions to 2026-05-18 §7.6)

```ini
# Archive provider selection
ARCHIVE_PROVIDER=catdv                            # catdv | fs
ARCHIVE_PROVIDER_CONFIG_PATH=./data/provider.yaml # optional, for richer config

# AI input store selection
AI_INPUT_STORE=gcs                                # gcs | gemini-files
# When AI_INPUT_STORE=gcs:
GCS_BUCKET_NAME=pragafilm-catdv-annotator-proxies
# When AI_INPUT_STORE=gemini-files:
# (no extra config; uses GOOGLE_APPLICATION_CREDENTIALS)

# Workspace / cache budgets
MEDIA_CACHE_CAP_GB=50
CLIP_CACHE_TTL_HOURS=168
LRU_TICK_INTERVAL_S=300

# Connection monitor
HEALTH_PROBE_INTERVAL_S=30
HEALTH_PROBE_TIMEOUT_S=5

# Sync engine
SYNC_RETRY_BASE_S=2
SYNC_RETRY_MAX_S=300
SYNC_TICK_INTERVAL_S=5

# Filesystem archive provider (when ARCHIVE_PROVIDER=fs)
FS_ROOT=/path/to/archive/root
```

CatDV-specific env vars from the 2026-05-18 spec move under "CatDV adapter
config" and become only relevant when `ARCHIVE_PROVIDER=catdv`. Likewise
`GCP_PROJECT_ID`/`GCP_LOCATION`/`GCS_BUCKET_NAME`/`GOOGLE_APPLICATION_CREDENTIALS`
are AI-store-specific (`gcs` adapter today).

### 14.1 Recommended config matrix

| Scenario | `ARCHIVE_PROVIDER` | `media_is_local` | `AI_INPUT_STORE` | Notes |
|---|---|---|---|---|
| Dev on Mac over VPN | `catdv` | `False` | `gcs` | Proxies cached locally; uploaded to GCS once |
| Prod on CatDV server | `catdv` | `True` | `gcs` | Direct disk reads; uploads to GCS once |
| Filesystem archive | `fs` | `True` | `gcs` | Sidecar JSONs; uploads to GCS for Gemini |
| Hobbyist / no-GCS install | `fs` | `True` | `gemini-files` | Files API; 48 h auto-expire |

---

## 15. Testing strategy

The 2026-05-18 spec's layered tests still apply. Additions:

1. **Contract tests for `ArchiveProvider`.** A shared pytest suite, parameterized
   over each adapter, asserts every method's contract — round-trip a clip
   through `get_clip` → `apply_changes(SetField)` → `get_clip`; markers add
   without removing existing; conflicts surface when `expected_etag` stale.
2. **Contract tests for `AIInputStore`.** Parameterized over GCS adapter
   (against the GCS storage emulator) and the Gemini Files stub: idempotent
   upload, dedup-by-sha256 only when capability declares it, evict removes
   the entry, `reference_for_gemini()` returns the right SDK shape.
3. **WriteQueue invariants.** Enqueue is atomic with the local UI's accept;
   `pending_operations` rows never lost on crash; drain ordering preserved
   per clip; `attempts` increments on retryable failure; `applied_at` set
   atomically with `status=applied`.
4. **SyncEngine state machine.** Property-based test: any sequence of
   `(enqueue, drain, fail, conflict, retry)` events leaves the queue in a
   consistent state (no `in_flight` rows after engine quiesces, no double
   apply).
5. **Workspace prep resilience.** Cache prep can be interrupted at any byte
   and resume; partial workspaces are usable; release returns disk.
6. **Cache eviction invariants.** Pinned-by-workspace media never evicted
   without `force=True`; metadata never evicted while `pending_operations`
   exist for the clip; LRU stops at the cap without crossing a pin;
   `cache_actions_log` row written for every action including skips.
7. **Offline → online cycle (end-to-end).** Set state offline, queue N ops,
   set online, watch drain. With induced network errors mid-drain, queue
   eventually converges.
8. **Filesystem adapter parity.** The same job + apply test that runs against
   CatDV runs against an FS adapter pointed at a tmp_path tree.
9. **Prod-mode integration test.** With `media_is_local=True`, verify
   `WorkspaceManager.prepare()` performs zero `fetch_media()` byte transfers
   and `/api/media/{clip}` streams directly from the configured archive
   directory.

Test-isolation rule from the original spec carries over: "Task 19 fix later"
is banned; isolation breakage blocks the PR.

---

## 16. v2 Definition of Done

- `ArchiveProvider` port exists and is the only way app code reaches an
  archive. Lint rule (or test that greps imports) enforces it.
- `AIInputStore` port exists and is the only way `annotator` reaches Gemini's
  input store. `GcsInputStore` is the default and shippable adapter.
- CatDV and FS archive adapters both pass the shared contract test suite.
- Apply path goes through `pending_operations` in all cases; `write_log`
  remains the audit trail of *upstream* writes.
- Connection-state pill, sync drawer, workspace switcher exist in UI.
- Per-clip cache badge and `/cache` management page exist; bulk evict actions
  respect pin invariants.
- Prod-on-server deployment works as described in §10: `media_is_local=True`,
  no proxy downloads, GCS upload happens exactly once per clip.
- "Go offline → annotate (cached) → reconnect → sync" cycle works end to end
  on a real machine with VPN toggled off mid-session (dev mode).
- Existing 2026-05-18 happy path (annotate + review + apply over live CatDV)
  still works end to end. Apply now reads "queued… applied" rather than
  "applying…" because of the WriteQueue indirection; otherwise the flow is
  unchanged.

### What v2 does NOT include

- Multi-active-provider UI.
- Background workspace prep cron (prep is user-triggered for now).
- Distributed sync / multi-user.
- Provider for: ResourceSpace, Interplay, Adobe Bridge, etc. (each is a
  follow-on project; the boundary makes them possible, not free.)
- Fully-offline AI (Vertex still requires connectivity).
- Encrypted local cache (the local DB is in `DATA_DIR`, on the user's
  machine; security model is "local file" as today).

---

## 17. Risks & open questions

- **CatDV conflict detection without etag.** Falling back to `modifyDate`
  comparison plus per-field value comparison is heuristic. Will miss races
  shorter than the modifyDate resolution. Acceptable for single-user; revisit
  if other CatDV-side editors become active.
- **Schema migration of `catdv_clip_id`.** We add `provider_id` /
  `provider_clip_id` columns and dual-write for one release before dropping
  `catdv_clip_id`. Lengthens the migration but avoids a flag-day.
- **Workspace pin vs LRU eviction.** Bug-prone area: a clip pinned to one
  workspace and released from another must not get evicted while still pinned
  elsewhere. Eviction logic must read `workspace_clips` for any pin.
- **GCS bills while clips sit in the bucket post-annotation.** Independent of
  this spec, but workspace lifecycle interacts with `gcs_files`: deleting a
  workspace doesn't delete GCS objects (they're per-clip, may be reused). A
  separate GCS-eviction policy decision is still owed.
- **Field-definition divergence between providers.** Templates currently use
  CatDV-shaped `pragafilm.*` identifiers in `target_map`. Cross-provider
  templates need a provider-tag on each `target_map` entry, and a way to
  declare "this template requires capability X." Spec keeps the door open;
  the v2 implementation requires templates to be tagged with a single
  `provider_id` and rejects cross-provider use. Multi-provider templates are
  v3.
- **`pending_operations` ordering across providers.** The single-provider
  rule sidesteps this. If we ever support two active providers, drain order
  per provider is independent.

---

## 18. Glossary additions

| Term | Meaning |
|---|---|
| **provider** | An archive backend (CatDV, filesystem, etc.) accessible via the `ArchiveProvider` port. |
| **adapter** | Concrete implementation of `ArchiveProvider` for one provider type. |
| **canonical model** | The app-internal `Clip`/`Marker`/`Field`/`ChangeSet` types; adapter-agnostic. |
| **change set** | A grouped list of typed change ops targeting one clip; the unit of write. |
| **write queue / pending_operations** | The persistent journal of change ops awaiting upstream application. |
| **sync engine** | The service that drains `pending_operations` against the active provider. |
| **connection state** | One of `online`, `degraded`, `offline`, `syncing`; explicit, user-visible. |
| **workspace** | A named, pinned subset of an archive's clips with cached metadata + media. |
| **etag** | Opaque per-clip version token used for optimistic concurrency where the provider supports it. |
| **AI input store** | The destination Gemini reads media bytes from (GCS today, Gemini Files API optional). A port distinct from `ArchiveProvider`. |
| **cache layer** | One of `metadata`, `media-local`, `media-ai`; tracked per clip by the cache inspector. |
| **pin** | Marker that exempts a cached item from LRU eviction; set by workspace membership. |
| **prod-on-server** | Deployment mode where the annotator runs on the same host as the archive, with `media_is_local=True`. |

---

## 19. Document control

- This spec changes the architecture of the app between v1 (2026-05-18 spec)
  and v2. The v1 spec is not deleted; sections of it that this spec overrides
  are marked at the top.
- Significant design changes after first commit are logged in
  `docs/decisions.md`.
