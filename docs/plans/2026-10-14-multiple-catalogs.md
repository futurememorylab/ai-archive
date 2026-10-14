# Multiple Catalogs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the active CatDV catalog in the topbar and let each browser switch it (cookie), with the catalog list cached in the DB for offline use.

**Architecture:** A cookie-backed `active_catalog_id(request, settings)` replaces the four request-path reads of `settings.catdv_catalog_id`. A `catalogs` table + `CatalogsRepo` cache the list fetched through a new `ArchiveProvider.list_catalogs()`. A lazily-loaded topbar partial (`/ui/catalog-picker`, built from `ui.menu`) posts to `/api/catalog/active`, which sets the cookie and redirects to `/`.

**Tech Stack:** FastAPI, aiosqlite, Jinja2 + HTMX + Alpine (`popover()`), pytest, Playwright walkthroughs.

**Spec:** `docs/specs/2026-10-14-multiple-catalogs-design.md`

## Global Constraints

- Cookie name `active_catalog`; `max_age=365*24*3600`, `httponly=True`, `samesite="lax"`, `path="/"`.
- Default catalog = `settings.catdv_catalog_id` when cookie missing / non-numeric.
- Catalog list TTL = 1 hour (`CATALOG_LIST_TTL = timedelta(hours=1)`).
- Fallback display name = `f"Catalog #{catalog_id}"`.
- Migration file `backend/migrations/0030_catalogs.sql`.
- UI uses only `ui.menu` / `ui.menu_item` / `ui.menu_header` (design-language guard).
- No sync fs I/O in `async def`; repos import no services; routes don't import `httpx`.
- User-facing error strings via `services/errors.py::humanise`.
- Writeback is untouched (clip IDs are server-wide — verified in spec).

**Spec deviations (deliberate, smaller):** the "CatalogService" is two module
functions in `services/active_catalog.py` taking `(core, live)` instead of a
class on `CoreCtx` — no ctx field, no late binding needed. The switch endpoint
is a plain form POST (the `ui.menu_item(post=…)` shape), so an invalid id
returns a bare 400 rather than a toast; it is only reachable by a crafted
request since the menu lists valid ids only.

## Review Focus

1. Cookie value with junk (`"abc"`, `""`, `"12; x"`) → silently the default catalog, never a 500.
2. CatDV `/catalogs` refresh raising (timeout, 5xx, auth) → picker still renders the stale list; page never 502s because of the picker.
3. App booted offline (`live_ctx is None`) → `/ui/catalog-picker` renders from DB / synthetic row; `POST /api/catalog/active` still works for cached ids.
4. Cookie pointing at a catalog not in the list → picker shows it (synthetic row) as current so the user can see where they are and switch away.
5. Existing walkthroughs (no cookie) keep showing the default catalog's clips — FakeArchive's per-catalog filtering must treat clips with no catalog as belonging to the default.

---

### Task 1: `CatalogInfo` + `list_catalogs` on client, CatDV adapter, FS adapter

**Files:**
- Modify: `backend/app/archive/model.py` (add dataclass after `FieldDef`)
- Modify: `backend/app/archive/provider.py` (protocol method)
- Modify: `backend/app/services/catdv_client.py` (after `list_fields`)
- Modify: `backend/app/archive/providers/catdv/adapter.py` (near `list_field_definitions`)
- Modify: `backend/app/archive/providers/fs/adapter.py:180-191`
- Test: `tests/unit/test_catdv_client_catalogs.py` (new), `tests/integration/test_fs_adapter.py:54-` (update)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class CatalogInfo: id: str; name: str; read_only: bool = False`
- Produces: `CatdvClient.list_catalogs(self) -> list[dict[str, Any]]` — `GET /catdv/api/9/catalogs` via `_call_json`; returns `env.data` if list else `[]`.
- Produces: `ArchiveProvider.list_catalogs(self) -> list[CatalogInfo]`; CatDV adapter maps `{"ID","name","readOnly"}` → `CatalogInfo(str(ID), name, bool(readOnly))`, translating `CatdvAuthError`→`AuthError` and transport errors → `ProviderError` the same way `list_field_definitions` does. No caching in the adapter.

- [ ] **Step 1: Failing tests**
  - `test_list_catalogs_returns_data_items`: mock transport (same style as existing `catdv_client` unit tests) answers `GET /catdv/api/9/catalogs` with `{"status":"OK","data":[{"ID":881507,"name":"AI katalog","readOnly":false},{"ID":913722,"name":"vyber pro AI","readOnly":true}]}` → result has 2 dicts, `[0]["ID"] == 881507`.
  - `test_catdv_adapter_list_catalogs_maps_to_catalog_info`: adapter with a stub client whose `list_catalogs` returns the two dicts → `[CatalogInfo("881507","AI katalog",False), CatalogInfo("913722","vyber pro AI",True)]`.
  - Update `test_list_catalogs_excludes_hidden_and_dotarchive` to assert `[c.id for c in catalogs]` and `isinstance(catalogs[0], CatalogInfo)`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/unit/test_catdv_client_catalogs.py tests/integration/test_fs_adapter.py -q` → FAIL (no attribute / no class).
