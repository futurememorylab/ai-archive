# Thumbnail Metadata-Gate Fix (poster cache + throttle + placeholder) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make thumbnails load for listed-but-never-opened clips (currently 404 even when online), without hammering CatDV and without broken-image artifacts.

**Background (root cause, verified live on rev 00006):** The thumb path's metadata gate (ADR 0065) returns 404 for any clip with no `clip_cache` row, because "no metadata ⇒ posterID unknowable." Listing clips writes only `clip_list_cache`; only *opening* a clip (`get_clip`) writes its `clip_cache` row. So listed-but-unopened clips have their thumbnails gated off even when online. Confirmed by a clean test: thumb 888709 = 404 → `get_clip 888709` (writes the row) → thumb = 200.

**Why not "just write clip_cache from the list":** `get_clip` is cache-first (adapter.py:166) and the list payload is a *lighter projection* (has `posterID` but no `thumbnailIDs`/detail fields). Writing list data into `clip_cache` would make `get_clip` serve that partial row forever — regressing clip detail. So we use a dedicated poster cache instead.

**Approach (3 parts):**
1. **Poster cache** — a lightweight `(provider_id, clip_id) → poster_id` table, populated during `list_clips`. The thumb path, on a metadata-gate miss, reads `posterID` from it and downloads the poster directly — no `get_clip`, no `clip_cache` pollution, one CatDV call per thumb. Orphans (not listed) stay gated, preserving ADR 0065's `/cache` protection.
2. **Throttle** — a `ThumbnailService` semaphore (cap **3**) around the CatDV thumbnail download, so a list-load's N lazy `<img>` requests don't stampede the seat-limited server.
3. **Placeholder** — one shared, aesthetic empty-thumb style (film-frame gradient + centred glyph) applied consistently across all thumb surfaces, replacing today's mix of `visibility:hidden` / removed-img / bare background.

**Tech Stack:** Python 3.13, FastAPI, aiosqlite, pytest/pytest-asyncio; Jinja + Alpine + app.css. Run Python via `.venv/bin/python`.

---

## File structure

| File | Responsibility | Action |
|---|---|---|
| `backend/migrations/0019_poster_cache.sql` | `poster_cache` table | Create |
| `backend/app/repositories/poster_cache.py` | `PosterCacheRepo` (`upsert_many`, `get_poster_id`) | Create |
| `backend/app/context.py` | `CoreCtx.poster_cache_repo` field; pass to `build_archive_provider`; `poster_id_provider` closure + `download_concurrency` into `ThumbnailService` | Modify |
| `backend/app/archive/registry.py` | thread `poster_cache_repo` into `CatdvArchiveAdapter` | Modify |
| `backend/app/archive/providers/catdv/adapter.py` | write poster_cache during list write-through | Modify |
| `backend/app/services/thumbnail_service.py` | poster-cache fallback + download semaphore | Modify |
| `backend/app/static/app.css` | shared `.thumb--empty` aesthetic placeholder + glyph | Modify |
| `backend/app/templates/pages/_video_list.html`, `_clip_picker_basket.html`, `_studio_set_clip_card.html` | consistent placeholder hook | Modify |
| `backend/app/static/clipPicker.js` | consistent placeholder onerror | Modify |
| tests under `tests/unit` + `tests/integration` | coverage per task | Create/Modify |

Branch: `cloud-run-deployment`. Commit after each task.

---

### Task 1: poster_cache table + PosterCacheRepo

**Files:** Create `backend/migrations/0019_poster_cache.sql`, `backend/app/repositories/poster_cache.py`, `tests/unit/test_poster_cache_repo.py`.

- [ ] **Step 1: Write the migration** `backend/migrations/0019_poster_cache.sql`:

```sql
-- 0019: lightweight poster-id cache. Maps a clip to its CatDV posterID,
-- populated when listing clips (the list payload carries posterID but not
-- full detail). Lets the thumbnail path fetch a listed clip's poster without
-- a full get_clip — sidestepping the metadata gate (ADR 0065) for clips that
-- have been listed but not opened, WITHOUT polluting clip_cache with partial
-- rows (get_clip is cache-first). See ADR 0072.
CREATE TABLE poster_cache (
  provider_id      TEXT    NOT NULL,
  provider_clip_id TEXT    NOT NULL,
  poster_id        INTEGER NOT NULL,
  updated_at       TEXT    NOT NULL,
  PRIMARY KEY (provider_id, provider_clip_id)
);
```

