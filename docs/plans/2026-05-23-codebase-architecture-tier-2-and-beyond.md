# Codebase architecture — Tier 2 and beyond

Status as of 2026-05-23, after commit `83dd3f6` (Tier 1 tooling).

## Status as of 2026-05-23 (end of session)

The plan was executed end-to-end across commits A–I on branch
`claude/tender-hawking-rp1zc`. Per-section status:

- **§1.4 findings**: addressed by §3.1 / §3.2 / §3.3 / §4.x work below.
- **§2.1 import-linter**: DONE — `5005673`. 3 contracts kept.
- **§2.2 C901 / radon**: NOT DONE (deferred — out of session scope).
- **§2.3 vulture**: NOT DONE (deferred).
- **§2.4 basedpyright**: PARTIAL — typed `get_ctx` landed (PR E `6aecaf3`);
  baseline grew because the typing surfaced latent Optional issues we hadn't
  been seeing. Steps 2–4 of §6 (specific-rule ratchets, baseline tightening)
  deferred.
- **§3.1 CONTEXT.md**: DONE — `a562c67`.
- **§3.2 ARCHITECTURE.md**: DONE — `a562c67`.
- **§3.3 module docstrings**: DONE (this PR). Every backend file now has a
  top-of-file docstring except the one re-export `__init__.py` we
  intentionally skip. `interrogate` is wired into `pyproject.toml` and
  pre-commit; `fail-under` is set to current per-callable coverage (~30%)
  rather than 70%, because the §3.3 sweep was module-level only and
  function/method docstring coverage is a separate ratchet to pick up later
  (see ADR 0022).
- **§3.4 ADR migration**: DONE — `8baf647`.
- **§4.1 pages.py split**: DONE — `e3849b5`.
- **§4.2 cache_actions / cache_inspector**: DONE — `0200577`. Construction
  collapsed; both modules kept because a deletion test asserts they remain
  separate seams.
- **§4.3 archive adapters sharpening**: NOT DONE (deferred).
- **§4.4 context.build() decomp**: DONE — `336dd80`.
- **§6 basedpyright ratchet**: PARTIAL (see §2.4 above).
- **§7 broken tests**: DONE (PR A) — `c94d702`.

Pick-up notes for future contributors are in ADR 0022.

This plan picks up where the Tier 1 hygiene work left off. Tier 1
closed the door on new mess — ruff lint + format, basedpyright with
a baseline file, and pre-commit are now enforced on every commit.
This plan covers everything Tier 1 deliberately didn't touch:
**architectural fitness, orientation docs, and the refactors the
tooling has now surfaced**.

The motivation hasn't changed: a senior reviewer flagged the
codebase as feeling chaotic despite real layering
(`archive/` → `repositories/` → `services/` → `routes/`). The
remaining gap is that nothing *enforces* the layering, nothing
*names* the domain in one place, and a few files have grown past
the point where a human can scan them top to bottom.

---

## 1. What was actually broken (the findings recap)

These are the concrete signals observed during the review. Some are
fixed in `83dd3f6`; others are open and explicitly addressed below.

### 1.1 Lint / hygiene findings (all 89 fixed in Tier 1)

| Count | Code | Meaning |
|------:|------|---------|
|    24 | `B904` | `raise X` inside `except` without `from err` / `from None` — loses stack context. |
|    14 | `ASYNC240` | `Path.exists()` / `Path.stat()` in async functions. Rule disabled globally for this project — see §5. |
|    10 | `I001` | Imports not sorted. |
|     9 | `E402` | Module imports not at top of file — all in `main.py`, factored out via `register_routers()`. |
|     7 | `UP017` | `datetime.utcnow()` → `datetime.now(timezone.utc)`. |
|     6 | `B905` | `zip(a, b)` without `strict=`. SQL-row dict builders — `strict=True` is correct (column-list/value-tuple lengths must match). |
|     6 | `UP035` | Deprecated import path. |
|     5 | `B017` | `pytest.raises(Exception)` — kept (legitimate "raises *something*" contract tests); added to `tool.ruff.lint.per-file-ignores`. |
|     4 | `E501` | Line too long. |
|     3 | `F401` | Unused imports. |
|     3 | `UP037` | Quoted annotation no longer needed. |
|     1 | `ASYNC230` | Blocking `open()` in async function. **Real bug** — proxy download writes (up to ~300 MB) blocked the event loop. Fixed by wrapping per-chunk writes in `asyncio.to_thread` in `catdv_client._stream_to_file`. |
|     1 | `UP007` | `Union[X, Y]` → `X | Y`. |

