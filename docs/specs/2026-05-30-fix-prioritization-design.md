# Fix prioritization — what to fix and in what order

**Date:** 2026-05-30
**Status:** Approved (design)
**Source:** Inspector-mode code review on `main` at 6198b8e.

## Problem

A round of hostile code review surfaced ~20 distinct issues across data
integrity, performance, UX consistency, and maintainability. The
findings need ordering, bundling, and a workflow that — beyond just
fixing them — leaves durable guardrails so future contributors (human
or AI) don't recreate the same anti-patterns.

The cost of not doing this is not a single bug. It is a slope: every
new feature ships slightly slower than the last, the next contributor
reads the surrounding code and learns the wrong lesson, and the
patterns being copied carry the same flaws forward.

## Goals

- Sequence the fixes by blast radius (data-loss / silent failure first),
  user impact (perf + UX) second, structural rot (god-context,
  parallel-evolved frontend, single-connection DB) third and fourth.
- For each fix, attach a guardrail — a test, a type, an import-linter
  contract, or a grep-style scanner — so a future agent cannot
  reintroduce the anti-pattern without CI catching it.
- Document each recurring anti-pattern as an ADR named after the
  pattern itself, so the lesson is discoverable from the index.
- Keep `CLAUDE.md` in sync at each tier boundary — it is the always-
  loaded surface and the only reliable place to install rules.
- Measure each tier by lines-added/lines-removed and shared-primitives-
  introduced / duplications-killed, so we know whether the codebase is
  getting shorter and more cohesive or just more elaborately wrong.

## Non-goals

- Switching to Postgres, switching off Alpine, switching off HTMX, or
  any other framework swap.
- Rewriting features that work as designed (the connection-monitor halt
  on first non-online state is intentional; the Plan-A no-Node frontend
  decision stands; the local-app-on-VPN threat model stands).
- Backfilling tests for areas not touched by these fixes.

## Approach: hybrid of per-fix bundles and tiered PRs

The fixes ship in **four tiered branches**, ordered by priority:

1. **Tier 1 — data loss and silent failures.**
2. **Tier 2 — user feel** (perf and UX consistency).
3. **Tier 3 — maintainability cleanup** (context split, frontend
   consolidation, guardrails sweep).
4. **Tier 4 — DB connection model** (its own brainstorm; see below).

Each tier merges into `main` as one PR. Inside the tier branch, work is
structured as per-fix commit clusters: each fix is its own commit (or
small cluster) carrying its own test or guardrail, so individual fixes
are revertable via `git revert <sha>` even after the tier squash-merge.

**Sequencing rule:** tier N+1 does not start until tier N is merged to
`main` and its `CLAUDE.md` updates are in. Reasons: tier 1's bug-fix
tests are the regression net for tier 3's refactors; tier 2's
shared primitives (batch helper, query-count test, toast store) become
tier 3's levers for broader sweeps.

**Tier 4 deferral:** the DB connection model change touches 120 call
sites and has more open design questions than the rest of tier 3
combined. It gets its own brainstorming session, its own spec, and is
optional — you can stop after tier 3 with a meaningfully better
codebase and only do tier 4 if measured DB serialisation becomes a
real bottleneck.

## Workflow shape