- [ ] **Step 2: Write the failing repo test** `tests/unit/test_poster_cache_repo.py`:

```python
from pathlib import Path

import aiosqlite
import pytest

from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.poster_cache import PosterCacheRepo

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


async def _db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await apply_migrations(conn, MIGRATIONS)
    return conn


@pytest.mark.asyncio
async def test_upsert_many_then_get():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 882119), (888709, 882156)])
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888709") == 882156
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888700") == 882119
    await conn.close()


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    conn = await _db()
    repo = PosterCacheRepo()
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="999") is None
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_overwrites():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 1)])
    await repo.upsert_many(conn, provider_id="catdv", entries=[(888700, 2)])
    assert await repo.get_poster_id(conn, provider_id="catdv", provider_clip_id="888700") == 2
    await conn.close()


@pytest.mark.asyncio
async def test_upsert_many_empty_is_noop():
    conn = await _db()
    repo = PosterCacheRepo()
    await repo.upsert_many(conn, provider_id="catdv", entries=[])  # must not error
    await conn.close()
```

- [ ] **Step 3: Run to verify fail** — `.venv/bin/python -m pytest tests/unit/test_poster_cache_repo.py -v` → ModuleNotFoundError.

- [ ] **Step 4: Implement** `backend/app/repositories/poster_cache.py`:

```python
"""PosterCacheRepo — persists / reads `poster_cache`, a lightweight
clip→posterID index populated when listing clips. The thumbnail path uses
it to fetch a listed clip's poster without a full get_clip (see ADR 0072)."""

from datetime import UTC, datetime

import aiosqlite


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PosterCacheRepo:
    async def upsert_many(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        entries: list[tuple[int, int]],
    ) -> None:
        """Upsert (clip_id, poster_id) pairs for one provider. No-op on []."""
        if not entries:
            return
        now = _now_iso()
        await conn.executemany(
            """
            INSERT INTO poster_cache
              (provider_id, provider_clip_id, poster_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(provider_id, provider_clip_id) DO UPDATE SET
              poster_id = excluded.poster_id,
              updated_at = excluded.updated_at
            """,
            [(provider_id, str(cid), int(pid), now) for cid, pid in entries],
        )
        await conn.commit()

    async def get_poster_id(
        self,
        conn: aiosqlite.Connection,
        *,
        provider_id: str,
        provider_clip_id: str,
    ) -> int | None:
        async with conn.execute(
            "SELECT poster_id FROM poster_cache "
            "WHERE provider_id = ? AND provider_clip_id = ?",
            (provider_id, provider_clip_id),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row is not None else None
```

- [ ] **Step 5: Run to verify pass** — `.venv/bin/python -m pytest tests/unit/test_poster_cache_repo.py -v` → 4 passed.

- [ ] **Step 6: Commit**
```bash
git add backend/migrations/0019_poster_cache.sql backend/app/repositories/poster_cache.py tests/unit/test_poster_cache_repo.py
git commit -m "feat(cache): poster_cache table + PosterCacheRepo (clip->posterID index)"
```

---

### Task 2: populate poster_cache when listing clips

**Files:** Modify `backend/app/archive/registry.py`, `backend/app/archive/providers/catdv/adapter.py`; Test `tests/unit/test_catdv_list_populates_poster_cache.py`.

**Context:** `CatdvArchiveAdapter.list_clips` builds `items` from the raw CatDV list payload (each raw has `posterID`) and calls `_write_list_through`. Add a parallel poster_cache write. The adapter must accept a `poster_cache_repo` (like `clip_list_cache_repo`). The raw items are at `data["items"]`; each raw dict has key `"posterID"` (an int) — extract `(int(raw["ID"]), int(raw["posterID"]))` for raws that have a truthy posterID.

- [ ] **Step 1: Write the failing test** `tests/unit/test_catdv_list_populates_poster_cache.py`. Read the existing `tests/unit/` adapter tests (e.g. `test_catdv_*`) for how the adapter is constructed with fakes; mirror that. The test boots a `CatdvArchiveAdapter` with a fake client returning a list payload `{"items": [{"ID": 888700, "posterID": 882119, ...}, {"ID": 888709, "posterID": 882156, ...}, {"ID": 888711}], "totalItems": 3}`, a real in-memory db with migrations applied, a real `PosterCacheRepo`, `is_online_provider=lambda: True`. After `await adapter.list_clips(catalog, ClipQuery(...))`, assert:
  - `poster_cache.get_poster_id(db, provider_id=adapter.id, provider_clip_id="888700") == 882119`
  - `... "888709") == 882156`
  - `... "888711") is None`  (no posterID in payload ⇒ not written)