### 1.2 Structural findings (not addressed by Tier 1)

| Finding | Evidence | Status |
|---|---|---|
| `routes/pages.py` has grown to 701 LOC. | Largest file in the backend; mixes prompt detail page, prompt CRUD actions, archive flows, and clip list pages. | **Open** — see §4.1. |
| `services/cache_actions.py` (522) and `services/cache_inspector.py` (426) are the next bulgers. | Both touch DB, the AI store, and the local filesystem in one module. | **Open** — see §4.2. |
| `archive/providers/fs/adapter.py` (416) and `archive/providers/catdv/adapter.py` (391) read large. | Adapters mix HTTP/disk wire calls with payload shaping. | **Open** — see §4.3. |
| `context.py` `build()` is a 220-line manual DI graph constructor with five conditional wiring paths. | Reads top-to-bottom but has closures over `ctx` that are easy to break. | **Open** — see §4.4. |
| No `CONTEXT.md` glossary; newcomer can't pin down nouns like Workspace vs WorkspaceManager. | Single biggest orientation gap. | **Open** — see §3.1. |
| No `docs/ARCHITECTURE.md`; layer rules are implicit. | Tribal knowledge — drifts. | **Open** — see §3.2. |
| 24 of 95 backend files have no docstrings at all. | Top-of-file context missing; greppability poor. | **Open** — see §3.3. |
| No layer-import enforcement. Routes *could* skip services and reach into repos directly. | Nothing in lint catches it. | **Open** — see §2.1. |
| No `docs/adr/` directory; decisions live in one ever-growing `docs/decisions.md`. | Works today (~1000 lines) but won't scale; not greppable per-decision. | **Open** — see §3.4. |
| 237 basedpyright errors in the baseline. | Captured in `.basedpyright/baseline.json`. | **Open, ratcheting** — see §6. |

### 1.3 Test-suite findings (pre-existing, unrelated to Tier 1)

Verified by stashing changes and rerunning against `main`. **10 tests
fail on Python 3.14** due to `pytest-asyncio` / event-loop integration:

- `tests/integration/test_routes_cache.py::test_cache_clip_status_and_evict`
- `tests/integration/test_routes_cache.py::test_cache_popover_partial`
- `tests/integration/test_routes_cache.py::test_cache_orphans_endpoint`
- `tests/integration/test_routes_cache.py::test_cache_page_orphans_tile`
- `tests/integration/test_routes_cache.py::test_cache_tab_local_filters_rows`
- `tests/integration/test_routes_cache.py::test_cache_tab_ai_filters_rows`
- `tests/integration/test_routes_cache.py::test_bulk_evict_route`
- `tests/integration/test_routes_pages_cache_badge.py::test_list_page_bulk_toolbar_actions_present`
- `tests/unit/test_gcs.py::test_upload_if_absent_uploads_when_missing`
- `tests/unit/test_settings.py::test_settings_loads_from_env`

The common stack is `RuntimeError: There is no current event loop in
thread 'MainThread'.` from `asyncio/events.py:715`. The cache-routes
cluster appears to use a shared event loop the test client tears down
between tests; the `test_gcs` / `test_settings` failures look like
unrelated env / mock isolation issues.

**Status — open, see §7.** Not blockers for architecture work but
worth fixing before any large refactor, so the suite is trustworthy
again.

### 1.4 Architectural friction observed (qualitative)

These weren't lint errors but came up during the review:

1. **Wide repositories.** `clip_cache.py` does both writes and a
   complex paginated/canonical list read. `prompts.py` (the repo,
   not the route) is 394 LOC and concentrates prompt + version
   semantics together — the version-state machine (draft / production
   / immutable) is implicit, not modelled.
