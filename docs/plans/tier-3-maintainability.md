# Tier 3 — Maintainability cleanup

**Spec:** `docs/specs/2026-05-30-fix-prioritization-design.md` § "Tier 3 — Maintainability cleanup"
**Handoff:** `docs/plans/tier-3-handoff-notes.md` (verified-fact crib sheet — read it before starting)
**Branch:** `fix/tier-3-maintainability`
**Worktree:** `.claude/worktrees/fix-tier-3-maintainability/`

This plan turns the tier-3 section of the approved spec into executable,
TDD-structured tasks. Each task is a self-contained commit cluster: a
failing test (or, for pure deletions, a characterisation test that pins
behaviour) first, then the implementation, then verification. Tier 1
(PR #21) and tier 2 (PR #23) are merged; their tests are the regression
net for these refactors.

The tier goal is **a shorter, more cohesive, more user-friendly
codebase** — not new features. Every task should remove more than it
adds, or it isn't pulling its weight. Target: ~-500 lines of production
code, 3 new shared primitives, 6+ duplications killed.

---

## Conventions for the executing agent

- **TDD throughout.** Write the test, watch it fail, implement, watch it
  pass. For pure deletions (`_DictWrap`, dead code), first add/keep a
  characterisation test that asserts the *behaviour* the deleted code
  provided, so the deletion is proven safe by green tests — never write
  implementation before the failing/pinning test.
- **One commit per task** (or tight cluster). Commit message references
  the task ID (e.g. `T3-A1`). Keep each fix independently `git revert`-able.
- **Run the full suite before each commit:** `.venv/bin/pytest -q`.
  Baseline is ~958+ passing with **one pre-existing failure to IGNORE**:
  `tests/integration/test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag`
  (failing on `main` before tier 1; tracked separately — do not fix here).
- **Line references drift.** Every `file:line` below was taken from the
  spec/handoff, verified against `main`. **Confirm with a quick grep
  before editing** — tiers 1/2 and PR #22 may have shifted lines.
- **CatDV seat discipline:** see CLAUDE.md. Do not spawn dev servers in
  parallel; SIGTERM only; reuse a running instance. Most tier-3 work is
  pure refactor + unit/integration tests and needs no live server.
- **Reuse over duplication** (user memory `feedback_reuse_no_duplication`):
  the whole point of this tier. If a task tempts you to copy a block,
  extract a shared helper instead and note it in the task's commit.
- **Minimal literal changes** (user memory `feedback_minimal_literal_changes`):
  refactor only what each task names. Do not opportunistically rewrite
  adjacent code; if you spot a new issue, add it to "Deferred items".
- **Python 3.14 venv** is in use and works here. Use `asyncio.run()` not
  `asyncio.get_event_loop()` in tests.
- **After a bash `sed -i`** the Edit tool loses file-read tracking — `Read`
  again before `Edit`.

### Sequencing

T3-A first (the context split makes T3-B's route reasoning cleaner and
gives T3-C's import-linter contracts something concrete to assert), then
T3-B, then T3-C last (its guardrails encode decisions A and B made).
Within T3-A, do A1 (context split) before A2 (route deps) before A3
(Jinja) before A4 (`_DictWrap`) before A5 (evict consolidation) — each
leans on the previous. A4 and A5 are independent of each other and may
be parallelised by separate subagents if desired.

---

## Pre-flight (do this once, before any task)

1. `git -C <repo> checkout main && git pull` — confirm at or past `74398b5`.
2. `git push origin main` — **required** before creating the worktree;
   `worktree.baseRef` is unset so `EnterWorktree` branches from
   `origin/main` and won't see unpushed commits (handoff §crib).
3. Create the worktree (`superpowers:using-git-worktrees` or `EnterWorktree`):
   branch `fix/tier-3-maintainability`, path
   `.claude/worktrees/fix-tier-3-maintainability/`.
4. Symlink shared resources from the parent into the worktree:
   `ln -s <parent>/.venv .venv && ln -s <parent>/.env .env && ln -s <parent>/data data`.
   Add bare names `.venv` and `data` to the shared `.git/info/exclude`.
   **Do not run `./run.sh` from the worktree** — it re-runs `pip install -e`
   and rewrites the editable-install path, breaking the parent's imports.
   Recover with `cd <parent> && .venv/bin/pip install -e .[dev]`.
5. **Baseline the suite:** `.venv/bin/pytest -q` → record the pass count
   and confirm the single known failure is the only red.
6. **Assign ADR numbers now.** `ls docs/adr/ | sort -V | tail -3` to find
   the next free number (handoff says next is **0047**; verify). Reserve:
   - `0047-context-split-and-route-deps-rationale.md` (T3-A)
   - `0048-alpine-store-not-x-data-stack-for-shared-state.md` (T3-B)
   If `ls` shows a higher max, bump both by the same offset and update the
   close-out task and every `00NN` reference below.

---

## T3-A. Backend structural unify

### T3-A1. Split `AppContext` into `CoreCtx` + `LiveCtx`

**Why.** `AppContext` (`backend/app/context.py`, ~30 fields, ~15 typed
`Foo | None`) forces `attach_provider` / `attach_ai_store` late-binding
and `assert ctx.foo is not None` at every live call site. The type
system should carry the offline/online contract instead.

**Design (spec "Approach A, strict type split").**
- `CoreCtx` — everything **always present**: `settings`, `db`, all
  repositories, the write queue. Nothing `Optional`.
- `LiveCtx` — carries a `CoreCtx` (composition, not inheritance, to avoid
  field duplication) plus everything **live, all non-Optional**: `catdv`,
  `ai_store`, `gemini`, `proxy_resolver`, `sync_engine`, and the cache
  services (`cache_inspector`, `cache_actions`, `thumbnail_service`,
  `workspace_manager`, `annotator`, `live_sessions`, `studio`,
  `connection_monitor`). Expose core fields via thin properties or a
  `.core` attribute — pick one and use it consistently; document the
  choice in ADR 0047.
- The app builds a `CoreCtx` always; when CatDV/Gemini wiring succeeds it
  builds a `LiveCtx` wrapping it. The connection monitor's transition
  online→offline is what decides which is exposed to live-only routes.
  Preserve the existing connection-monitor halt-on-first-non-online
  behaviour (spec non-goal: do not change it).
- **Delete** `attach_provider` / `attach_ai_store` and the ADR-0021
  apology comments. Delete every `assert ctx.foo is not None` that the
  split makes unreachable.

**Failing test first.** `tests/unit/test_context_split.py`:
- Asserts `AppContext` is no longer importable from
  `backend.app.context` (forces all future code through the split). Use
  `with pytest.raises(ImportError)` / `assert not hasattr(module, "AppContext")`.
- Asserts `CoreCtx` has no `Optional`/`| None` service fields
  (introspect `typing.get_type_hints` / dataclass fields; assert none are
  `Optional`).
- Asserts `LiveCtx` exposes `catdv`, `ai_store`, `gemini` as non-Optional.

**Implementation.** Introduce the two dataclasses; migrate the app
factory (`backend/app/main.py` / wherever `AppContext` is constructed)
and `lifespan` to build `CoreCtx` then `LiveCtx`. Update all imports.
This touches many call sites — let basedpyright/the test suite drive
completeness. The dependency providers in T3-A2 finish the route side.

**Verification.** `.venv/bin/pytest -q`; `basedpyright` (or the project's
type checker) clean on `context.py`, `main.py`, and touched routes; no
`assert ctx.* is not None` remain (`grep -rn "is not None" backend/app/routes`
should drop sharply — confirm the remaining hits are legitimate).

---

### T3-A2. Unify route deps to one `Depends(get_live_ctx)`

**Why.** `routes/cache.py` repeats a triple
`get_ctx` + `_inspector(ctx)` + `_actions(ctx)` dependency dance; the
`_inspector` / `_actions` helpers recur across route modules. With
`LiveCtx` wiring the cache services already, routes can take a single
`Annotated[LiveCtx, Depends(get_live_ctx)]`.

**Failing test first.** Extend `tests/integration/test_routes_cache.py`
(canonical fixture: `_setenv(monkeypatch, tmp_path)` + `_make_app()` +
`TestClient(app)`): assert the cache routes still return 200 and the
same payloads with the unified dependency. Add an assertion that
`get_live_ctx` is the single dependency declared (introspect
`route.dependant.dependencies`), and that `_inspector` / `_actions` are
gone (`assert not hasattr(routes.cache, "_inspector")`).

**Implementation.** Add `get_core_ctx` and `get_live_ctx` providers
(one module, e.g. `backend/app/deps.py`). `get_live_ctx` raises a clear
503/"CatDV offline" when only a `CoreCtx` is available — this is the
typed offline contract surfacing at the edge, replacing scattered
asserts. Rewrite `routes/cache.py` (and any other route using the
`_inspector`/`_actions` triple) to depend on `LiveCtx` and reach
`ctx.cache_inspector` / `ctx.cache_actions` directly. Delete the helpers.

**Verification.** `.venv/bin/pytest -q`; manually (read-only) hit
`/cache` against a running server only if convenient — not required for
green.

---

### T3-A3. Consolidate four Jinja environments into one

**Why.** `Jinja2Templates(directory=...)` is instantiated four times
(`routes/cache.py`, `routes/connection.py`, `routes/ui.py`,
`routes/pages/templates.py`). Filters (`bytes_human`, `comma`) live only
on the cache instance; the `smpte` global only on
`routes/pages/templates.py`. A render through the wrong env throws
`UndefinedError`.

**Failing test first.** `tests/unit/test_templates_shared.py`:
- Render a tiny template string through the shared env asserting
  `bytes_human`, `comma` filters and the `smpte` global all resolve.
- Assert there is exactly one `Jinja2Templates` construction in
  `backend/app/` (grep-style: walk `backend/app/`, count
  `Jinja2Templates(` occurrences, assert == 1). This is also a tier-3
  guardrail and pairs with T3-C.

**Implementation.** Make `backend/app/routes/pages/templates.py` the
single source (handoff: shared instance is
`from backend.app.routes.pages.templates import templates`, already has
`smpte`). Move the `bytes_human` / `comma` filters there. Replace the
other three instantiations with imports of that `templates`. Keep the
import path stable so existing `templates.TemplateResponse(...)` callers
don't churn.

**Verification.** `.venv/bin/pytest -q`; spot-render the cache,
connection, and a pages template in tests to confirm filters/globals
reach every render (spec acceptance flow T3-#2).

---

### T3-A4. Delete `_DictWrap`; templates read dataclasses directly

**Why.** `_DictWrap` (`routes/cache.py`, ~lines 396–415) exists only to
re-shape inspector dataclasses into dict-access for templates that
predate the dataclasses. ~20 lines plus its characterisation test, gone.

**Pinning test first.** Before deleting, ensure an integration test
asserts the *rendered* cache page contains the fields `_DictWrap` was
exposing (sizes, counts, pins, etc.) for a seeded cache. If
`test_routes_cache.py` already covers this, lean on it; otherwise add the
assertions first and watch them pass on current code.

**Implementation.** Rewrite the cache templates
(`backend/app/templates/pages/` — find via
`grep -rln "_DictWrap\|\.get(" backend/app/templates/pages/`) to access
dataclass attributes directly (`row.size_bytes` not `row['size_bytes']`).
Delete `_DictWrap` and its characterisation test. Honour `with context`
on any include that references outer-scope vars (handoff lesson 6).

**Verification.** `.venv/bin/pytest -q`; the pinning assertions from the
step above must stay green against the rewritten templates.

---

### T3-A5. Collapse three evict impls into one `LayerPolicy`-driven method

**Why.** `_evict_local_media_impl`, `_evict_ai_media_impl`,
`_evict_metadata_impl` (`backend/app/services/cache_actions.py`,
~lines 269–517) are three ~70-line near-duplicates. This is the tier's
single biggest line-count reduction (~-150).

**Design.** A small frozen `LayerPolicy` dataclass capturing what differs
per layer: which repo/table to read, how to locate bytes (local path /
GCS key / none), the delete action (already async-safe per tier-2 T2-5 —
preserve `await asyncio.to_thread(os.unlink, ...)` and the async
`p.exists()` check), the DB index to clear, and the human label. One
`_evict_impl(policy, ...)` drives all three. Define three module-level
policy constants: `LOCAL_MEDIA`, `AI_MEDIA`, `METADATA`.

**Failing test first.** If `test_cache_actions` coverage for the three
evicts is thin, add per-layer tests asserting: correct bytes removed,
correct DB index cleared, offline path returns gracefully, and **no sync
filesystem call on the event loop** (the to_thread wrapper survives the
refactor — the T3-C grep test will also enforce this). Watch them pass on
the current three impls, then refactor and keep them green (refactor-safe
TDD: tests pin behaviour across the consolidation).

**Implementation.** Introduce `LayerPolicy` + the three constants +
`_evict_impl`. Replace the three methods; keep their public method names
/ signatures as thin wrappers calling `_evict_impl(LOCAL_MEDIA, ...)` etc.
so callers and routes don't change.

**Verification.** `.venv/bin/pytest -q`; `git diff --stat` should show a
clearly negative delta on `cache_actions.py`.

---

## T3-B. Frontend structural unify

Target: `backend/app/static/studio.js` shrinks from ~499 lines to ~250.
Reuse the shared UI library and `Alpine.store('toast')` (CLAUDE.md
"Frontend: explore before implementing" + "Frontend error handling").

### T3-B1. `Alpine.store('studio')` replaces `_x_dataStack[0]` reach-ins

**Why.** 4+ sites in `studio.js` reach into Alpine's private API
(`document.querySelector('.studio-page')?._x_dataStack?.[0]`) to get
cross-component page state. `Alpine.store` is the documented pattern for
exactly this. The `window.studio` shim (`studio.js` ~lines 23–41)
collapses to thin wrappers over the store.

**Failing test first.** Mirror the Python-state-machine test pattern
(handoff: `tests/_helpers/studio_state.py` + `tests/unit/test_studio_run_button_label.py`).
Add a **grep-style** test now that will go green only after the refactor:
`tests/unit/test_no_x_data_stack.py` asserting `_x_dataStack` appears in
**no** file under `backend/app/static/` (and `backend/app/templates/`).
It fails on current `main` — that's the failing test. (T3-C generalises
this into the formal guardrail; introduce it here scoped to the studio
files, widen in T3-C.)

**Implementation.** New `backend/app/static/studioStore.js` registering
`Alpine.store('studio', { ... })` holding the shared page state
(active prompt version, focused clip, model selection, etc. — read the
current `_x_dataStack[0]` accessors to enumerate exactly what's shared).
Rewrite `modelPicker`, `archivePicker`, `studioPromptCard` (and any other
component currently reaching in) to read/write `Alpine.store('studio')`.
Reduce `window.studio` to thin wrappers (or delete if nothing external
needs it — grep templates for `window.studio` usage first). Register
`studioStore.js` in `layout.html` / the studio page's script includes
**before** Alpine initialises.

**Byte-exact labels.** Any run-button label strings must byte-match the
Python mirror (handoff lesson 4: `✓ ⊘ ⟳ … ▶ ·` exact codepoints). If you
touch `runButtonLabel`, update `tests/_helpers/studio_state.py` in lockstep.

**Verification.** `.venv/bin/pytest -q`; the new grep test goes green;
existing studio integration tests
(`tests/integration/test_studio_folder_list_polish.py` et al.) stay
green. Manual (spec acceptance T3-#3): switch active prompt version →
compare card, run button, URL all update in sync.

---

### T3-B2. One HTMX↔Alpine lifecycle helper

**Why.** Four scattered `htmx:afterSwap` handlers plus manual
`Alpine.initTree()` / `htmx.process()` calls re-init swapped subtrees
inconsistently — the source of "dead clicks" on HTMX-injected nodes.

**Failing test first.** `tests/integration/` — assert an HTMX partial
swap response (e.g. folder-kids, prompt-card swap) returns markup the
helper can re-init; and a grep-style assertion that
`Alpine.initTree(` / `htmx.process(` appear in **only one** static file
(`htmxAlpine.js`). Fails on current `main`.

**Implementation.** New `backend/app/static/htmxAlpine.js`: a single
`htmx:afterSwap` (and `htmx:afterSettle` if needed) handler that, on any
swap, re-inits Alpine on the new subtree, re-processes HTMX, and
reconciles `.selected` / focus state against the registered
`Alpine.store('studio')`. Remove the four ad-hoc handlers from
`studio.js`. Studio opts in by including the module; document the opt-in
so future pages reuse it (do **not** auto-bind globally in a way that
double-processes existing pages — scope to a registered root or a data
attribute).

**Guard the deferred fragilities** the tier-2 reviews flagged (handoff
"Deferred items"): replace silent null-selector no-ops in `addSelected` /
`createFolder` with a `console.warn` (diagnostic-only is fine per
CLAUDE.md), and prefer a stable selector over `lastElementChild` in
`createFolder` so it survives `_studio_folder_card.html` gaining sibling
roots. These are small and in-scope here since the helper owns swap wiring.

**Verification.** `.venv/bin/pytest -q`; grep test green; manual (spec
acceptance T3-#4): trigger an HTMX swap, click a control inside the
swapped subtree → it responds (Alpine + HTMX both wired).

---

### T3-B3. Broaden the toast / no-reload sweep

**Why.** Tier 2 covered `studio.js` / `review.js` / `liveSession.js`.
Remaining silent fetches / `location.reload()` / `alert()` violate the
CLAUDE.md "Frontend error handling" rule.

**Failing test first.** Grep-style `tests/unit/test_no_silent_fetch_ux.py`
asserting **no** `alert(`, no `location.reload(`, and no bare
`.catch(` without a toast in `backend/app/static/*.js` (allow
`console.error`/`console.warn` for diagnostic-only noise — match the
CLAUDE.md carve-out; encode the allowlist explicitly). Fails on current
`main` because of the known remaining sites.

**Implementation.** Audit `clipAnnotate.js`, `liveSession.js`,
`review.js`, `promptEditor.js`. Specifically resolve the deferred
`review.js:99` `location.reload()` (tier-2 T2-4 deferred it): make its
backing endpoint return an HTMX partial on `HX-Request: true` and swap in
place + push a success toast (CLAUDE.md: never `location.reload()` after
CRUD). Normalise the cosmetic inconsistencies the tier-2 review noted:
`err.message || String(err)` everywhere (not `err.message || err`), and
brace `review.js::applyStay`'s toast call to match the file. Remove the
unused `import pytest` in the studio.js test file.

**Verification.** `.venv/bin/pytest -q`; grep test green; manual (spec
acceptance T2-#3/#4 still hold): a fetch failure shows a toast; folder
CRUD and the former `review.js` reload site swap partials with no full
page reload.

---

## T3-C. Guardrails + dead-code sweep

The discipline layer that stops T3-A/B's wins from rotting. These are
mostly *tests and contracts* — they encode decisions already made.

### T3-C1. Extend import-linter contracts

**Why.** Each contract maps to a pattern a prior tier killed; the
contract stops its reintroduction in CI.

**Implementation.** Extend `.importlinter`:
- **routes may not import `httpx`** (forces route code through the
  archive/client layer — tier 1 narrowed provider errors there).
- **repositories may not import services** (layer-direction; repos are
  leaves).
- The `_x_dataStack` ban is JS, not Python imports — that's enforced by
  the T3-B1/T3-C2 grep test, not import-linter. Note this in the contract
  comments so a reader doesn't expect it here.

**Verification.** `lint-imports` passes on the cleaned tree; then
temporarily add an offending import in a scratch file and confirm
`lint-imports` fails (delete the scratch). Document the run command in
the close-out.

---

### T3-C2. Generalise the source-grep guardrail tests

**Why.** Tier 2's `tests/unit/test_no_sync_fs_in_async.py` only scans
`cache_actions.py`. Widen it; consolidate the T3-B grep tests.

**Implementation.**
- Expand `test_no_sync_fs_in_async.py` to walk **all** `async def` blocks
  under `backend/app/services/` (and `backend/app/routes/`) and assert no
  bare `os.unlink` / `os.remove` / `Path.unlink` / `Path.exists` /
  `Path.read_text` / `Path.write_text` / raw `open(` calls. Use `\(`
  anchors so a function *reference* passed to `asyncio.to_thread` doesn't
  false-positive (handoff lesson 9). Allow an explicit `# sync-io-ok`
  pragma escape hatch for the rare justified case.
- Promote `tests/unit/test_no_x_data_stack.py` (from T3-B1) to scan the
  whole `backend/app/static/` + `backend/app/templates/` tree, with the
  carve-out that `htmxAlpine.js` / `studioStore.js` are the only files
  permitted to touch Alpine internals if any residue remains (ideally
  none — aim for zero references anywhere).
- Keep the single-`Jinja2Templates` and single-`initTree/process`
  assertions from T3-A3 / T3-B2 (or fold them here for one guardrail
  module).

**Verification.** `.venv/bin/pytest -q` green; each grep test demonstrably
fails when you inject a violation (probe then revert).

---

### T3-C3. Pin the clips page against N+1

**Why.** The clips page render is "strongly suspected N+1 by analogy" to
the cache page tier 2 fixed.

**Implementation.** Locate the clips render (`backend/app/routes/pages/clips.py`
or wherever — grep for the clips page route). Wrap its render path in a
test using `tests/_helpers/query_count.py::assert_query_count`, seeding
10 / 100 / 1000 clips and asserting a **constant** query count. If it
scales with N, fix it with `chunked_in_clause` (`repositories/_batch.py`)
exactly as tier 2 fixed the cache loaders — batch the per-key loaders
into one `WHERE (...) IN (...)` query each, and push filters/pagination
into SQL (`CacheInspector.list_for_inventory` is the reusable shape per
handoff). If it turns out already-bounded, keep the test as a regression
pin and note that in the commit.

**Verification.** `.venv/bin/pytest -q`; the query-count test passes at
all three seed sizes with the same N.

---

### T3-C4. Delete documented dead code

**Why.** `cache_inspector.py:443-444` has `_ = json` and a "reserved for
future use" comment (handoff: tier 2 may have already touched it —
confirm presence first).

**Implementation.** Grep-confirm, then delete the dead binding, the
comment, and the now-unused `json` import if nothing else uses it. Sweep
for any other dead code the refactor exposed (unused imports after the
context split, orphaned helpers). Keep it minimal — only provably-dead
lines.

**Verification.** `.venv/bin/pytest -q`; `git diff` shows only deletions;
no new lint warnings for unused imports.

---

## Tier 3 close-out (final task)

A single close-out commit on the branch before opening the PR.

1. **CLAUDE.md additions** (spec § "CLAUDE.md additions / Tier 3"):
   - New **"Patterns we've removed"** section:
     - "Cross-component state in Alpine uses `Alpine.store('name')`. Never
       `_x_dataStack`." → cross-ref `test_no_x_data_stack.py`.
     - "No sync filesystem I/O inside `async def`. Wrap in
       `asyncio.to_thread(...)`." → cross-ref `test_no_sync_fs_in_async.py`.
     - "Two contexts: `CoreCtx` (always present) and `LiveCtx` (CatDV +
       Gemini wired). Routes declare which they need via
       `Depends(get_core_ctx)` / `Depends(get_live_ctx)`; `Optional`
       services are not added back." → cross-ref ADR 0047.
     - "One Jinja env: import `templates` from
       `backend.app.routes.pages.templates`. Don't instantiate
       `Jinja2Templates` elsewhere." → cross-ref `test_templates_shared.py`.
     - "One HTMX↔Alpine lifecycle helper (`static/htmxAlpine.js`). Don't
       hand-roll `Alpine.initTree` / `htmx.process` per page."
   - Add cross-references from the existing tier-1 "Error handling
     discipline" and tier-2 "Performance discipline" / "Frontend error
     handling" sections to the new import-linter contracts and grep tests.
2. **ADRs** (MADR-lite; see `docs/adr/0042-...md` for shape):
   - `docs/adr/0047-context-split-and-route-deps-rationale.md` — Context,
     Alternatives (kept god-context + asserts / inheritance vs
     composition / runtime guards vs type-level), Decision (CoreCtx +
     LiveCtx, composition, `get_*_ctx` deps), Consequences.
   - `docs/adr/0048-alpine-store-not-x-data-stack-for-shared-state.md` —
     Context, Alternatives (`_x_dataStack` reach-in / event bus / props
     drilling), Decision (`Alpine.store('studio')` + one lifecycle
     helper), Consequences.
   - Update the index table in `docs/decisions.md` with both.
3. **Verification before completion** (`superpowers:verification-before-completion`):
   - `.venv/bin/pytest -q` — full suite green except the one known
     pre-existing failure. Paste the summary line.
   - `lint-imports` — green.
   - The project type-checker (basedpyright/mypy) — green on touched files.
   - `git diff --stat origin/main...HEAD` — capture the **scorecard**:
     net line delta (target strongly negative, ~-500 production lines),
     new primitives (`LayerPolicy`, `Alpine.store('studio')`,
     `htmxAlpine`), duplications killed (4 Jinja→1; 3 evict→1; 4
     `_x_dataStack`→1 store; `_DictWrap`→0; late-binding→0; route
     triples→1 dep).
4. **PR** (`superpowers:finishing-a-development-branch` →
   `gh pr create`): title `fix(tier-3): maintainability cleanup`, body
   carries the scorecard + the spec's "Tier 3 acceptance" flows as a
   checklist, links the spec and both ADRs. Co-author trailer per repo
   convention.

---

## Acceptance flows (from the spec — verify before PR)

1. **CoreCtx / LiveCtx split enforced** — a route declaring `CoreCtx`
   but using a live-only service fails type-check; `AppContext` no longer
   exists; no route asserts `ctx.foo is not None`.
2. **Filter reaches every render** — `|bytes_human` / `|comma` resolve on
   any route's template; no `UndefinedError` from a private Jinja env.
3. **Studio components share state via `Alpine.store`** — switch active
   prompt version → compare card, run button, URL update in sync; zero
   `_x_dataStack` references (grep test enforces in CI).
4. **HTMX-injected nodes are alive** — trigger any swap, click a control
   inside the new subtree → it responds; one lifecycle helper is the only
   re-init/re-process site.
5. **Guardrails fail loudly** — introduce a route `import httpx`, a
   service `_x_dataStack` reference, or an `async def` `os.unlink` → CI
   fails naming the rule and file.

---

## Deferred items (carry forward, do not expand scope here)

From tier 1 (still open): `sync_max_attempts` pydantic lower bound;
NOT_FOUND adapter tests for `list_clips` / `list_field_definitions`;
`apply_changes` put-clip race test; dev-DB orphan testbench tables from
reverted PR #9.

From tier 2 (still open, absorb only if a task above naturally touches
the site): `_poll()` max-retry counter for permanent 5xx.

**Tier 4 (DB connection pool) is explicitly out of scope** — it needs its
own `superpowers:brainstorming` session (pool size, mutex granularity,
read-promotes-to-write, 120-call-site migration, benchmark methodology).
Do not start it as part of tier 3.
