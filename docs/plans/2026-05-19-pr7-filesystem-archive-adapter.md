# PR 7: Filesystem archive adapter + shared contract tests — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** Land the second `ArchiveProvider` adapter (`FilesystemArchiveProvider`) and a shared Protocol contract test suite that runs against both `catdv` and `fs`. PR 7 is the final PR of the seven-PR migration in `docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md` (spec §5.3, §13 PR 7, §15 items 1 and 8). The FS adapter walks a `FS_ROOT` directory: subdirectories are catalogs; media files are clips; per-clip JSON sidecars (`<clipname>.annot.json`) hold markers, fields, and notes; `.archive/fields.json` carries field definitions. Writes are POSIX-atomic (tempfile + fsync + rename) and use `sha256` of the on-disk sidecar as a real optimistic-concurrency etag. `media_is_local=True`, so `fetch_media` is a path resolution only. After PR 7 the archive port is provably substitutable.

**Architecture:** `backend/app/archive/providers/fs/` adds four modules: `adapter.py` implements `ArchiveProvider`; `sidecar.py` owns the JSON shape and (de)serialisation; `fields.py` reads `FS_ROOT/.archive/fields.json`; `media_probe.py` resolves duration/fps via `ffprobe` if present, otherwise returns `(0.0, 25.0)` and logs a single warning. `backend/app/archive/registry.py` learns a second branch (`name == "fs"`). `backend/app/settings.py` gains `fs_root: Path | None` and `fs_media_exts: str` (comma-separated; default `.mov,.mp4,.mkv,.mxf,.m4v,.avi`), with a `model_validator` requiring `fs_root` whenever `archive_provider="fs"`. `AppContext.build` learns to skip CatDV-client construction for FS; the CatDV adapter path is unchanged. A shared contract test module (`tests/contract/test_archive_provider.py`) is parameterised over both adapters via a `pytest` fixture that yields a built `ArchiveProvider` plus a "seeder" callable for inserting a test clip. The FS-only test suite (`tests/integration/test_fs_adapter.py`, `tests/integration/test_fs_sidecar.py`, etc.) covers sidecar round-tripping, atomic-write semantics, missing `.archive/fields.json`, and `ffprobe` absence. A small end-to-end test (`tests/integration/test_fs_e2e.py`) wires WriteQueue + SyncEngine + FilesystemArchiveProvider against a `tmp_path` tree and asserts a `SetField` lands in the sidecar.

**Tech Stack:** Python 3.12, frozen dataclasses, `aiosqlite` (only for the e2e test), `pathlib`, `hashlib`, `subprocess` (for `ffprobe`), `pytest` + `pytest-asyncio`. No new third-party deps (do not pull in `ffmpeg-python`).

**Scope guardrail:** This plan implements ONLY what spec §13 PR 7 lists. NOT in this PR: a second AI input store adapter (Gemini Files API), ResourceSpace / Interplay / Bridge adapters, multi-active-provider UI, new core ports beyond PRs 1-6, new core tables or migrations. The FS adapter does not write through to `clip_cache` — the `get_clip` path bypasses cache and reads sidecar on every call (rationale: sidecar reads are sub-millisecond local I/O; caching adds invalidation complexity for no gain).