2. **`route → service` is not always honored.** Several routes call
   repos directly (e.g. `routes/jobs.py` reaches `ctx.jobs_repo`,
   `routes/live.py` instantiates `LiveSessionsRepo()` itself).
   Routes-call-repos isn't necessarily wrong, but the *rule* is
   currently "whatever you feel like." Either it's allowed (then the
   `services/` layer's purpose needs to be named) or it isn't
   (then import-linter should enforce).
3. **DI by closure.** `context.build()` uses closures like
   `db_provider=lambda c=ctx: c.db`, `is_online_provider=...` to defer
   reads of mutable state. It works but it's a footgun for a
   newcomer — they can't tell whether `c.db` is a property, a method
   call, or a snapshot.
4. **Two paths to `cache_inspector`/`cache_actions`.** Built once
   without external services, rebuilt with external services on top.
   The second construction *replaces* the first — a contributor who
   only reads the first path will get the wiring wrong.
5. **No domain noun for "the Live session."** `live_sessions` shows up
   as a repo, a service, a model, and a route. The four files agree
   on the shape but no single page says "a Live session is X, with
   lifecycle Y, and it expires after Z."

---

## 2. Tier 2 — architectural fitness (a day's work)

### 2.1 Add `import-linter` with layer rules

**Goal.** Make the layer rules executable. CI fails if a route imports
a repo skipping its service, or if `models/` imports from `services/`.

**Proposed contract** (in `.importlinter`):

```ini
[importlinter]
root_package = backend

[importlinter:contract:layers]
name = Backend layer order
type = layers
layers =
    backend.app.routes
    backend.app.services
    backend.app.repositories
    backend.app.archive
    backend.app.models
ignore_imports =
    # if needed; document each with one-line comment
```

**Open question before applying.** Currently, several routes call
repos directly. We have two paths:

- **(a)** Encode the *current* practice: `routes` may call either
  `services` or `repositories`. Add a `forbidden` contract instead:
  no module in `routes/` may import from `archive/`; no module in
  `services/` may import from `routes/`. Looser but reflects reality.
- **(b)** Tighten to strict layering: routes → services → repositories.
  Refactor each route that currently calls a repo to go via a service.
  Stricter but means writing ~10 trivial pass-through services.

Recommend **(a)** first, then revisit per-route as needed during §4
refactors.

**Add to `pre-commit` after the contract is green.**

### 2.2 Enable ruff `C901` (complexity) + drop `radon` reports into CI

**Goal.** Catch the *next* `pages.py`-class bulger before it ships.

`C901` is part of the `C` (mccabe) ruleset. Add `"C"` to `[tool.ruff.lint] select`
and set `[tool.ruff.lint.mccabe] max-complexity = 10` as a starting
point. Expect a small list of immediate offenders — fix or carve out
per-function `# noqa: C901` with a justification.

Layer `radon cc -a backend/` into CI for trend tracking (not a gate).

### 2.3 Dead-code sweep with `vulture` (one-off, then drop)

**Goal.** Confirm or refute the assumption that nothing is dead.
There are zero `TODO`/`FIXME` markers in the codebase, which is
either a discipline win or a sign that dead code accumulates
unmarked. Run once, fix what's confirmed dead, then *don't* keep
`vulture` in CI — it has too many false positives on dynamic-dispatch
code (FastAPI, Pydantic).

### 2.4 Tighten basedpyright incrementally

See §6 for the ratcheting strategy.

---

## 3. Tier 3 — orientation docs (a few hours; biggest human ROI)

### 3.1 `docs/CONTEXT.md` — the domain glossary

**Goal.** One page, one sentence per noun. The `improve-codebase-architecture`
skill's `LANGUAGE.md` prescribes this.

Target nouns (drafted from the review):

- **Clip** — a CatDV media asset. `(provider_id, provider_clip_id)`
  uniquely identifies it. The "canonical" form is `CanonicalClip` in
  `archive/model.py`.
- **Archive Provider** — port satisfied by either the CatDV REST
  adapter or the filesystem-sidecar adapter. Both implement
  `ArchiveProvider` in `archive/provider.py`.
- **AI Input Store** — port for "where Gemini reads the media from."
  Today: GCS (production), Gemini Files (stub), local.