(If constructing the adapter with all its repos is heavy, look at an existing adapter unit test and copy its fixture/builder. Use the real `PosterCacheRepo` and a real `:memory:` aiosqlite conn with `apply_migrations`.)

- [ ] **Step 2: Run to verify fail** — the adapter doesn't accept/!use `poster_cache_repo` yet.

- [ ] **Step 3: Implement.**

  **3a.** In `backend/app/archive/registry.py`, add `poster_cache_repo: Any = None` to `build_archive_provider`'s signature and pass `poster_cache_repo=poster_cache_repo` into the `CatdvArchiveAdapter(...)` construction.

  **3b.** In `backend/app/archive/providers/catdv/adapter.py` `__init__`, accept `poster_cache_repo: Any = None` and store `self._poster_cache = poster_cache_repo`.

  **3c.** In `list_clips`, after `await self._write_list_through(...)` and before `return page`, add a poster-cache write built from the **raw** items (not the canonical page), so posterID is read straight from the payload:

```python
        await self._write_poster_cache(raw_items or [])
        return page
```

  and add the helper (place near `_write_list_through`):

```python
    async def _write_poster_cache(self, raw_items: list[dict[str, Any]]) -> None:
        if self._poster_cache is None:
            return
        entries = [
            (int(raw["ID"]), int(raw["posterID"]))
            for raw in raw_items
            if raw.get("ID") is not None and raw.get("posterID")
        ]
        if not entries:
            return
        await self._poster_cache.upsert_many(
            self._db_provider(), provider_id=self.id, entries=entries
        )
```

  (`raw_items` is already bound in `list_clips` at `raw_items = data.get("items") ...`.)

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/unit/test_catdv_list_populates_poster_cache.py -v` plus the existing adapter tests `tests/unit/ -k catdv` to confirm no regression.

- [ ] **Step 5: Commit**
```bash
git add backend/app/archive/registry.py backend/app/archive/providers/catdv/adapter.py tests/unit/test_catdv_list_populates_poster_cache.py
git commit -m "feat(catdv): populate poster_cache from list payload (write-through)"
```

---

### Task 3: ThumbnailService poster-cache fallback + download throttle

**Files:** Modify `backend/app/services/thumbnail_service.py`; Test `tests/unit/test_thumbnail_service.py`.

**Context:** Add `poster_id_provider: Callable[[int], Awaitable[int | None]] | None = None` and `download_concurrency: int = 3` to `__init__`. Create `self._download_sem = asyncio.Semaphore(download_concurrency)` and `self._poster_id_provider = poster_id_provider`. Extract a `_download_and_store` helper (semaphore-wrapped) used by both the metadata-cached path and the new poster-cache fallback. On a metadata-gate miss, instead of `return None`, call the poster-cache fallback.

- [ ] **Step 1: Write failing tests** — append to `tests/unit/test_thumbnail_service.py`:

```python
import asyncio as _asyncio


class _PosterProvider:
    def __init__(self, mapping: dict[int, int]):
        self.mapping = mapping
        self.calls: list[int] = []

    async def __call__(self, clip_id):
        self.calls.append(clip_id)
        return self.mapping.get(clip_id)


@pytest.mark.asyncio
async def test_poster_cache_fallback_downloads_without_get_clip(tmp_path: Path):
    # metadata gate says "not cached" -> use poster cache, download directly,
    # never call archive.get_clip.
    catdv = _FakeCatdv()
    poster = _PosterProvider({42: 882156})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
    )
    out = await svc.get_or_fetch(42)
    assert out == tmp_path / "42.jpg"
    assert catdv.calls == [882156]      # downloaded the poster id from cache
    assert poster.calls == [42]


@pytest.mark.asyncio
async def test_poster_cache_miss_returns_none(tmp_path: Path):
    catdv = _FakeCatdv()
    poster = _PosterProvider({})        # no entry
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_no_poster_provider_gate_still_terminal(tmp_path: Path):
    # Without a poster provider, the gate miss is terminal as before.
    catdv = _FakeCatdv()
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=catdv,
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
    )
    assert await svc.get_or_fetch(42) is None
    assert catdv.calls == []