**Worktrees.** Each tier gets its own `git worktree` under
`.claude/worktrees/` (the project's existing pattern). Branch names:
`fix/tier-1-data-loss`, `fix/tier-2-user-feel`,
`fix/tier-3-maintainability`, `fix/tier-4-db-pool`. Invoked via
`superpowers:using-git-worktrees` at the start of each tier.

**PR cadence.** One PR per tier, merged into `main`.

**Superpowers skills used, in order:**
- `brainstorming` — this document.
- `writing-plans` — at the end of brainstorming, produces one plan per
  tier in `docs/plans/`. Tier 1's plan is written first; tiers 2 and 3
  get plans written at the start of their respective execution.
- `using-git-worktrees` — at the start of each tier.
- `test-driven-development` — for every fix with a guardrail test.
- `systematic-debugging` — when a fix turns out deeper than expected.
- `verification-before-completion` — before claiming any fix complete.
- `requesting-code-review` — at end of each tier before opening the PR.
- `finishing-a-development-branch` — to open the PR and clean up.

**Spec and plan locations** (per `CLAUDE.md` and user memory):
- Umbrella spec: `docs/specs/2026-05-30-fix-prioritization-design.md`
  (this file).
- Per-tier plans: `docs/plans/tier-N-<slug>.md`.
- ADRs: `docs/adr/NNNN-<slug>.md` per existing numbering; index in
  `docs/decisions.md` updated each tier.

**Per-tier close-out commit** lands on each branch before opening the
PR. It contains:
- `CLAUDE.md` updates for any new rules introduced by the tier.
- `docs/decisions.md` index updates for any new ADRs.
- The PR description includes a scorecard: lines added vs removed, new
  shared primitives introduced, duplications killed.

---

## Tier 1 — Stop the bleeding (data loss + silent failures)

Seven fixes, ordered by blast radius. Each lists the fix, the
guardrail, and whether it drops an ADR.

### T1-1. Provider exceptions are not "not found"

`CacheInspector.list_orphans(deep=True)` (cache_inspector.py:322) and
`WorkspaceManager.prepare` (workspace_manager.py:132, :160) both do
`except Exception:` and treat *any* failure of `provider.get_clip` as
evidence the clip is gone. A VPN flap can mark hundreds of clips orphan
— and the next "Evict orphans" action wipes legitimately-cached data.
Same flap can lock workspace clips into a terminal error state.

**Fix.** New helper `archive/errors.py::is_provider_not_found(exc) ->
bool` that matches httpx 404, CatdvError-with-NOT-FOUND, and similar
documented "missing" signals. Inspector and workspace prep use it;
non-NotFound exceptions become a "transient check failed — try again
later" signal, not orphan / error.

**Guardrail.** Test with mocked provider that raises an arbitrary
transport error. Assert: not marked orphan, not produced as
`evict_orphans` candidate. Same shape for workspace prep.

**ADR.** `00NN-narrow-provider-errors-never-treat-exceptions-as-not-found.md`.
This is the centerpiece of the tier.

### T1-2. Sync engine: unknown exceptions are retryable, not fatal

`sync_engine._tick` (sync_engine.py:202-204) catches unknown exceptions
and calls `mark_failed` — terminal, never retried. A transient adapter
bug or new transport error permanently kills queued writes that would
have succeeded on retry.

**Fix.** Change the catchall to `mark_retryable`. Add a `max_attempts`
ceiling (currently absent) — at the ceiling, flip to `mark_failed`.
Constant in `Settings`, default 10.

**Guardrail.** Test: provider raises arbitrary exception → row stays
pending with `attempts++`; at `max_attempts` flips to failed.

**ADR.** Shares the T1-1 ADR — "unknown ≠ terminal" is the same
principle.

### T1-3. Humanise user-facing error messages

`run_job` (annotator.py:114) does `msg = str(exc) or
exc.__class__.__name__`. For httpx and google-cloud exceptions
`str(exc)` is often empty or just a status code, so the user sees
"HTTPStatusError" with no actionable detail.

**Fix.** New `services/errors.py::humanise(exc) -> str` that extracts
status + body for httpx, error code + message for google-cloud, etc.
Used in `annotator`, `sync_engine._handle_result`, and any other
user-facing error surface.

**Guardrail.** Parameterised test across common exception types;
asserts non-empty informative output containing status code AND a
snippet of the response body.

**CLAUDE.md rule** (no ADR needed): "user-facing error strings go
through `humanise()`; never bare `str(exc)`."

### T1-4. Make the GEMINI_API_KEY browser exposure auditable

`live_sessions.mint_ephemeral_token` (live_sessions.py:106-112) ships
the raw production API key to the browser. The risk is currently
documented only in a code comment.

**Fix.** Not a flow change (real ephemeral-token auth is a separate
project). Three things:
1. README adds a "Security caveats" section naming the exposure and
   the threat model (single-operator local + VPN only).
2. Startup logs WARNING when `gemini_api_key` is set: "Live sessions
   expose this key to the browser; threat model assumes single-operator
   local + VPN only."
3. ADR documenting the accepted risk, what was tried (`authTokens.create`
   1007 close), and the conditions that would force a revisit.

**Guardrail.** Test asserts the WARNING log line fires at startup when
the key is configured.

**ADR.** `00NN-gemini-live-api-key-exposure-accepted-risk.md`.

### T1-5. Surface the migration 0011 gap

`backend/migrations/` skips from 0010 → 0012. Investigation: migration
`0011_studio.sql` shipped in PR #9 (commit 8a9b2bb), was reverted three
days later (commit 1065546), and replaced by `0012_prompt_media_kind.sql`
(PR #8) and `0013_studio.sql` (PR #10). The dev DB at `data/app.db`
still has `0011_studio.sql` in `schema_migrations` from the brief
window PR #9 was live. Fresh installs are fine. Risk surface: a future
PR claiming 0011 would apply cleanly on fresh installs but
inconsistently on dev DBs that ran PR #9.

**Fix.**
1. Do NOT renumber (would break dev DBs).
2. Add `backend/migrations/0011_REVERTED.txt` (no `.sql` extension; the
   runner ignores it) documenting the history and the rule.
3. Startup check in `migrations_runner.py`: raise if a new `.sql` file
   collides with a number that has a `.txt` sentinel.
4. Informational startup check: warn if `schema_migrations` contains
   entries whose files no longer exist on disk (one warning on dev DBs;
   actionable but not a failure).

**Guardrail.** Integration test loads a tmpdir migrations layout with a
sentinel + a colliding `.sql` → asserts the runner raises.

**ADR.** `00NN-migration-numbering-and-the-0011-gap.md`.

### T1-6. CatDV query string sanitisation

`CatdvClient.list_clips` (catdv_client.py:126) does `q.replace("(",
"").replace(")", "")` and calls it sanitisation. Quotes, backslashes,
the keywords `and`/`or` are not handled.

**Fix.** Check CatDV docs for the actual escape rules. If escaping
isn't supported, switch to a per-character allowlist (alphanumerics +
space + a small set) and reject the rest with a clear 400.

**Guardrail.** Parameterised test of malicious `q` strings that would
alter query semantics under today's code → assert they're either
escaped or rejected, never injected.

**ADR.** None.

### T1-7. CatDV health probe must not eat a seat

`CatdvClient.health` (catdv_client.py:252) goes through `_call_json`,
which on AUTH envelope (catdv_client.py:103) silently re-logs in. If
the seat cap is the *reason* we're probing, the probe itself can take
the seat.

**Fix.** `_call_json_no_reauth` variant (or `reauth=False` flag).
`health()` uses it; an AUTH envelope becomes "ok=False, detail=not-
authenticated" instead of triggering login.

**Guardrail.** Test with mocked transport that returns AUTH envelope →
asserts `health()` returns ok=False AND no second POST to `/session`
was made.

**ADR.** None.

### Tier 1 close-out

`CLAUDE.md` gains an "Error handling discipline" section pointing at
`is_provider_not_found` and `humanise`, plus the rule on bare
`except Exception:`. Three new ADRs land (T1-1+T1-2 shared, T1-4,
T1-5). Scorecard expectation: roughly net-zero on lines (small helpers
added, ugly try/except blocks shrunk); 2 new shared primitives; 3
instances of the exception-as-NotFound anti-pattern collapsed onto one
helper.

---

## Tier 2 — User feel (perf + UX consistency)

Five fixes. Two are big-impact perf; three are UX consistency wins.

### T2-1. Kill the cache page N+1 (the headline perf fix)

`CacheInspector._load_metadata`, `_load_media_local`, `_load_media_ai`,
`_load_pins`, `_load_pending_counts` (cache_inspector.py:334-440) all
loop per-key — 5×N round-trips per cache page render despite the
docstring at line 151 claiming they batch. `cache_page` (cache.py:189-
254) compounds it: `_all_cached_keys` runs twice (209 + 247),
`list_orphans` runs twice (207 + 239), filters are applied in Python
after fetch, and pagination is `rows[offset:offset+limit]` *after*
hydrating everything.

**Fix, three pieces in one commit cluster.**
1. New shared primitive
   `repositories/_batch.py::chunked_in_clause(keys, chunk_size=500)` —
   SQLite parameter-limit-safe `WHERE (a, b) IN ((?,?), …)` builder.
   Reused by all five inspector loaders; each collapses to one batched
   query.
2. `cache_page` computes `_all_cached_keys` and `list_orphans` once
   each.
3. Push tab / store / workspace / orphan / evictable filters into SQL
   WHERE clauses. Pagination becomes SQL `LIMIT/OFFSET`.

The misleading docstring at cache_inspector.py:151 ("Fetch per-layer
rows in one batched pass each") is rewritten to describe the new
batched implementation, not deleted.

**Guardrail.** New helper `tests/_helpers/query_count.py::assert_query_count
(db, max_n)` wrapping aiosqlite trace hooks (itself a reusable
primitive). Test asserts cache_page renders with a bounded query count
*regardless of the number of cached clips* — seed 10, 100, 1000 clips,
assert same N.

**ADR.** `00NN-no-n-plus-one-batch-with-where-in.md`. `CLAUDE.md`
gains a "Performance discipline" section.

### T2-2. Studio run cancel race + visible cancelled state

`studio.js::cancel` (studio.js:171-184) flips `running = false` in
`finally` and stops polling. If the server-side Gemini call finishes
between cancel-request and cancel-ack, the result is silently
discarded — no toast, no log, the run just disappears. No
`'cancelled'` UI state exists either.

**Fix.**
- Cancel waits for the server to confirm (`POST /api/jobs/{id}/cancel`
  returns final status, or polling continues until run row is
  terminal).
- If the server returns `'ok'` mid-cancel, surface as "Completed
  before cancel landed" via toast and show the result. Don't fabricate
  a cancelled state for a run that succeeded.
- `runButtonLabel()` (studio.js:154) gets explicit `'⊘ Cancelled'`
  flash parallel to the existing `'✓ Done'` flash.

**Guardrail.** Integration test simulates "completion arrives after
cancel" → assert review_items are persisted AND user-facing status
reflects completion, not cancel. UI snapshot test asserts the
Cancelled label exists.

**ADR.** None.

### T2-3. Central toast component

Error UX today: `alert()` (studio.js:402), `console.error` (multiple),
or silent (most fetches). Inconsistent and unactionable.

**Fix.** New shared primitive `static/toast.js` exposing
`Alpine.store('toast')` with `push(message, {level})`. Toast root
included in `layout.html` so every page picks it up. All existing
`alert()` calls and silently-swallowed fetch errors in `studio.js`,
`clipAnnotate.js`, `liveSession.js`, `review.js` rewritten to use it.

**Guardrail.** Unit test asserts `layout.html` includes the toast
root; behavioural test on one route asserts the toast surface exists
in rendered HTML.

**CLAUDE.md rule:** "user-visible errors go through
`Alpine.store('toast').push()`; never `alert()` or silent `catch`."

### T2-4. Replace `location.reload()` with HTMX partials

`studio.js` reloads the whole page after creating a folder (line 400)
and adding clips (line 372). Jarring; contradicts the rest of the
studio's partial-swap design.

**Fix.** Both endpoints already return JSON; add HTMX-aware variants
returning the new folder card / updated folder-kids partial. Client
code swaps in place via `htmx.process`.

**Guardrail.** Integration test asserts the folder-create POST returns
JSON for API calls and a partial for HTMX requests; studio.js
behavioural test asserts no full reload is triggered.

**ADR.** None.

### T2-5. Async-safe file ops in cache eviction

`CacheActions._evict_local_media_impl::os.unlink(p)` (cache_actions.py:322)
is blocking sync I/O on the event loop. On the CatDV-host deployment
where proxies live on `/Volumes/ARECA*` (network mounts), an unlink
that stalls freezes every other request — including the seat-keepalive.

**Fix.** `await asyncio.to_thread(os.unlink, p)`. Same treatment for
the `p.exists()` check.

**Guardrail.** `tests/unit/test_no_sync_fs_in_async.py` — source-grep
test walking the file and asserting no bare `os.unlink` / `Path.unlink`
/ `Path.exists` calls inside `async def` blocks. The grep test is
dumber but more durable than a runtime test. Generalised in tier 3
across all async paths.

**ADR.** None for now. A "no sync I/O in async paths" CLAUDE.md rule
lands at the end of tier 3 once the broader sweep is done.

### Tier 2 close-out

`CLAUDE.md` gains "Performance discipline" and "Frontend error
handling" sections. One new ADR. Scorecard expectation: meaningfully
negative line count on the cache code (five looping loaders collapse
into batched versions; `cache_page` shrinks substantially); 3 new
shared primitives (`chunked_in_clause`, `assert_query_count`,
`Alpine.store('toast')`); 9 duplications killed.

The shared primitives become tier 3's levers for sweeping the rest of
the codebase.

---

## Tier 3 — Maintainability cleanup

Three clusters in one tier branch. Recommended sequencing: T3-A first
(makes T3-B easier); then T3-B; T3-C last.

### T3-A. Backend structural unify

Five interlocking refactors that reinforce each other:

- **AppContext split.** `AppContext` (context.py:57-98) has ~30 fields,
  ~15 typed `Foo | None`. Apologetic ADR-0021 comments mark the
  `attach_provider` / `attach_ai_store` workarounds. **Approach A,
  strict type split:** `CoreCtx` (nothing optional), `LiveCtx`
  (everything live, nothing optional, cache services already wired),
  `OfflineCtx` (the no-CatDV path). Routes that need live take
  `LiveCtx`; routes that don't take `CoreCtx`. Type system carries the
  contract.
- **Route deps unification.** Triple `get_ctx + _inspector + _actions`
  pattern in routes/cache.py becomes one
  `Annotated[LiveCtx, Depends(get_live_ctx)]`. Kills `_inspector` /
  `_actions` helpers across multiple route modules.
- **Jinja consolidation.** Four separate `Jinja2Templates(directory=…)`
  instances (cache.py, connection.py, ui.py, pages/templates.py) become
  one `app_templates` module. The `bytes_human` / `comma` filters
  (cache.py:32-50) and `smpte` global (templates.py:16) move there;
  filters now reach every render.
- **Delete `_DictWrap`.** The wrapper class (cache.py:396-415) exists
  only because templates were written for dict-shape and inspector
  grew dataclasses. Templates rewritten for direct dataclass access.
  Net deletion ~20 lines plus the wrapper's characterisation test.
- **Consolidate evict impls.** Three ~70-line copies
  (`_evict_local_media_impl`, `_evict_ai_media_impl`,
  `_evict_metadata_impl` at cache_actions.py:269-517) become one
  parameterised method driven by a small `LayerPolicy` dataclass.

**Guardrail.** One new test asserts old `AppContext` shape no longer
exists (forces future code through the split). Existing test suite is
the regression net for everything else.

**ADR.** One — `00NN-context-split-and-route-deps-rationale.md`.

### T3-B. Frontend structural unify (Approach A from brainstorm)

Three sub-fixes shrinking studio.js from 499 lines to roughly 250:

- **`Alpine.store('studio')` replaces `_x_dataStack[0]` hacks.** The
  4+ places in studio.js reaching into Alpine's private API
  (`document.querySelector('.studio-page')?._x_dataStack?.[0]`) all
  need cross-component access to page state — exactly what
  `Alpine.store` exists for. New `static/studioStore.js` exposes the
  store; `modelPicker`, `archivePicker`, `studioPromptCard` read /
  write directly. `window.studio` shim (studio.js:23-41) collapses to
  thin wrappers.
- **One HTMX-Alpine lifecycle helper.** Four scattered `htmx:afterSwap`
  handlers + manual `Alpine.initTree()` and `htmx.process()` calls
  reduce to one `static/htmxAlpine.js` module: a single handler that,
  on any HTMX swap, re-inits Alpine on the new subtree, re-processes
  HTMX, and reconciles `.selected` / focus state against a registered
  store. Studio uses it; future pages opt in.
- **Sweep toast and no-reload broader.** Apply tier-2's
  `Alpine.store('toast')` to remaining silent fetches in
  `clipAnnotate.js`, `liveSession.js`, `review.js`, `promptEditor.js`.
  Audit for additional `location.reload()`.

**Guardrail.** New behavioural test asserts `_x_dataStack` is not
referenced outside the lifecycle helper (grep-style test). Existing
studio integration tests catch regressions.

**ADR.** One —
`00NN-alpine-store-not-x-data-stack-for-shared-state.md`.

### T3-C. Guardrails + dead code sweep

The discipline layer that prevents tier 3's wins from rotting:

- **Import-linter contracts** extending `.importlinter`: routes may
  not import `httpx` directly; services and templates may not
  reference `_x_dataStack`; repositories may not import services.
  Each contract maps to a pattern we just killed.
- **Generalised grep tests.** Tier 2's
  `test_no_sync_fs_in_async.py` expands to scan all `async def` blocks
  for sync I/O (`os.unlink`, `Path.exists`, `Path.read_text`, raw
  `open()`). `tests/unit/test_no_x_data_stack.py` enforces the
  Alpine-store rule.
- **Apply T2-1's `assert_query_count`** to the clips page render —
  strongly suspected N+1 by analogy. Fix any found.
- **Delete documented dead code.** The `_ = json` and accompanying
  "reserved for future use" comment at cache_inspector.py:443-444.
  Whatever else the sweep turns up.

**Guardrail.** The contracts and tests *are* the guardrails.

**ADR.** None — enforcement of decisions already made.

### Tier 3 close-out

`CLAUDE.md` gains a "Patterns we've removed" section pointing at the
new import-linter contracts and grep tests; tier 1's "Error handling
discipline" and tier 2's "Performance discipline" / "Frontend error
handling" sections get cross-references to the relevant contracts.
Two new ADRs. Scorecard expectation: strongly negative net line count
(evict consolidation removes ~150 lines; AppContext split removes ~50
assert-non-None sites; `_DictWrap` + duplicate Jinja setup remove ~80;
studio.js shrinks by ~250). New shared primitives: 3 (`LayerPolicy`,
`Alpine.store('studio')`, `htmxAlpine` lifecycle helper). Duplications
killed: 6 (4 Jinja envs → 1; 3 evict impls → 1; 4 `_x_dataStack`
reach-ins → 1 store; `_DictWrap` → 0; multiple late-binding dances →
0; route helper triples → 1 dep).

---

## Tier 4 — DB connection model (separate brainstorm)

**Status: deferred, requires its own brainstorming session.**

`db.py:14` opens one `aiosqlite.Connection` for the whole app. All 120
`ctx.db` call sites use it. `aiosqlite` runs a single SQLite worker
thread per connection, so all reads and writes serialise on this one
thread regardless of WAL being enabled. WAL provides concurrency
*between* connections; with one connection, none of that concurrency
is realised. Symptom: cache page render stalls every other operation;
sync engine tick blocks user requests.

**Direction (subject to confirmation in tier-4 brainstorm).** Approach
D from the brainstorm: reader pool + dedicated writer with app-level
mutex. New `Database` service replaces `ctx.db: aiosqlite.Connection`
with `ctx.db: Database`. Routes use `async with ctx.db.read() as
conn:` and `async with ctx.db.write() as conn:`.

**Open questions for tier-4 brainstorm.**
- Reader pool size (default? configurable?).
- Writer mutex granularity (one global lock, or per-table?).
- Transactional reads that promote to writes (workspace prep loop) —
  use `write()` for the whole block, or expose `read_then_write()`?
- Migration plan for the 120 call sites — automated sed + manual
  review of complex cases.
- Benchmark methodology — needs hard numbers to size the win before
  committing.

**When to do tier 4.** Optional. Only justify it once measured
serialisation becomes a real bottleneck (using tier 2's query-count
infrastructure to baseline). You can stop after tier 3 and have a
meaningfully better codebase.

---

## Guardrail mechanisms (cross-tier reference)

| Mechanism | Used for | First introduced |
|---|---|---|
| Targeted unit tests | Single-fix regression coverage | Tier 1 (T1-1, T1-2, T1-3, T1-5, T1-6, T1-7, T1-4-log) |
| `humanise()` helper | Standardise user-facing error messages | Tier 1 (T1-3) |
| `is_provider_not_found()` helper | Narrow exception → NotFound | Tier 1 (T1-1) |
| Startup log assertions | Surface accepted risks | Tier 1 (T1-4) |
| Startup gap-check | Migration numbering discipline | Tier 1 (T1-5) |
| `chunked_in_clause()` helper | Batched `WHERE … IN` queries | Tier 2 (T2-1) |
| `assert_query_count()` helper | N+1 regression tests | Tier 2 (T2-1) |
| `Alpine.store('toast')` | Frontend error UX | Tier 2 (T2-3) |
| Source-grep tests | Code-shape rules (no sync I/O, no `_x_dataStack`) | Tier 2 (T2-5), broadened in tier 3 |
| Import-linter contracts | Layer-boundary rules | Already exists; tier 3 extends |
| Type-level contracts | Mode-availability (CoreCtx / LiveCtx) | Tier 3 (T3-A) |

---

## ADRs introduced

- `00NN-narrow-provider-errors-never-treat-exceptions-as-not-found.md`
  (tier 1, shared by T1-1 + T1-2).
- `00NN-gemini-live-api-key-exposure-accepted-risk.md` (tier 1, T1-4).
- `00NN-migration-numbering-and-the-0011-gap.md` (tier 1, T1-5).
- `00NN-no-n-plus-one-batch-with-where-in.md` (tier 2, T2-1).
- `00NN-context-split-and-route-deps-rationale.md` (tier 3, T3-A).
- `00NN-alpine-store-not-x-data-stack-for-shared-state.md` (tier 3,
  T3-B).
- `00NN-sqlite-connection-pool-and-writer-mutex.md` (tier 4, if
  pursued).

Numbers assigned during plan-writing; the index in
`docs/decisions.md` is updated at each tier close-out.

---

## CLAUDE.md additions (per tier)

**Tier 1 — Error handling discipline.**
- "Bare `except Exception:` is allowed only in event-loop watchdog
  code. Anywhere a caller might infer 'this thing is absent' from a
  caught exception, narrow with `is_provider_not_found(exc)` from
  `archive/errors.py`."
- "User-facing error strings go through `humanise(exc)` from
  `services/errors.py`. Never `str(exc)`."

**Tier 2 — Performance discipline and Frontend error handling.**
- "Batched DB queries use `chunked_in_clause()` from
  `repositories/_batch.py`. Tests assert query counts via
  `assert_query_count()` from `tests/_helpers/query_count.py`."
- "User-visible errors go through `Alpine.store('toast').push()`. No
  `alert()`, no silent `catch`."

**Tier 3 — Patterns we've removed.**
- "Cross-component state in Alpine uses `Alpine.store('name')`. Never
  `_x_dataStack`."
- "No sync filesystem I/O inside `async def`. Wrap in
  `asyncio.to_thread(...)`."
- "Two contexts: `CoreCtx` (always present) and `LiveCtx` (CatDV +
  Gemini wired). Routes declare which they need; `Optional` services
  are not added back."
- Cross-references to the relevant import-linter contracts and grep
  tests so a contributor seeing a CI failure can find the rule.

**Tier 4 — Database access** (only if tier 4 is pursued).
- "DB reads use `async with ctx.db.read() as conn:`. Writes use
  `async with ctx.db.write() as conn:`. Transactional reads that
  promote to writes use `write()` for the whole block."

---

## Manual acceptance flows

Each flow names the setup, the actions, and the observable result. A
colleague (or future agent) who didn't write the code should be able
to follow them on a running app and either tick them off or report
exactly which step broke.

### Tier 1 acceptance

1. **Provider transient errors do not orphan clips** (T1-1).
   - Setup: a populated cache (some entries in `clip_cache`,
     `proxy_cache`, `ai_store_files`).
   - Action: simulate a CatDV transport failure (block VPN or stop
     the CatDV server); navigate to `/cache?orphans=1`.
   - Expected: the page shows no orphans created by the transient
     failure; either an empty orphan list or only genuinely-missing
     clips. The page surfaces "transient check failed — try again"
     for clips that could not be deep-checked.

2. **Sync engine retries unknown failures** (T1-2).
   - Setup: queue a pending write to a clip; force the provider
     adapter to raise an unfamiliar exception type on apply.
   - Action: wait for the next sync tick (or `POST
     /api/sync/drain`).
   - Expected: the pending_op row stays at `status='pending'` with
     `attempts` incremented. After the configured max attempts, flips
     to `failed` with the humanised error.

3. **Job errors carry actionable detail** (T1-3).
   - Setup: misconfigure `GOOGLE_APPLICATION_CREDENTIALS` so Gemini
     calls fail with a credential error.
   - Action: trigger an annotation job; observe the job error.
   - Expected: the error message includes the status code and a
     snippet of the response body — enough for the user to know what
     went wrong, not just `HTTPStatusError`.

4. **GEMINI_API_KEY exposure is auditable** (T1-4).
   - Setup: `.env` with `GEMINI_API_KEY` set.
   - Action: `./run.sh` and tail the log.
   - Expected: WARNING line names the browser exposure and the
     threat-model constraint. README has a "Security caveats" section
     that matches.

5. **Migration numbering check fires on collision** (T1-5).
   - Setup: create a test migrations dir with a `.txt` sentinel for
     0011 and a colliding `0011_other.sql`.
   - Action: run the migrations runner against this dir.
   - Expected: runner raises with a clear message; production
     `data/app.db` still boots without errors and warns about the
     pre-existing `0011_studio.sql` entry in `schema_migrations`.

6. **CatDV query sanitisation rejects injection** (T1-6).
   - Setup: a populated CatDV catalog.
   - Action: in the `/clips` search box, enter a query containing
     parens, quotes, backslashes, and the word `and`.
   - Expected: search runs safely; either the special characters are
     escaped or the input is rejected with a clear message. The clips
     list never returns wrong results because the query was reshaped.

7. **CatDV health probe does not take a seat** (T1-7).
   - Setup: CatDV server with seat cap reached.
   - Action: trigger the connection monitor probe (`POST
     /api/connection/retry`).
   - Expected: probe reports offline without making a `POST /session`
     call. Seat usage does not change.

### Tier 2 acceptance

1. **Cache page scales with batch size** (T2-1).
   - Setup: dev DB with progressively larger caches (10, 100, 1000
     clips).
   - Action: open `/cache`; measure response time.
   - Expected: response time grows sub-linearly with clip count;
     query count (visible in the new
     `assert_query_count`-instrumented logs) stays constant.

2. **Studio cancel surfaces final state** (T2-2).
   - Setup: studio page with a clip focused, a long-ish prompt.
   - Action: click Run, then click Cancel before the run finishes;
     wait.
   - Expected: button shows ⊘ Cancelled flash; OR (race) the run
     completes mid-cancel, toast says "Completed before cancel
     landed", review_items appear. Never silently disappears.

3. **Toast shows on fetch failure** (T2-3).
   - Setup: any page with a fetch-triggered action.
   - Action: turn off network (or stop the backend); trigger the
     action.
   - Expected: a toast appears with the failure detail. No `alert()`
     dialogs. No silent failure.

4. **No full reload on folder CRUD** (T2-4).
   - Setup: studio page with no folders.
   - Action: create a folder, then add a clip to it.
   - Expected: folder appears and the clip appears via partial swap;
     the browser does not reload the page (URL bar reload icon does
     not spin; scroll position preserved).

5. **Async eviction does not stall other requests** (T2-5).
   - Setup: CatDV-host deployment with a clip cached on a slow
     network mount.
   - Action: trigger an evict (`POST /api/cache/clip/.../evict`)
     while another request is in flight.
   - Expected: the other request returns promptly; the evict
     completes in the background; the source-grep test in CI fails
     loudly if anyone adds back a sync filesystem call inside an
     `async def`.

### Tier 3 acceptance

1. **CoreCtx / LiveCtx split is enforced** (T3-A).
   - Setup: clean checkout post-merge.
   - Action: attempt to write a route that uses a live-only service
     while declaring `CoreCtx`.
   - Expected: type-check (basedpyright / mypy) fails. The old
     `AppContext` shape no longer exists; no route asserts `ctx.foo
     is not None`.

2. **Filter reaches every render** (T3-A, Jinja consolidation).
   - Setup: any route rendering a template that uses
     `|bytes_human` or `|comma`.
   - Action: render the page.
   - Expected: filter applies correctly; no `UndefinedError` from a
     route whose router instantiated its own Jinja env.

3. **Studio components share state via Alpine.store** (T3-B).
   - Setup: studio page with a focused clip and an open compare card.
   - Action: switch the active prompt version via the picker; observe
     compare card, run button, URL.
   - Expected: all three update in sync. No `_x_dataStack` references
     remain in `studio.js` (the grep test enforces this in CI).

4. **HTMX-injected nodes are alive** (T3-B, lifecycle helper).
   - Setup: studio page; trigger any HTMX swap (folder kids,
     prompt-card swap).
   - Action: click an interactive control inside the swapped subtree.
   - Expected: control responds (Alpine wired, HTMX wired). No "dead
     clicks". The single lifecycle helper is the only handler doing
     re-init / re-process.

5. **Guardrails fail loudly** (T3-C).
   - Setup: clean checkout post-merge.
   - Action: introduce a deliberate regression — a route imports
     `httpx`, or a service references `_x_dataStack`, or an `async
     def` calls `os.unlink` directly.
   - Expected: CI fails with a clear message naming the rule and the
     offending file. The fix is to use the documented pattern.

### Tier 4 acceptance

Deferred to tier-4 brainstorm. Will name the bottleneck the change is
meant to relieve, the benchmark methodology, and the observable proof
of relief.