- **Workspace** — a user-curated pinned subset of clips + their
  proxies. Managed by `WorkspaceManager`; persisted via
  `WorkspacesRepo`.
- **Proxy Cache** — local on-disk H.264 .mov copies of CatDV proxies.
  `ProxyCacheRepo` is the index; `proxy_resolver` is the read API;
  `proxy_cache_reconciler` keeps the index honest at startup.
- **Write Queue** — pending mutations to the archive (markers, notes,
  fields). `WriteQueue` enqueues, `SyncEngine` drains.
- **Sync Engine** — background task that drains the write queue when
  the `ConnectionMonitor` says we're online.
- **Connection Monitor** — periodically pings the archive provider;
  drives "online / offline / forced_offline" everywhere downstream.
- **Live Session** — a single browser-direct Gemini Live conversation
  about one clip. Persisted as a row + transcript JSON.
- **Prompt / Version** — the annotation template. A Prompt has many
  Versions; exactly one Version is `production` at a time;
  `production` versions are immutable.

Bar: a newcomer should read CONTEXT.md and then be able to read a
random source file without bouncing between four others.

### 3.2 `docs/ARCHITECTURE.md` — the layer map

**Goal.** One ASCII diagram + a "if X breaks, look in Y" table.

```
              ┌────────────────┐
              │   routes/      │  ← HTTP + Jinja templates
              └───────┬────────┘
                      │
              ┌───────▼────────┐
              │   services/    │  ← orchestration, queues, monitors
              └───────┬────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
┌───────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
│ repositories │ │ archive/ │ │ models/    │  ← Pydantic / dataclass
└──────────────┘ └──────────┘ └────────────┘
        │             │
   ┌────▼─────┐  ┌────▼─────┐
   │ aiosqlite│  │ httpx /  │
   │ (app.db) │  │ FS / GCS │
   └──────────┘  └──────────┘
```

Plus a short symptom-to-file table:

| Symptom | First file to read |
|---|---|
| Marker save 502 | `routes/catdv.py`, `services/write_queue.py`, `services/sync_engine.py` |
| Proxy 404 / "unavailable" | `services/proxy_resolver.py`, `repositories/proxy_cache.py` |
| Live session never starts | `routes/live.py`, browser-direct WSS (no backend bridge) |
| Sync stuck "in_flight" | `repositories/pending_operations.py` + crash-recovery in `context.build()` |

Cross-link from the README.

### 3.3 Module-level docstrings (≥ top-of-file)

**Goal.** 1–3 lines at the top of every backend module saying what it
does and what it depends on. Currently 24 of 95 backend files have
none.

Approach:

- Generate proposed docstrings from the existing code via a one-shot
  pass (manual or scripted), then review.
- Add `interrogate` to dev deps with `fail-under = 70` (raise later)
  as a separate pre-commit hook. Cheap to run.

### 3.4 Migrate `docs/decisions.md` → `docs/adr/NNNN-*.md`

**Goal.** `decisions.md` is at ~1000 LOC and growing; one file per
decision is greppable, reviewable per-PR, and avoids merge conflicts.