@pytest.mark.asyncio
async def test_download_concurrency_is_bounded(tmp_path: Path):
    # Many concurrent fetches must not exceed download_concurrency in-flight.
    state = {"now": 0, "max": 0}

    class _SlowCatdv:
        async def download_thumbnail(self, thumb_id, dest, **kw):
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
            await _asyncio.sleep(0.02)
            state["now"] -= 1
            Path(dest).write_bytes(b"\xff\xd8x")

    poster = _PosterProvider({i: 1000 + i for i in range(10)})
    svc = ThumbnailService(
        cache_dir=tmp_path, archive=_RecordingArchive(), catdv=_SlowCatdv(),
        is_online_provider=lambda: True,
        metadata_cached_provider=lambda _cid: False,
        poster_id_provider=poster,
        download_concurrency=3,
    )
    outs = await _asyncio.gather(*[svc.get_or_fetch(i) for i in range(10)])
    assert all(o is not None for o in outs)
    assert state["max"] <= 3
```

(`_RecordingArchive` already exists in this file and raises if `get_clip` is called — so the first test also proves `get_clip` is NOT called on the poster-cache path.)

- [ ] **Step 2: Run to verify fail** — `TypeError: unexpected keyword argument 'poster_id_provider'`.

- [ ] **Step 3: Implement** in `backend/app/services/thumbnail_service.py`:

  **3a.** Add `import asyncio` (already present) and, in `__init__`, two params after `metadata_cached_provider` (and after the existing `durable_store` param):
```python
        poster_id_provider: Callable[[int], Awaitable[int | None]] | None = None,
        download_concurrency: int = 3,
```
  and after `self._durable = durable_store`:
```python
        # Fallback poster-id source for clips that are listed but not in
        # clip_cache: lets the thumb fetch proceed without get_clip (ADR 0072).
        self._poster_id_provider = poster_id_provider
        # Bound concurrent CatDV thumbnail downloads so a list-load's burst of
        # <img> requests doesn't stampede the seat-limited server.
        self._download_sem = asyncio.Semaphore(download_concurrency)
```

  **3b.** Replace the metadata-gate `return None` with the poster-cache fallback. Current:
```python
            if not result:
                # No clip_cache row → posterID is unknowable; skip CatDV.
                return None
```
  becomes:
```python
            if not result:
                # No clip_cache row. Try the lightweight poster cache populated
                # when listing — gives posterID without a get_clip (ADR 0072).
                return await self._fetch_via_poster_cache(clip_id, dest)
```

  **3c.** Replace the existing CatDV download tail of `get_or_fetch` (the `try: await self._catdv.download_thumbnail(...)` block through the final `return dest / return None`) with a single call:
```python
        return await self._download_and_store(int(thumb_id), clip_id, dest)
```

  **3d.** Add the two helpers (place after `get_or_fetch`, before `push_durable`):
```python
    async def _fetch_via_poster_cache(self, clip_id: int, dest: Path) -> Path | None:
        if self._poster_id_provider is None:
            return None
        poster_id = await self._poster_id_provider(clip_id)
        if not poster_id:
            return None
        return await self._download_and_store(int(poster_id), clip_id, dest)

    async def _download_and_store(self, thumb_id: int, clip_id: int, dest: Path) -> Path | None:
        async with self._download_sem:
            try:
                await self._catdv.download_thumbnail(thumb_id, dest)
            except Exception as exc:  # noqa: BLE001 — transport / auth / 404
                _log.debug("thumb: download(%s) failed: %s", clip_id, exc)
                if dest.exists() and dest.stat().st_size == 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                    dest.unlink(missing_ok=True)  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
                return None
        if dest.exists() and dest.stat().st_size > 0:  # sync-io-ok: pre-existing, tracked for the tier-4 async-io pass
            if self._durable is not None:
                await self._durable.put(clip_id, dest)
            return dest
        return None