**Decisions to record in `docs/decisions.md`** (Task 11):
1. **`provider_clip_id` is the clip path relative to `FS_ROOT`, with the media extension stripped.** Example: `FS_ROOT/archive_30s/clip001.mov` → `provider_clip_id="archive_30s/clip001"`. This is stable across renames-within-extension (e.g. `.mov` ↔ `.MOV` normalised to lowercase), composes the catalog (first path segment) and clip name in one string, and is human-readable in the audit log. Alternatives rejected: a `sha256` of the path (opaque in logs); the absolute path (leaks `FS_ROOT`); the filename alone (collides across catalogs). The forward slash is the canonical separator in stored ids regardless of host OS, so Windows paths normalise on the way in.
2. **A catalog is a top-level directory under `FS_ROOT`.** Nested subdirectories within a catalog are walked recursively and contribute to `provider_clip_id` (catalog-relative path), but they are NOT separate catalogs. Rationale: the UI's catalog switcher is one-level; deeper nesting is a clip-organisation choice the FS user makes. Hidden directories (those starting with `.`, especially `.archive`) and the literal `.archive` directory are excluded from the catalog list.
3. **`ffprobe` is optional. If `shutil.which("ffprobe")` is `None`, the adapter logs a single warning at startup (via `media_probe.probe`) and returns `(duration_secs=0.0, fps=25.0)` for every clip.** Failed probes (subprocess error, malformed JSON) also fall back to defaults. Rationale: deployments without `ffprobe` should still be usable end to end; surfacing the gap as a config error would block hobbyist installs unnecessarily. The probe is per-call (no module-level cache) for testability; the missing-`ffprobe` warning is emitted at most once per process via a module flag.
4. **Timecodes in sidecars are stored as `{"secs": float, "frm": int, "fps": float}` triples, with `frm = round(secs * fps)`.** Rationale: the canonical `Timecode` carries `secs/fps/frm/txt`; we drop `txt` from the persisted shape because SMPTE-string rendering is a display concern reconstructible from `secs+fps`. Stored frame numbers anchor the marker against the clip's declared fps so a downstream fps re-detection (a future `ffprobe` cache) does not silently shift markers.
5. **The FS adapter is etag-aware (`supports_etag=True`); etag = `sha256` of the sidecar bytes on disk.** A missing sidecar has etag `None`. Writes compute the etag of the post-write sidecar and return it as `WriteResult.new_etag`. Rationale: the FS write atomicity (tempfile + rename) makes a real etag cheap and correct, unlike CatDV's `modifyDate` heuristic.
6. **The FS adapter ignores `clip_cache` / `field_def_cache`.** Both repos are wired by `AppContext` for the CatDV path; the FS adapter accepts and silently ignores them (matching `CatdvArchiveAdapter`'s `cache_enabled` guard). Rationale: the sidecar IS the cache; an additional mirror invites two-source-of-truth bugs and offers no latency win on a local disk.

---

## File map

### New files

| Path | Responsibility |
|---|---|
| `backend/app/archive/providers/fs/__init__.py` | Re-export `FilesystemArchiveProvider`. |
| `backend/app/archive/providers/fs/adapter.py` | `FilesystemArchiveProvider` implements `ArchiveProvider`. |
| `backend/app/archive/providers/fs/sidecar.py` | Sidecar JSON (de)serialisation. |
| `backend/app/archive/providers/fs/fields.py` | Load `FS_ROOT/.archive/fields.json` → `list[FieldDef]`. |
| `backend/app/archive/providers/fs/media_probe.py` | `ffprobe`-based duration/fps; fallback `(0.0, 25.0)`. |
| `tests/contract/__init__.py` | Empty. |
| `tests/contract/test_archive_provider.py` | Shared contract suite, parameterised over `catdv` + `fs`. |
| `tests/integration/test_fs_adapter.py` | FS-specific adapter behaviour. |
| `tests/integration/test_fs_sidecar.py` | Sidecar round-trip + provider_data preservation. |
| `tests/integration/test_fs_atomic_write.py` | Tempfile-rename atomicity; on-failure file unchanged. |
| `tests/integration/test_fs_fields_loader.py` | `.archive/fields.json` parsing + missing-file path. |
| `tests/integration/test_fs_media_probe.py` | `ffprobe`-missing returns defaults. |
| `tests/integration/test_fs_e2e.py` | End-to-end: WriteQueue → SyncEngine → FilesystemArchiveProvider. |
| `tests/fixtures/fs_archive/.archive/fields.json` | Sample fields definition. |
| `tests/fixtures/fs_archive/archive_30s/clip001.mov` | Tiny media file (empty bytes). |
| `tests/fixtures/fs_archive/archive_30s/clip001.annot.json` | Sample sidecar. |
| `docs/fs-archive-format.md` | FS archive on-disk format reference (under 200 lines). |

### Modified files

| Path | Change |
|---|---|
| `backend/app/archive/registry.py` | Dispatch on `archive_provider == "fs"` to `FilesystemArchiveProvider`. |
| `backend/app/settings.py` | Add `fs_root: Path | None`, `fs_media_exts: str`; validate `fs_root` required when `archive_provider="fs"`. |
| `backend/app/context.py` | `AppContext.build` skips `CatdvClient` when `archive_provider="fs"`; passes `fs_root` + media exts. |
| `docs/DEPLOY.md` | Short FS-deploy section (env vars + fields file). |
| `docs/decisions.md` | Six PR-7 decisions appended. |

### Deleted files

None.

---

## Tasks

### Task 1: Sidecar JSON shape + (de)serialisation

**Files:**
- Create: `backend/app/archive/providers/fs/sidecar.py`
- Create: `tests/integration/test_fs_sidecar.py`

- [ ] **Step 1:** Sidecar shape: `{"markers": [...], "fields": {...}, "notes": {...}, "provider_data": {...}}`. Markers: `{"name", "in": {"secs","frm","fps"}, "out": {...}|null, "description"|null, "category"|null, "color"|null}`. Fields: `{identifier: {"value": ..., "is_multi": bool}}`. Notes: `{name: text}`. Round-trip tests: extra keys preserved via `provider_data`; missing optional keys yield defaults.
- [ ] **Step 2:** Functions `sidecar_to_dict(canonical_clip) -> dict`, `sidecar_from_dict(d, fps) -> (markers, fields, notes, provider_data)`. (`fps` defaulted by the adapter from media_probe.)
- [ ] **Step 3:** Verify — `pytest tests/integration/test_fs_sidecar.py`.

### Task 2: Field-defs loader

**Files:**
- Create: `backend/app/archive/providers/fs/fields.py`
- Create: `tests/integration/test_fs_fields_loader.py`

- [ ] **Step 1:** `load_field_defs(fs_root: Path) -> list[FieldDef]`. Reads `fs_root / ".archive" / "fields.json"` if present; returns `[]` if missing. JSON shape: a list of dicts with `identifier`, `name`, `type` (one of the canonical `FieldDef.type` literals), `is_multi`, `is_editable`, optional `picklist_values`. Unknown keys go into `provider_data`.
- [ ] **Step 2:** Tests: missing file → `[]`; well-formed file → `list[FieldDef]`; malformed JSON → empty list + warning logged.
- [ ] **Step 3:** Verify — `pytest tests/integration/test_fs_fields_loader.py`.

### Task 3: Media probe

**Files:**
- Create: `backend/app/archive/providers/fs/media_probe.py`
- Create: `tests/integration/test_fs_media_probe.py`

- [ ] **Step 1:** `probe(path: Path) -> tuple[float, float]` returning `(duration_secs, fps)`. If `shutil.which("ffprobe")` is `None`, log a warning once (module-level flag) and return `(0.0, 25.0)`. Otherwise run `ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate,duration -of json <path>` and parse. On any failure (timeout, non-zero exit, malformed JSON), return `(0.0, 25.0)`.
- [ ] **Step 2:** Tests: monkeypatch `shutil.which` to return `None`; assert defaults. Monkeypatch `subprocess.run` to return a `CompletedProcess` with known JSON; assert parsed values.
- [ ] **Step 3:** Verify — `pytest tests/integration/test_fs_media_probe.py`.

### Task 4: FilesystemArchiveProvider — read API

**Files:**
- Create: `backend/app/archive/providers/fs/__init__.py`
- Create: `backend/app/archive/providers/fs/adapter.py`
- Create: `tests/integration/test_fs_adapter.py`
- Create: `tests/fixtures/fs_archive/.archive/fields.json`
- Create: `tests/fixtures/fs_archive/archive_30s/clip001.mov` (empty)
- Create: `tests/fixtures/fs_archive/archive_30s/clip001.annot.json`

- [ ] **Step 1:** `FilesystemArchiveProvider(fs_root, media_exts, clock=...)`. `id="fs"`. Capabilities: `supports_markers=True`, `supports_notes=frozenset({"notes","bigNotes"})`, `supports_field_create=False`, `supports_etag=True`, `media_is_local=True`, `write_atomicity="per-clip"`.
- [ ] **Step 2:** `list_catalogs()` returns sorted directories under `fs_root` excluding hidden (`.*`) and `.archive`. Each entry: `{"id": <name>, "name": <name>}`.
- [ ] **Step 3:** `list_clips(catalog, ClipQuery)` walks `fs_root/catalog` recursively, returns a `ClipPage`. Each item is a `CanonicalClip` built by reading the sidecar (if present) and calling `media_probe`. Honours `query.text` (case-insensitive substring of clip name), `offset`, `limit`.
- [ ] **Step 4:** `get_clip(provider_clip_id)` resolves the media file under `fs_root` (recover extension by globbing), reads sidecar if present, returns `CanonicalClip`. Raises `FatalProviderError` if the media file is missing.
- [ ] **Step 5:** Tests use the fixture tree: list catalogs ⇒ `["archive_30s"]`; `get_clip("archive_30s/clip001")` returns a canonical clip with the sidecar markers/fields/notes.
- [ ] **Step 6:** Verify — `pytest tests/integration/test_fs_adapter.py -k "read or list"`.

### Task 5: FilesystemArchiveProvider — write API + etag

**Files:**
- Modify: `backend/app/archive/providers/fs/adapter.py`
- Modify: `tests/integration/test_fs_adapter.py`
- Create: `tests/integration/test_fs_atomic_write.py`

- [ ] **Step 1:** `apply_changes(change_set)`. Compute live etag (`sha256` of sidecar bytes on disk, or `None` if missing). If `change_set.expected_etag` is set and differs, return `WriteResult(status="conflict", ...)`. Otherwise apply ops on top of the current sidecar dict (additive `AddMarkers`, scalar `SetField`, `AppendNote` joins with `\n`, `ReplaceNote` overwrites). Write sidecar via tempfile + `fsync` + `os.replace`. Return `WriteResult(status="ok", new_etag=<post-write sha256>)`.
- [ ] **Step 2:** Test: `SetField` then `get_clip` round-trip; `AddMarkers` accumulates; stale `expected_etag` → `conflict`; `AppendNote` is commutative under two writes.
- [ ] **Step 3:** Atomicity test: simulate a `json.dumps` raise inside the write (patch sidecar.serialise to raise); assert sidecar on disk is unchanged after the failure (no `.tmp` left around).
- [ ] **Step 4:** Verify — `pytest tests/integration/test_fs_adapter.py tests/integration/test_fs_atomic_write.py`.

### Task 6: FilesystemArchiveProvider — health + field_definitions

**Files:**
- Modify: `backend/app/archive/providers/fs/adapter.py`
- Modify: `tests/integration/test_fs_adapter.py`

- [ ] **Step 1:** `health()`: checks `fs_root.is_dir()` and that `.archive/fields.json` is readable when it exists. Returns `ProviderHealth(ok=True, latency_ms=…)` or `ok=False` with a detail string.
- [ ] **Step 2:** `list_field_definitions()`: delegates to `fields.load_field_defs(fs_root)`. Optionally caches via the injected `field_def_cache_repo` (matching CatDV adapter) — but per decision 6, skip cache write-through here unless a future PR adds a use.
- [ ] **Step 3:** Tests for health (broken root → not ok) and field defs (matches fixture file).
- [ ] **Step 4:** Verify — `pytest tests/integration/test_fs_adapter.py`.

### Task 7: Provider-selection wiring

**Files:**
- Modify: `backend/app/settings.py`
- Modify: `backend/app/archive/registry.py`
- Modify: `backend/app/context.py`
- Create: `tests/integration/test_fs_settings_and_registry.py`

- [ ] **Step 1:** `Settings`: add `fs_root: Path | None = None`, `fs_media_exts: str = ".mov,.mp4,.mkv,.mxf,.m4v,.avi"`. `model_validator` raises if `archive_provider=="fs"` and `fs_root` is None/empty.
- [ ] **Step 2:** `build_archive_provider` dispatch: `name == "fs"` → `FilesystemArchiveProvider(fs_root=settings.fs_root, media_exts=settings.fs_media_exts.split(","))`. Still passes `clip_cache_repo` / `field_def_cache_repo` / `db_provider` (ignored by FS adapter).
- [ ] **Step 3:** `AppContext.build`: when `settings.archive_provider == "fs"`, don't open a `CatdvClient`. The `proxy_resolver` is set to `None` for FS (workspace_manager skips media step when `media_is_local=True`).
- [ ] **Step 4:** Tests: building settings with `archive_provider="fs"` and no `fs_root` raises; registry returns an FS adapter; `AppContext.build` works with FS provider against a tmp_path tree (skip if it requires GCP — guard with the same `init_external` pattern).
- [ ] **Step 5:** Verify — `pytest tests/integration/test_fs_settings_and_registry.py`.

### Task 8: Shared contract test suite

**Files:**
- Create: `tests/contract/__init__.py`
- Create: `tests/contract/test_archive_provider.py`

- [ ] **Step 1:** Two pytest fixtures, each yielding `(provider, seed_clip)` where `seed_clip(name) -> ClipKey`. The CatDV fixture wraps `running_fake_catdv()` and seeds via `fake.clips[...]`. The FS fixture creates a `tmp_path` tree with one media file and one sidecar.
- [ ] **Step 2:** Parameterise via `@pytest.fixture(params=["catdv", "fs"])` that returns one of the two builders. Each test resolves the right fixture from `request`.
- [ ] **Step 3:** Tests (each runs against both adapters):
  - `test_get_clip_round_trip` — seed, `get_clip`, assert canonical shape.
  - `test_set_field_round_trip` — `apply_changes(SetField)` then `get_clip` shows the new value.
  - `test_add_markers_is_additive` — initial marker count + 1 after `AddMarkers`.
  - `test_append_note_commutative` — two `AppendNote`s land in the note text.
  - `test_stale_etag_conflict` — only when `provider.capabilities.supports_etag`; otherwise skip via `pytest.skip`.
  - `test_capabilities_shape` — `supports_markers` is bool, `supports_notes` is a frozenset, `write_atomicity` ∈ `{"per-clip","per-op"}`.
- [ ] **Step 4:** Verify — `pytest tests/contract/test_archive_provider.py -v`.

### Task 9: FS end-to-end (WriteQueue + SyncEngine)

**Files:**
- Create: `tests/integration/test_fs_e2e.py`

- [ ] **Step 1:** Build a `tmp_path` FS archive with one clip + sidecar. Construct `FilesystemArchiveProvider`, `WriteQueue`, `SyncEngine`, a no-op `ConnectionMonitor` (or `None`, set state to `online`), and the relevant repos against the `db` fixture.
- [ ] **Step 2:** Enqueue a `SetField` via `WriteQueue.enqueue_apply` (use a minimal `Annotation`+`ReviewItem` shape). Drain via `engine.drain_once()`. Read the sidecar back from disk. Assert the field value updated.
- [ ] **Step 3:** Verify — `pytest tests/integration/test_fs_e2e.py`.

### Task 10: Docs

**Files:**
- Create: `docs/fs-archive-format.md`
- Modify: `docs/DEPLOY.md`

- [ ] **Step 1:** `fs-archive-format.md`: directory layout, sidecar JSON schema, `.archive/fields.json` shape, etag semantics, ffprobe expectations. Under 200 lines.
- [ ] **Step 2:** `DEPLOY.md`: new short section "Filesystem archive provider" with the four env vars (`ARCHIVE_PROVIDER=fs`, `FS_ROOT`, optional `FS_MEDIA_EXTS`) and a one-line pointer to `fs-archive-format.md`.

### Task 11: Decision log

**Files:**
- Modify: `docs/decisions.md`

- [ ] **Step 1:** Append a "2026-05-19: PR 7 — Filesystem archive adapter" entry with the six decisions above, in the existing format (Context / Alternatives / Choice / Why).

### Task 12: Final verification

- [ ] `python -m pytest -q --deselect tests/integration/test_proxy_resolver_fs.py::test_fs_resolver_raises_when_unreadable` is green (314 baseline + new tests, all passing).
- [ ] `ruff check backend tests` reports no new lints above baseline.
- [ ] Shared contract suite passes for both adapters in the same run.