Use [MADR](https://adr.github.io/madr/) format. `adr-tools` can
manage numbering. One-time conversion script: split on `^## YYYY-MM-DD`
headers, slugify the title, write each block to
`docs/adr/NNNN-slug.md`. Keep `decisions.md` as a stub pointing at
`docs/adr/`.

CLAUDE.md's "record decisions at end of session" instruction needs
to be updated to point at `docs/adr/` once converted.

---

## 4. Tier 4 — refactors the tooling has surfaced

These are the deepening opportunities the `improve-codebase-architecture`
skill would propose. Each is sized to one PR; do them one at a time.

### 4.1 Split `routes/pages.py` (701 LOC) by feature

**Currently mixes.** clip detail page, prompt detail page, prompt CRUD
form actions, prompt archive/restore/duplicate forms, clip list page.

**Proposal.** Three feature routers in `routes/pages/`:
- `routes/pages/clips.py` (list + detail)
- `routes/pages/prompts.py` (detail + form actions)
- `routes/pages/__init__.py` (re-exports the three routers as
  `page_routers`)

**Tests.** Currently `tests/integration/test_routes_pages*.py`
already split along these lines — the split is half-done.

### 4.2 Cut `services/cache_actions.py` (522) + `cache_inspector.py` (426)

**Currently mixes.** Inspector reads DB + local FS + provider; Actions
write to all three plus the AI store. Inspector is constructed twice
(once minimal, once with external services) — see finding 1.4.4.

**Proposal — deletion test first.** Apply the deletion test: if these
modules were inlined into their callers, would complexity vanish or
just move? The Inspector probably earns its keep (six callers in
routes + LRU eviction). The Actions module is more pass-through;
worth grilling.

If they stay: collapse the two construction paths in `context.py`
into one — pass a None provider for the no-external case, never
re-construct. Removes the "is this the early or late inspector?"
confusion.

### 4.3 Sharpen archive adapters

**`archive/providers/catdv/adapter.py` (391)** mixes HTTP calls,
envelope handling, and CanonicalClip mapping. The mapping is already
in `archive/providers/catdv/payload.py` — move the remaining inline
shaping there.

**`archive/providers/fs/adapter.py` (416)** mixes filesystem walks,
sidecar JSON parsing, and the provider contract. Filesystem walk
already factored into `archive/providers/fs/sidecar.py` — move
remaining inline disk I/O there.

### 4.4 Decompose `context.build()`

**Currently.** One classmethod, 220 lines, five conditional wiring
paths (forced offline / login failed / fs provider / catdv provider /
no-external dev).

**Proposal.** Three subsystem builders:

```python
@classmethod
async def build(cls, settings, *, init_external=True) -> AppContext:
    ctx = await cls._build_core(settings)
    await _build_cache_subsystem(ctx)         # always
    if init_external:
        await _build_archive_subsystem(ctx)   # provider + resolver
        await _build_sync_subsystem(ctx)      # monitor + sync + lru + prefetch
    return ctx
```

Each builder is ~50 LOC, top-down readable, no closures over `ctx`
unless documented in one place.

---

## 5. ASYNC240 — the one rule we opted out of, and the path back

**Disabled** in `pyproject.toml` with an inline rationale (we don't
use anyio/trio; sub-ms local-SSD stat calls don't warrant `to_thread`
hops). The 13 sites that fired this rule are still there, unchanged.

**When to revisit:**

- If we ever adopt `anyio` for structured concurrency, re-enable and
  port to `anyio.Path`.
- If we deploy somewhere with a slower filesystem (NFS, network
  mount, container layer with overlay-fs), profile event-loop
  blocking on those sites first — the rule may turn out to have
  been right.
- If we hit event-loop stalls on the cache reconciler at startup
  (it scans the proxy cache dir), wrap *that* loop in `to_thread`,
  not the per-file stat.

**One real blocking-I/O fix that was applied** (the `ASYNC230`):
`catdv_client._stream_to_file` now wraps per-chunk file writes in
`asyncio.to_thread` because proxy downloads are 100–300 MB and were
previously holding the event loop the entire stream.

---

## 6. basedpyright — the 237-error baseline and how to drain it

**Baseline breakdown** (most common categories):

| Count | Category | Typical cause |
|------:|----------|---------------|
|    74 | `reportArgumentType` | Untyped `dict`/`Any` flowing into a typed parameter; Pydantic model fields used as untyped. |
|    58 | `reportAttributeAccessIssue` | Accessing attributes on `Any`-typed objects (often `request.app.state.ctx`). |
|    38 | `reportOptionalSubscript` | `Optional[dict]` indexed without narrowing. |
|    21 | `reportCallIssue` | Calling something with the wrong signature; often `**kwargs` shape mismatch. |
|    17 | `reportIndexIssue` | Subscripting an `Any` or non-subscriptable type. |
|     9 | `reportOptionalMemberAccess` | `.foo` on `Optional` without `is None` check. |
|     9 | `reportOptionalCall` | Calling an `Optional[Callable]` without narrowing. |
|     4 | `reportReturnType` | Function returns wider type than declared. |
|     3 | `reportGeneralTypeIssues` | Misc. |
|     3 | other | `reportAssignmentType`, `reportOperatorIssue`, `reportUnboundVariable`. |

**Ratcheting strategy.**

1. Annotate `request.app.state.ctx` once. A single typed accessor
   (e.g. `def get_ctx(request: Request) -> AppContext`) will collapse
   the majority of `reportAttributeAccessIssue` and many
   `reportOptionalMemberAccess`. Big win, small change.
2. Fix `reportOptional*` by adding the missing `is None` guards. These
   are real latent bugs — exactly what a type checker exists for.
3. Type the wire-shape JSON dicts that flow out of the CatDV adapter
   into routes as `TypedDict`s or Pydantic models. Big chunk of
   `reportArgumentType` lives here.
4. Refresh the baseline after each batch:
   `.venv/bin/basedpyright --writebaseline backend/ tests/`
5. After the baseline is small (<50), promote `typeCheckingMode` from
   `"basic"` to `"standard"`, then `"strict"`.

Cadence: aim for the baseline to halve every 2–3 weeks. CI shouldn't
block on the count; the pre-commit hook already prevents *regressions*.

---

## 7. The 10 pre-existing test failures

Diagnose root cause before assuming it's a pytest-asyncio bug. The
shared symptom is `RuntimeError: There is no current event loop in
thread 'MainThread'.` — in Python 3.14, asyncio no longer creates an
implicit event loop on first access. `pytest-asyncio` may not yet be
fully 3.14-compatible.

**Plan:**

1. Confirm by reading the relevant changes in
   [pytest-asyncio releases](https://pypi.org/project/pytest-asyncio/)
   since the last version we tested under (whichever pre-3.14 Python
   the suite passed under). Possibly we're pinned to a version that
   pre-dates 3.14 support.
2. Bump `pytest-asyncio` to the latest. If still failing, check the
   `conftest.py` fixtures for hand-rolled `get_event_loop()` calls
   that need to become `asyncio.new_event_loop()` or
   `asyncio_default_fixture_loop_scope`.
3. If the cache-routes cluster is uniquely affected, look at how
   their `TestClient` interacts with the FastAPI lifespan — likely
   one test tears down a loop the next test still has a reference to.

Tracked separately because architectural refactors against a broken
suite are dangerous.

---

## 8. Recommended sequence

A green-to-green path that's safe to do in PR-sized slices:

1. **PR A — fix the 10 broken tests** (§7). Architectural work
   without a trusted suite is reckless.
2. **PR B — CONTEXT.md + ARCHITECTURE.md** (§3.1, §3.2). Cheapest
   orientation win; informs every later refactor.
3. **PR C — import-linter** with the looser §2.1(a) contract +
   pre-commit hook. Locks the layering we have today.
4. **PR D — `routes/pages.py` split** (§4.1). Smallest of the
   refactors, fully tested already.
5. **PR E — basedpyright ratchet #1** (§6 step 1, typed `get_ctx`).
   Single change that knocks the largest single category out.
6. **PR F — `context.build()` decomposition** (§4.4). Touches startup;
   do after the type cleanup gives us confidence.
7. **PR G — ADR migration** (§3.4). Mechanical; do whenever there's a
   gap in feature work.
8. **PR H — cache_actions / cache_inspector grilling + decision**
   (§4.2). May or may not produce a refactor; the deletion test runs
   first.
9. **Ongoing** — module docstring sweep (§3.3, opportunistic);
   basedpyright ratchet steps 2–4 (§6, one batch per week).

Stop after each PR. Don't bundle.

---

## 9. Explicit non-goals

To prevent scope creep, the following are **out of scope** for this
plan and should be deferred to a separate brief if they come up:

- Replacing aiosqlite with a different storage layer.
- Replacing the manual DI in `context.py` with a DI container framework.
- Migrating from FastAPI's `lifespan` to a different startup model.
- Adding API versioning, OpenAPI generation polish, or a separate
  admin/frontend split.
- Any change to the wire shape of the CatDV REST integration.
- Any rewrite from Jinja templates to a JS SPA.

These are all reasonable conversations to have, but they aren't
"orienting the existing code" — they're "building something else."