```

  Keep the `_build_image_poster` path as-is (it already pushes durable; it's the rare stills path, left unthrottled).

- [ ] **Step 4: Run to verify pass** — `.venv/bin/python -m pytest tests/unit/test_thumbnail_service.py tests/unit/test_thumbnail_uploaded_guard.py tests/unit/test_thumbnail_service_image.py tests/unit/test_no_sync_fs_in_async.py -v`. All green (new + pre-existing). PIL must be installed for the image test (`uv pip install --python .venv/bin/python "pillow>=10.4"` if missing).

- [ ] **Step 5: Commit**
```bash
git add backend/app/services/thumbnail_service.py tests/unit/test_thumbnail_service.py
git commit -m "feat(thumbnail): poster-cache fallback + bounded download concurrency"
```

---

### Task 4: wire poster cache into context

**Files:** Modify `backend/app/context.py`.

- [ ] **Step 1: Implement** four edits:

  **4a.** Add the import near the other repo imports:
```python
from backend.app.repositories.poster_cache import PosterCacheRepo
```

  **4b.** Add a `CoreCtx` field next to `proxy_cache_repo` / `clip_list_cache_repo`:
```python
    poster_cache_repo: PosterCacheRepo = field(default_factory=PosterCacheRepo)
```

  **4c.** In `_build_archive_subsystem`, pass the repo into `build_archive_provider(...)` (alongside `clip_list_cache_repo=core.clip_list_cache_repo`):
```python
        poster_cache_repo=core.poster_cache_repo,
```

  **4d.** In the `ThumbnailService(...)` construction (added in the durable-cache work), add a `poster_id_provider` closure that reads the poster cache. Just before the construction:
```python
        async def _poster_id(clip_id: int) -> int | None:
            return await core.poster_cache_repo.get_poster_id(
                core.db, provider_id=archive.id, provider_clip_id=str(clip_id)
            )
```
  and pass `poster_id_provider=_poster_id,` as a kwarg to `ThumbnailService(...)`. (Leave `download_concurrency` at its default 3.)

- [ ] **Step 2: Verify** — `.venv/bin/python -c "import backend.app.context"`; `.venv/bin/python -m pytest tests/unit/test_context_delegation.py -v`; `.venv/bin/lint-imports` → 0 broken.

- [ ] **Step 3: Commit**
```bash
git add backend/app/context.py
git commit -m "feat(context): wire poster_cache repo + poster_id_provider into thumbnail service"
```

---

### Task 5: shared aesthetic empty-thumb placeholder

**Files:** Modify `backend/app/static/app.css`, `backend/app/templates/pages/_video_list.html`, `_clip_picker_basket.html`, `_studio_set_clip_card.html`, `backend/app/static/clipPicker.js`.

**Context:** Today the empty/broken state differs per surface: `_video_list` adds `.thumb--empty` (a gradient), `_clip_picker_basket` + `clipPicker.js` use `visibility:hidden` (blank), `_studio_set_clip_card` removes the img / uses a bare background. Unify on one styled placeholder: the existing `.thumb--empty` film-frame gradient **plus a centred muted glyph**, applied via the same `onerror` hook everywhere. Read `docs/design-language.md` first; use design tokens, not raw hex where a token exists.

- [ ] **Step 1: Upgrade the placeholder style** in `backend/app/static/app.css`. Replace the existing `.vlist .thumb--empty` rule with a shared, reusable version that also carries a centred glyph. Keep the gradient; add a centred film/image SVG glyph via `background-image` layered over the gradient:

```css
/* Shared empty/failed-thumbnail placeholder: film-frame gradient + a quiet
   centred glyph. Applied (via onerror) on every thumb surface so a genuinely
   posterless clip shows a clean placeholder, never a broken-image icon. */