- [ ] **Step 3:** Implement the three methods + protocol line + dataclass.
- [ ] **Step 4:** Same command → PASS.
- [ ] **Step 5:** Commit `feat(#124): list catalogs through the archive provider`.

### Task 2: `catalogs` table + `CatalogsRepo` on `CoreCtx`

**Files:**
- Create: `backend/migrations/0030_catalogs.sql` (DDL from spec §2, plus a leading `--` comment line)
- Create: `backend/app/repositories/catalogs.py`
- Modify: `backend/app/context.py` (import + `catalogs_repo: CatalogsRepo = field(default_factory=CatalogsRepo)` next to `clip_list_cache_repo`)
- Test: `tests/integration/test_catalogs_repo.py`

**Interfaces:**
- Produces: `class CatalogsRepo`:
  - `async def list(self, conn, *, provider_id: str) -> list[CatalogInfo]` — ordered by `name COLLATE NOCASE`.
  - `async def fetched_at(self, conn, *, provider_id: str) -> str | None` — `MIN(fetched_at)`, None when empty.
  - `async def replace_all(self, conn, *, provider_id: str, catalogs: list[CatalogInfo], fetched_at: str) -> None` — DELETE for provider + executemany INSERT + one `commit()`.

- [ ] **Step 1: Failing tests** (use the `db` fixture from `tests/integration/conftest.py`):
  - `test_replace_all_then_list_round_trip`: replace with two catalogs → `list` returns both, sorted by name, `read_only` preserved; `fetched_at` returns the stamp.
  - `test_replace_all_drops_vanished_catalogs`: replace with A,B then with B only → `list == [B]`.
  - `test_empty_provider_fetched_at_is_none`.
  - `test_list_is_scoped_by_provider`: rows for `"fs"` not returned for `"catdv"`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/integration/test_catalogs_repo.py -q` → FAIL.
- [ ] **Step 3:** Migration + repo + ctx field.
- [ ] **Step 4:** Same → PASS; also `.venv/bin/python -m pytest tests/unit/test_context_delegation.py -q` → PASS (CoreCtx-fields ⊆ LiveCtx-accessors drift guard: add the matching delegator property on `LiveCtx` if it fails).
- [ ] **Step 5:** Commit `feat(#124): catalogs cache table + repo`.

### Task 3: Active-catalog resolution + cached list functions

**Files:**
- Create: `backend/app/services/active_catalog.py`
- Test: `tests/unit/test_active_catalog.py`

**Interfaces:**
- Consumes: `CatalogsRepo` (Task 2), `ArchiveProvider.list_catalogs` (Task 1).
- Produces:
  - `COOKIE = "active_catalog"`, `CATALOG_LIST_TTL = timedelta(hours=1)`
  - `def active_catalog_id(request: Request, settings: Settings) -> str` (body in spec §1).
  - `def is_live_online(live: LiveCtx | None) -> bool` — `live is not None`, monitor not forced (`is_forced`/`_forced_offline`), `current_state() == ConnectionState.online`.
  - `async def list_catalogs(core: CoreCtx, live: LiveCtx | None, active_id: str, *, now: datetime | None = None) -> list[CatalogInfo]` — algorithm in spec §2 steps 1-3; `provider_id = core.settings.archive_provider`; refresh failure → `logger.warning("catalog list refresh failed: %s", humanise(exc))`, catch `Exception` only.
  - `async def catalog_name(core: CoreCtx, catalog_id: str) -> str` — DB lookup, fallback `f"Catalog #{catalog_id}"`.

- [ ] **Step 1: Failing tests** (fake `core` = real `CoreCtx`-like object with `db` from an in-memory migrated DB is heavy; instead use a `SimpleNamespace(db=…, settings=SimpleNamespace(archive_provider="catdv", catdv_catalog_id=881507), catalogs_repo=CatalogsRepo())` over the conftest `db`, so these live in `tests/integration/test_active_catalog.py` for the DB ones and `tests/unit/test_active_catalog.py` for the pure ones):
  - unit `test_cookie_wins` (`"913722"` → `"913722"`), `test_missing_cookie_defaults`, `test_non_numeric_cookie_defaults` for `"abc"`, `""`, `"12; x"` → `"881507"`.
  - unit `test_is_live_online`: None → False; forced → False; state offline → False; online → True.
  - integration `test_fresh_rows_skip_provider`: rows stamped `now-10min`, archive stub whose `list_catalogs` raises `AssertionError` → returns rows.
  - `test_stale_rows_refresh_when_online`: rows stamped `now-2h`, stub returns `[A,B,C]` → result has 3 and repo now has 3.
  - `test_stale_rows_served_offline`: stale rows, `live=None` → stale rows, stub not called.
  - `test_refresh_failure_serves_stale`: stub raises `ProviderError("boom")` → stale rows, no raise.
  - `test_empty_offline_gives_synthetic_active`: empty table, `live=None`, active `"913722"` → `[CatalogInfo("913722","Catalog #913722",False)]`.
  - `test_unknown_active_appended`: rows A,B; active `"999"` → A,B + synthetic 999.
  - `test_catalog_name_lookup_and_fallback`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/unit/test_active_catalog.py tests/integration/test_active_catalog.py -q` → FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Same → PASS.
- [ ] **Step 5:** Commit `feat(#124): resolve active catalog from cookie + cached catalog list`.

### Task 4: Switch request-path readers to the active catalog

**Files:**
- Modify: `backend/app/routes/pages/clips.py:268` and `:338-341` (breadcrumb name via `await catalog_name(ctx.core, catalog_id)`; `"id": catalog_id`)
- Modify: `backend/app/routes/catdv.py:18`
- Modify: `backend/app/routes/batches.py:105`
- Modify: `backend/app/routes/ui.py:64,76`
- Test: `tests/integration/test_active_catalog_routes.py` (new; env/app setup copied from `test_routes_batches.py::_make_client`, `install_live_ctx` with a recording fake archive)

**Interfaces:**
- Consumes: `active_catalog_id`, `catalog_name` (Task 3).

- [ ] **Step 1: Failing tests** — fake archive records the `catalog` arg of `list_clips` and returns an empty `ClipPage`:
  - `test_clips_page_uses_cookie_catalog`: cookie `active_catalog=913722`, seed `catalogs` row `913722 vyber pro AI` → `GET /` 200, recorded catalog `"913722"`, body contains `vyber pro AI · #913722`.
  - `test_clips_page_defaults_without_cookie`: no cookie → recorded `"7"` (env `CATDV_CATALOG_ID=7`), body contains `Catalog #7`.
  - `test_api_catdv_clips_uses_cookie`: `GET /api/catdv/clips` with cookie → recorded `"913722"`.
  - `test_batch_picker_rows_use_cookie`: the batches picker-rows endpoint (route at `batches.py:~85`) with cookie → recorded `"913722"`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/integration/test_active_catalog_routes.py -q` → FAIL.
- [ ] **Step 3:** Swap the four readers; delete the `"AI katalog"` literal.
- [ ] **Step 4:** Same → PASS; then `.venv/bin/python -m pytest tests/integration/test_clips_page_perf.py -q` → PASS (query-count guard: the name lookup adds one constant query — bump the documented inventory + expected count by 1 if it fails, never make it per-row).
- [ ] **Step 5:** Commit `feat(#124): list surfaces follow the active catalog`.

### Task 5: Topbar picker partial + switch endpoint

**Files:**
- Create: `backend/app/routes/catalogs.py` (`router = APIRouter(tags=["catalogs"])`)
- Modify: `backend/app/main.py` (include router next to `ui_router`)
- Create: `backend/app/templates/pages/_catalog_picker.html`
- Modify: `backend/app/templates/pages/layout.html:51` (after the brand `<a>`)
- Test: `tests/integration/test_catalog_picker.py`

**Interfaces:**
- Consumes: Task 3 functions.
- Produces:
  - `GET /ui/catalog-picker` → partial; context `{"catalogs": list[CatalogInfo], "active_id": str, "active_name": str}`; `Cache-Control: no-store`.
  - `POST /api/catalog/active` form field `catalog_id` → valid: `303` to `/` + cookie (Global Constraints values); invalid: `400`, no cookie.
- Layout container: `<div id="catalog-picker" data-test="catalog-picker" hx-get="/ui/catalog-picker" hx-trigger="load" hx-swap="innerHTML"></div>`.
- Partial: `{% call ui.menu(label=active_name, trigger_attrs='data-test="catalog-picker-trigger"') %}` → `ui.menu_header("Catalog")` + per catalog `ui.menu_item(c.name, post="/api/catalog/active", current=(c.id == active_id), meta=("read-only" if c.read_only else None), desc="#" ~ c.id, attrs='name="catalog_id" value="' ~ c.id ~ '" data-test="catalog-option-' ~ c.id ~ '"')`.

- [ ] **Step 1: Failing tests:**
  - `test_picker_lists_cached_catalogs_offline`: app booted offline (no `install_live_ctx`), seed two rows → `GET /ui/catalog-picker` body has both names, `is-current` on the default's item, `read-only` on the read-only one.
  - `test_picker_marks_cookie_catalog_current`: cookie `913722` → the `catalog-option-913722` element carries `is-current`.
  - `test_post_active_sets_cookie_and_redirects`: `POST` `catalog_id=913722` (`follow_redirects=False`) → 303, `location == "/"`, `set-cookie` contains `active_catalog=913722`, `HttpOnly`, `SameSite=lax`.
  - `test_post_unknown_catalog_400`: `catalog_id=999` → 400, no `set-cookie`.
  - `test_post_non_numeric_400`: `catalog_id=abc` → 400.
  - `test_layout_has_picker_container`: `GET /prompts` body contains `id="catalog-picker"`.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/integration/test_catalog_picker.py -q` → FAIL.
- [ ] **Step 3:** Implement route, partial, layout hook. Style only via existing tokens / classes (add at most a tiny `#catalog-picker` margin rule in `app.css` if the trigger sits flush against the brand).
- [ ] **Step 4:** Same → PASS; `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/unit/test_templates_shared.py -q` → PASS; `lint-imports` → no broken contracts.
- [ ] **Step 5:** Commit `feat(#124): topbar catalog picker + switch endpoint`.

### Task 6: Walkthrough fixtures + three recorded scenarios

**Files:**
- Modify: `tests/walkthrough/fakes.py` (`FakeArchive`)
- Modify: `tests/walkthrough/seed.py` (catalogs rows + second-catalog clips)
- Modify: `tests/walkthrough/app_server.py` only if clips for the fake are assembled there
- Create: `tests/walkthrough/scenarios/_catalog_support.py`, `catalog_picker_visible.py`, `catalog_switch.py`, `catalog_switch_offline.py`
- Test: `tests/walkthrough/test_fakes_search_unit.py` (add catalog filter unit test)

**Interfaces:**
- `FakeArchive.__init__(clips, catalogs: tuple[CatalogInfo, ...] = (), clip_catalogs: dict[str, str] | None = None, default_catalog: str = "881507")` — `clip_catalogs` maps clip id → catalog id; unmapped clips belong to `default_catalog`. `list_clips(catalog, q)` filters to clips whose catalog == `catalog`. `async def list_catalogs()` returns `list(catalogs)`.
- Seed: catalogs `881507 AI katalog`, `913722 vyber pro AI`; two clips in 913722 named `ARNOLD Bogdan Sis 02` and `BENES Jana 01` (ids not colliding with existing fixtures), and their pages in `clip_list_cache` for 913722 so the offline scenario has cached clips.
- Scenarios: `TOPIC = "Catalogs"`; slugs `catalog-picker-visible`, `catalog-switch`, `catalog-switch-offline`; steps exactly as spec §5 "New scenarios". Use `expect(...)` on `data-test` hooks (`catalog-picker-trigger`, `catalog-option-<id>`), breadcrumb text, and clip row names. Offline scenario disconnects via the existing connection-chip control and reconnects at the end.

- [ ] **Step 1: Failing unit test** `test_fake_archive_filters_by_catalog`: two clips, one mapped to 913722 → `list_clips("913722")` returns 1, `list_clips("881507")` returns the other.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/walkthrough/test_fakes_search_unit.py -q` → FAIL.
- [ ] **Step 3:** Implement fake + seed + scenarios.
- [ ] **Step 4:** `.venv/bin/python -m tests.walkthrough.run --assert` → all scenarios pass (existing + 3 new).
- [ ] **Step 5:** `.venv/bin/python -m tests.walkthrough.run --record catalog-picker-visible catalog-switch catalog-switch-offline` → three `.webm` in `tests/walkthrough/artifacts/` and a "Catalogs" section in `artifacts/index.html`; eyeball one frame per video.
- [ ] **Step 6:** Commit `test(#124): catalog walkthrough scenarios`.

### Task 7: Full verification + live acceptance

- [ ] **Step 1:** `.venv/bin/python -m pytest -n auto -q` → all green; `lint-imports` → green.
- [ ] **Step 2:** Start the dev server via the `server-start` skill (seat check first), run spec manual acceptance flows 1–9 against real CatDV (both catalogs), then stop via `server-stop` and confirm `Application shutdown complete.`
- [ ] **Step 3:** Commit any fixes; push branch; open PR referencing #124 with the walkthrough videos noted.
