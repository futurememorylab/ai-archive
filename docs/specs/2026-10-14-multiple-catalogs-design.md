# Multiple catalogs — active-catalog picker (#124)

**Date:** 2026-10-14
**Issue:** [#124 Support for multiple catalogs](https://github.com/futurememorylab/ai-archive/issues/124)
**Status:** Draft — awaiting review

## Intent

So far the app has worked against exactly one CatDV catalog, fixed at boot by
`CATDV_CATALOG_ID`. Our service account now sees more than one. The user wants
to **see which catalog is active** and **switch to another** from the UI.

What the user decided (brainstorm, 2026-10-14):

- **Scope = per browser.** The active catalog lives in a cookie. Two people on
  the Cloud Run deployment can work in different catalogs at the same time.
  `CATDV_CATALOG_ID` stays as the default when no cookie is set.
- **List = whatever CatDV returns** for our service account
  (`GET /catdv/api/9/catalogs`). CatDV permissions are the filter; no admin
  allowlist. The list is cached in the DB so the picker works offline.
- **Walkthrough videos** (Playwright `--record`) must cover the new flows.

Assumptions (stated, not asked):

- **Verified (live probe 2026-10-14):** CatDV clip IDs are server-wide, not
  per catalog. The two catalogs' clip ID sets don't overlap (881507: 888700…,
  913722: 913813…), and `GET /clips/{id}` with no catalog parameter returns the
  right clip with its own `catalogID`. Writeback (`GET` + `PUT
  /catdv/api/9/clips/{id}`) therefore needs no catalog and is unchanged; clip
  detail / Studio / drafts keyed by clip ID work regardless of the active
  catalog. Only *list* surfaces are catalog-scoped.
- The same media added to two catalogs is two distinct clips (different IDs) —
  annotated and written back independently. No cross-catalog dedup.
- CatDV field definitions are server-wide, not per catalog — no change there.

Live probe (2026-10-14) of `GET /catdv/api/9/catalogs`: envelope
`{"status":"OK","data":[…]}`, each item carries `ID`, `name`, `readOnly`,
`groupID`, `groupName` (+ audit fields). Currently two catalogs:
`881507 AI katalog`, `913722 vyber pro AI` (both group `ai`).

## Current state

The data layer is already catalog-keyed: `clip_cache.catalog_id`,
`clip_list_cache(provider_id, catalog_id, …)`, `workspaces.catalog_id`,
`ArchiveProvider.list_clips(catalog, query)`. The single-catalog assumption
lives only in the **request-path readers** of `settings.catdv_catalog_id`:

| Reader | Use |
|---|---|
| `routes/pages/clips.py` (`clips_list`, + the hardcoded `"AI katalog"` name) | clips page list + breadcrumb |
| `routes/catdv.py::list_clips` | `/api/catdv/clips` (Studio clip picker) |
| `routes/batches.py` | catalog for a new batch |
| `routes/ui.py::workspace_switcher` | workspace creation |

`archive/registry.py` also reads it as `default_catalog_id` — the fallback the
CatDV adapter stamps on clip payloads that don't carry their catalog. That is
not "the active catalog" and stays as is.

## Design

### 1. Resolving the active catalog

New module `backend/app/services/active_catalog.py`:

```python
COOKIE = "active_catalog"

def active_catalog_id(request: Request, settings: Settings) -> str:
    raw = request.cookies.get(COOKIE, "")
    return raw if raw.isdigit() else str(settings.catdv_catalog_id)
```

The four readers above call it instead of `settings.catdv_catalog_id`. No
validation against the catalog list on read: a cookie naming a catalog that
has since vanished just yields an empty list, and the picker lets the user
switch away. (Keeps every page render DB-free for this concern.)

### 2. Catalog list — a small cache layer

Same shape as the other cache layers (CLAUDE.md → *Cache management*): a DB
index, a service with a graceful miss, an online gate.

- **Migration `0030_catalogs.sql`**

  ```sql
  CREATE TABLE catalogs (
    provider_id TEXT NOT NULL,
    catalog_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    read_only   INTEGER NOT NULL DEFAULT 0,
    fetched_at  TEXT NOT NULL,
    PRIMARY KEY (provider_id, catalog_id)
  );
  ```

- **Repo `repositories/catalogs.py`** — `list(db, provider_id)` and
  `replace_all(db, provider_id, rows, fetched_at)` (delete + insert in one
  transaction, through the write-serialized connection). Leaf — no service
  imports.

- **Provider** — add `list_catalogs() -> list[CatalogInfo]` to the
  `ArchiveProvider` protocol, with `CatalogInfo(id: str, name: str,
  read_only: bool)` in `archive/model.py`. The CatDV adapter implements it via
  a new `CatdvClient.list_catalogs()` (`GET /catdv/api/9/catalogs`, through
  `_call_json` so AUTH re-login works). The FS adapter's existing
  `list_catalogs()` is adapted to return `CatalogInfo` (read_only=False).

- **Service `services/catalog_service.py`** — `CatalogService.list(active_id)
  -> list[CatalogInfo]`:
  1. Read rows from the repo.
  2. If online (live ctx present and its connection monitor reports online —
     the same `_is_online` gate `context.py` injects into the CatDV adapter
     and thumbnail service) **and** rows are empty or older than
     `CATALOG_LIST_TTL = 1h`, call `archive.list_catalogs()` and
     `replace_all`. On failure: log a warning (`humanise(exc)`) and fall
     through to the stale rows — never raise.
  3. If the active id is not among the rows, append a synthetic
     `CatalogInfo(active_id, f"Catalog #{active_id}", False)` so the current
     catalog is always shown.

  The service lives on `CoreCtx` (DB-only reads are offline-safe); the refresh
  path asks the live context for the provider only when online. No
  `Optional` field, no late binding (ADR 0047): it takes a callable that
  returns the live archive or `None`.

- **Name lookup helper** — `CatalogService.name_for(catalog_id) -> str` (DB
  read; fallback `"Catalog #id"`) replaces the hardcoded `"AI katalog"` on the
  clips page breadcrumb.

### 3. UI

- **Topbar catalog menu.** In `pages/layout.html`, right after the brand, a
  container `<div id="catalog-picker" hx-get="/ui/catalog-picker"
  hx-trigger="load" hx-swap="innerHTML">` (same lazy pattern as the connection
  chip — no page route has to pass catalog data, and the CatDV refresh is off
  the page-render path).
- **Partial `pages/_catalog_picker.html`** built only from the shared library:
  `{{ ui.menu(label=<active name>) }}` with one `{{ ui.menu_item(...) }}` per
  catalog — `current=True` on the active one, `meta="read-only"` where
  `read_only`, `post="/api/catalog/active"` with the id in `attrs` /
  `hx-vals`. Header `{{ ui.menu_header("Catalog") }}`. `data-test` hooks:
  `catalog-picker`, `catalog-option-<id>`.
- **Switch endpoint** `POST /api/catalog/active` (`CoreCtx`), body/form
  `catalog_id`. Validates it is in `CatalogService.list(...)` → else 400
  (`humanise`-style message, surfaced via the toast store). On success: set
  cookie (`max_age=365d`, `httponly`, `samesite=lax`, `path=/`) and respond
  `HX-Redirect: /` (plain `303 → /` for non-HTMX). Redirecting to `/` is
  deliberate: the clips list is the catalog-scoped surface, and a paginated
  `offset` from catalog A is meaningless in catalog B.
- **Clips page breadcrumb** shows `catalog.name · #id` from `name_for`, and
  the `catalog <id>` meta label stays.

Not touched: the brand text, Studio/clip detail (clip-ID keyed), workspace
switcher markup (only its catalog source changes).

### 4. Error handling & offline

| Situation | Behaviour |
|---|---|
| Online, first visit, empty table | Picker fetches from CatDV, caches, renders |
| Offline, table populated | Picker renders the cached list; switching works (clips list falls back to `clip_cache` for that catalog — existing behaviour) |
| Offline, table empty | Picker shows only the active catalog (synthetic) |
| CatDV refresh fails (timeout/5xx) | Warning logged, stale rows served, no toast |
| Cookie names unknown / vanished catalog | Empty clip list; picker still lists real catalogs to switch away |
| `POST` with id not in list / non-numeric | 400 → error toast; cookie unchanged |
| Read-only catalog | Selectable; menu item shows "read-only". Writeback behaviour unchanged (CatDV rejects → existing pending-op failure path) |

### 5. Testing

TDD, slice by slice.

**Unit**
- `active_catalog_id`: cookie present → cookie; missing / non-numeric →
  settings default.
- `CatalogService.list`: fresh rows → no provider call; stale + online →
  refresh + replace; stale + offline → stale rows, no call; refresh raises →
  stale rows; active id missing → synthetic row appended.
- `CatdvClient.list_catalogs` maps envelope `data` → `CatalogInfo` (respx /
  existing httpx mocking pattern).
- Design-language guard stays green (picker uses `ui.menu`).

**Integration**
- Clips page with `active_catalog=913722` cookie calls
  `archive.list_clips("913722", …)` and shows that catalog's name in the
  breadcrumb; with no cookie uses the settings default.
- `/api/catdv/clips` and batch creation honour the cookie.
- `POST /api/catalog/active` sets the cookie + `HX-Redirect`; unknown id → 400,
  no `Set-Cookie`.
- `/ui/catalog-picker` offline renders from the DB without touching the
  provider.
- Repo `replace_all` round-trip.

**Walkthrough (Playwright, recorded as videos)**

Fixture changes:
- `FakeArchive` gains `list_catalogs()` and optional per-catalog clips: clips
  carry their `catalog_id`; `list_clips(catalog, …)` filters by it.
- `seed.py` seeds two catalogs (`881507 AI katalog`, `913722 vyber pro AI`)
  in the `catalogs` table and a couple of distinctively-named clips in the
  second catalog.

New scenarios (topic **"Catalogs"**, each one `.webm` in the gallery):

1. `catalog-picker-visible` — open `/`; the topbar shows "AI katalog"; open
   the menu; both catalogs listed, the active one marked current.
2. `catalog-switch` — switch to "vyber pro AI"; lands on `/`; breadcrumb and
   topbar show the new name; the list shows only the second catalog's clips;
   reload → still the second catalog (cookie persisted); switch back.
3. `catalog-switch-offline` — disconnect via the connection chip (existing
   control); picker still lists both catalogs from cache; switching shows the
   cached clips of the target catalog.

Existing scenarios must stay green (they run with no cookie → default
catalog, whose clips are the existing fixture set).

Run: `.venv/bin/python -m tests.walkthrough.run --assert` before merge,
`--record catalog-picker-visible catalog-switch catalog-switch-offline` to
produce the videos for the PR.

## Out of scope

- Admin allowlist / hiding catalogs (CatDV permissions are the filter).
- Per-catalog field definitions.
- Catalog in the URL / shareable catalog links.
- Multi-catalog (union) clip list.
- Renaming the "CatDV Annotator" brand text.

## Manual acceptance flows

Setup: dev server on `:8765` online (VPN up), service account sees ≥2
catalogs. Use a fresh browser profile (no `active_catalog` cookie).

1. **Catalog name visible.** Open `http://127.0.0.1:8765/`. Topbar shows
   "AI katalog" next to the brand; clips breadcrumb reads
   "AI katalog · #881507". Open `/prompts` and `/studio` — the topbar still
   shows "AI katalog".
2. **List of catalogs.** Click the topbar catalog menu. It lists
   "AI katalog" (marked current) and "vyber pro AI". Esc / click-outside
   closes it.
3. **Switch catalog.** Choose "vyber pro AI". You land on `/`; topbar and
   breadcrumb show "vyber pro AI · #913722"; clip list shows that catalog's
   clips (none from "AI katalog").
4. **Persists per browser.** Reload — still "vyber pro AI". Open the app in a
   second browser/profile — it shows "AI katalog" (default), unaffected.
5. **Clip from the other catalog still works.** In catalog "vyber pro AI",
   open a clip's detail page, play it, open it in Studio. Then switch back to
   "AI katalog" and open a previously-annotated clip — detail and drafts are
   intact.
6. **Batch in the active catalog.** In "vyber pro AI", create a batch / bulk
   annotate from the clips page; the job runs over clips of that catalog.
7. **Studio clip picker.** In Studio, open the archive clip picker — it lists
   clips of the active catalog.
8. **Offline.** Disconnect via the connection chip (go offline). The catalog menu still
   lists both catalogs; switching to the other one shows its cached clips
   (with the "Showing cached clips only" banner). Reconnect.
9. **Bad input.** `curl -X POST -d catalog_id=999 http://127.0.0.1:8765/api/catalog/active`
   → 400; in the browser nothing changes.
10. **Regression.** Existing walkthroughs pass (`/e2e` assert mode) and the
    three new Catalogs videos appear in the gallery after `--record`.