.thumb--empty,
.vlist .thumb--empty,
.studio-clip-card .thumb.thumb-missing {
  animation: none;
  background-color: #2a221a;
  background-image:
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='24' height='24' fill='none' stroke='%237a6a52' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Crect x='3' y='5' width='18' height='14' rx='2'/%3E%3Cpath d='M3 9h18M7 5v14M17 5v14'/%3E%3C/svg%3E"),
    linear-gradient(135deg, #2a221a, #3c2e1f 60%, #4a3826);
  background-repeat: no-repeat, no-repeat;
  background-position: center, center;
  background-size: 40% auto, cover;
}
```

(Adjust the selectors to match the actual empty-state class each surface uses after Step 2; the goal is one rule covering list thumbs, picker thumbs, and studio cards.)

- [ ] **Step 2: Make every surface use the shared placeholder on error.**

  **2a.** `_clip_picker_basket.html:12` — change `onerror="this.style.visibility='hidden'"` to:
```html
onerror="this.classList.add('thumb--empty'); this.removeAttribute('src');"
```

  **2b.** `backend/app/static/clipPicker.js:104` — change the inline `onerror="this.style.visibility='hidden'"` in the rendered `<img class="thumb" ...>` to:
```js
onerror="this.classList.add('thumb--empty'); this.removeAttribute('src');"
```

  **2c.** `_studio_set_clip_card.html` — the img variant (line ~14-15) already adds `thumb-missing` to the parent `.thumb` and removes the img; ensure `.thumb.thumb-missing` is covered by the shared rule (Step 1 selector includes `.studio-clip-card .thumb.thumb-missing`). For the **background-image variant** (line ~21, `<div class="thumb" style="background-image:url(...)">`) there is no onerror; leave its happy path but ensure the `.thumb` base has a non-broken background (it already uses `var(--bg-2)`-style fill) so a 404 background simply shows the fill — acceptable. Do **not** restructure the card; only ensure the missing-state class is styled.

  **2d.** `_video_list.html:48` already adds `.thumb--empty` — no change needed beyond confirming the new shared rule renders correctly there.

- [ ] **Step 3: Verify** — design-language guard + template tests stay green:
```
.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py -v
```
Expected: pass. (If the guard flags a raw hex, swap to a design token from app.css `:root`.)

- [ ] **Step 4: Commit**
```bash
git add backend/app/static/app.css backend/app/templates/pages/_video_list.html backend/app/templates/pages/_clip_picker_basket.html backend/app/templates/pages/_studio_set_clip_card.html backend/app/static/clipPicker.js
git commit -m "feat(ui): shared aesthetic empty-thumbnail placeholder across surfaces"
```

---

### Task 6: full verification + ADR

- [ ] **Step 1: Full suite** — `.venv/bin/python -m pytest tests/ -q --ignore=tests/unit/test_erosion_gate.py` (radon not installed locally). Expected: all green.
- [ ] **Step 2: Guards + lint** — `.venv/bin/python -m pytest tests/unit/test_no_sync_fs_in_async.py tests/unit/test_context_delegation.py tests/unit/test_templates_shared.py tests/unit/test_design_language_guard.py -q` and `.venv/bin/lint-imports` (0 broken).
- [ ] **Step 3: ADR 0072** — write `docs/adr/0072-thumbnail-poster-cache.md` (MADR-lite, match 0071): the metadata gate (ADR 0065) 404s listed-but-unopened clips; "write list data into clip_cache" rejected because get_clip is cache-first + list is a lighter projection (would regress detail); chosen a dedicated `poster_cache` (clip→posterID) populated on list, consulted by the thumb path as a get_clip-free fallback; bounded download concurrency (3) to protect the seat-limited server; unified aesthetic placeholder. Add the row to `docs/decisions.md`. Commit:
```bash
git add docs/adr/0072-thumbnail-poster-cache.md docs/decisions.md
git commit -m "docs: ADR 0072 thumbnail poster cache + download throttle"
```
- [ ] **Step 4: Deploy** — build + deploy per the handover cheatsheet, then re-verify on the live service: a previously-404 listed clip (e.g. 888717/888720) now returns 200 server-side without first opening it; the clip-list page shows real thumbnails (or the styled placeholder, never a broken icon). Disconnect to free the seat when done.

## Manual acceptance flows

1. **Listed-but-unopened clip thumbnails (the fix).** Fresh instance, Connect. Load the clip list. A clip never individually opened (its `clip_cache` row absent) now shows its real thumbnail. Server-side: `curl .../api/media/<listed-id>/thumb` → 200 without a prior `/api/catdv/clips/<id>` call.
2. **No stampede.** With many thumbnails loading on a cold instance, server logs / behavior show at most 3 concurrent CatDV thumbnail downloads in flight.
3. **Posterless clip placeholder.** A clip with no poster in CatDV shows the shared film-frame placeholder (centred glyph), not a broken-image icon — on the clips list, the clip-picker, and studio cards.
4. **Detail not regressed.** Open a clip that was only listed before; its detail page shows full metadata (fields, etc.) — confirming the poster cache did not poison `clip_cache` (`get_clip` still fetches full detail on open).
5. **Offline still works.** Disconnect; previously-cached/GCS thumbnails still render (durable cache intact); uncached ones show the placeholder.
