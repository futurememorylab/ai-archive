# Tier 3 — Handoff Notes (for the agent picking this up)

**Status:** Tier 1 (PR #21) and Tier 2 (PR #23) are MERGED to `main` as of commit `74398b5`. Tier 3 has not started — no plan file, no worktree.

**Your job:** read this file + `docs/specs/2026-05-30-fix-prioritization-design.md` § "Tier 3 — Maintainability cleanup", then write the executable plan via `superpowers:writing-plans`, then execute via `superpowers:subagent-driven-development`.

**Why a handoff note instead of a full plan:** the previous agent's context was running low. Plan-writing for tier 3 is heavier than for tiers 1/2 because tier 3's refactors touch more files and have more design choices. A fresh agent with full context will write a better plan than a low-context agent rushing it.

---

## What lives on `main` now (post-tier-2)

| Helper / pattern | File | Use it |
|---|---|---|
| `NotFoundError(ProviderError)` + `is_provider_not_found(exc) -> bool` | `backend/app/archive/errors.py` | Any place deciding "is this exception evidence of absence?" |
| `humanise(exc) -> str` | `backend/app/services/errors.py` | All user-facing error strings (job errors, sync engine, etc.) |
| `chunked_in_clause(keys, chunk_size=400)` | `backend/app/repositories/_batch.py` | Any repo method taking a list of keys |
| `assert_query_count(conn, max_n)` | `tests/_helpers/query_count.py` | Pin N+1 regression boundaries in tests |
| `Alpine.store('toast').push(msg, {level})` | `backend/app/static/toast.js` | All user-visible JS errors and info |
| `CacheInspector.list_for_inventory(...)` | `backend/app/services/cache_inspector.py` | SQL-side filtering + pagination for cache-style inventory views (tier 3 may apply to clips page) |
| `mark_failed(bump_attempts=True)` repo flag | `backend/app/repositories/pending_operations.py` | Terminal-fail + attempts increment in one atomic SQL |
| `tests/_helpers/studio_state.py` mirror | — | Python mirror pattern for any JS state-machine logic worth testing |

| CLAUDE.md section | What it pins |
|---|---|
| "Error handling discipline" | bare `except Exception:` rules, `humanise`, `is_provider_not_found` |
| "Performance discipline" | `chunked_in_clause`, `assert_query_count`, the rule for new per-key methods |
| "Frontend error handling" | Alpine.store('toast'), bans on `alert()` / silent `.catch()` / `location.reload()` |

ADRs 0042 (provider-error narrowing), 0043 (Gemini API-key exposure), 0044 (migration numbering), 0046 (no N+1) all on `main`.

---

## Tier 3 scope (from the umbrella spec)

Three clusters in one tier branch:

### T3-A: Backend structural unify
- **AppContext split.** Currently 30+ fields, ~15 `Foo | None`. Split into `CoreCtx` (always present: settings, db, repos, write queue) + `LiveCtx` (catdv, ai_store, gemini, resolver, sync engine, cache services already wired). Routes declare which they need; type system carries the offline/online contract. Kills the `attach_provider` / `attach_ai_store` late-binding dance and every `assert ctx.foo is not None`.
- **Route deps unification.** Triple `get_ctx + _inspector + _actions` pattern in `routes/cache.py` becomes one `Annotated[LiveCtx, Depends(get_live_ctx)]`. Kills `_inspector` / `_actions` helpers across routes.
- **Jinja consolidation.** Four `Jinja2Templates(directory=...)` instances (`routes/cache.py`, `routes/connection.py`, `routes/ui.py`, `routes/pages/templates.py`) → one shared module. Filters (`bytes_human`, `comma`) and globals (`smpte`) consolidated.
- **Delete `_DictWrap`** (cache.py:396-415). Templates rewritten for direct dataclass access.
- **Consolidate evict impls.** Three ~70-line `_evict_X_impl` methods in `cache_actions.py:269-517` → one parameterised method driven by a `LayerPolicy` dataclass.

### T3-B: Frontend structural unify
- **`Alpine.store('studio')` replaces `_x_dataStack[0]` hacks.** ~4 places in studio.js reach into Alpine's private API. Stores are the documented Alpine pattern for cross-component state.
- **One HTMX-Alpine lifecycle helper** (`static/htmxAlpine.js`). Four scattered `htmx:afterSwap` handlers + manual `Alpine.initTree()` + `htmx.process()` calls reduce to one module. Studio uses it; future pages opt in.
- **Sweep toast/no-reload broader.** Already done in tier 2 across studio.js / review.js / liveSession.js. Tier 3 picks up any remaining sites (audit promptEditor.js again now that toast exists; also `review.js` line 99 `location.reload()` that tier 2 deliberately deferred).

### T3-C: Guardrails sweep
- **Import-linter contracts** extending `.importlinter`: routes may not import `httpx` directly; services + templates may not reference `_x_dataStack`; repositories may not import services. Each maps to a pattern tier 2 or earlier killed.
- **Generalise the grep tests.** `tests/unit/test_no_sync_fs_in_async.py` from tier 2 covers only `cache_actions.py`. Expand to scan ALL `async def` blocks in `backend/app/services/` for sync I/O.
- **Apply T2-1's `assert_query_count`** to the clips page render (`routes/pages/clips.py` or wherever it lives). Strongly suspected N+1 by analogy.
- **Delete documented dead code:** `_ = json` and the "reserved for future" comment at `cache_inspector.py:443-444` (if still present; tier 2 may have touched it).

### Expected scorecard (tier-3 target)
- **Strongly negative net line count.** This is the tier where the spec's "shorter codebase" goal actually lands. Expect ~-500 lines of production code.
- **3 new shared primitives:** `LayerPolicy`, `Alpine.store('studio')`, `htmxAlpine` lifecycle helper.
- **6+ duplications killed:** 4 Jinja envs → 1; 3 evict impls → 1; 4 `_x_dataStack` reach-ins → 1 store; `_DictWrap` → 0; late-binding dances → 0; route helper triples → 1 dep.
- **2 new ADRs:** `00NN-context-split-and-route-deps-rationale.md`, `00NN-alpine-store-not-x-data-stack-for-shared-state.md`. The NEXT available ADR number to check is **0047** (0045 = bulk-annotate, 0046 = no N+1; verify with `ls docs/adr/ | sort -V | tail -3` before assigning).

---

## Verified-fact crib sheet (saves you grep time)

- **Shared `templates` instance:** `from backend.app.routes.pages.templates import templates`. Pre-tier-3 it has the `smpte` global. Tier 3's Jinja consolidation should move `bytes_human` / `comma` filters here too — currently they're registered only in `routes/cache.py:49-50`.
- **Studio router:** prefix `/api/studio`. Functions: `create_folder` (line 50, body `FolderCreate`), `add_folder_clips` (line 86, body `AddClips`), `list_folders`, `rename_folder`, `delete_folder`, `list_folder_clips`. **NOT** `add_clips` — that name is the repo method.
- **Folder partials:** `_studio_folder.html` is the *kids* partial (clip cards). `_studio_folder_list.html` is the whole sidebar. `_studio_folder_card.html` is the new single-folder partial extracted in tier 2.
- **Studio test fixture pattern:** `importlib.reload(main_mod)` + `TestClient(app)`. Look at `tests/integration/test_studio_folder_list_polish.py` for the canonical example.
- **Cache test fixture pattern:** `_setenv(monkeypatch, tmp_path) + _make_app() + TestClient(app)`. Look at `tests/integration/test_routes_cache.py` for the canonical example.
- **Python state-machine mirror pattern:** `tests/_helpers/studio_state.py` is the verbatim mirror of `studio.js::runButtonLabel`. The Python tests in `tests/unit/test_studio_run_button_label.py` are the regression net. Apply this pattern for any JS state machine you add or change.
- **Python 3.14 venv:** in use; CLAUDE.md says "known-broken" but actually works for this project. **Use `asyncio.run()` not `asyncio.get_event_loop()`** in tests — the latter is deprecated in 3.10+.
- **`worktree.baseRef` is unset** on this machine; `EnterWorktree` defaults to branching from `origin/main`. **Always `git push origin main` before invoking `EnterWorktree`**, otherwise the worktree won't see your latest local commits.
- **Pre-existing test failure to IGNORE:** `tests/integration/test_routes_review.py::test_clip_detail_draft_controls_show_without_review_flag` was failing on `main` before tier 1 started. Don't try to fix it as part of tier 3 — it's tracked separately.
- **Symlinks for `./run.sh` from worktree:** `ln -s <parent>/.venv .venv && ln -s <parent>/.env .env && ln -s <parent>/data data`. Add `.venv` and `data` (bare names) to `.git/info/exclude` (the shared one; per-worktree exclude requires `extensions.worktreeConfig` which isn't enabled).
- **Sharp edge with shared `.venv`:** `./run.sh` runs `pip install -e .[dev]` from whatever directory it's invoked in, which rewrites the editable-install location to that directory. Running from a worktree breaks the parent's import paths. Easy to flip back: `cd <parent> && .venv/bin/pip install -e .[dev]`.

---

## Workflow pattern (mirror tier 1 + tier 2)

1. **`superpowers:writing-plans`** with the tier-3 scope above. Plan should split T3-A / T3-B / T3-C into ~12-15 tasks total. Each task TDD-style with failing-test-first.
2. **Push plan to `origin/main`** before creating the worktree.
3. **`superpowers:using-git-worktrees`** (or `EnterWorktree` directly): branch `fix/tier-3-maintainability`, path `.claude/worktrees/fix-tier-3-maintainability/`.
4. **Symlink `.env`, `data/`, `.venv`** from parent.
5. **Baseline:** `.venv/bin/pytest -q` should show ~958+ passing (+ tier-2 tests, + whatever PR #22 added), 1 pre-existing failure.
6. **`superpowers:subagent-driven-development`** with the plan path. One implementer subagent per task + two-stage review (spec compliance, then code quality).
7. **Tier close-out:** CLAUDE.md additions, ADRs added + indexed in `docs/decisions.md`, full `pytest -q` + `lint-imports`, scorecard, PR opened via `gh pr create`.

---

## Lessons from tier 1 + tier 2 reviews (apply to tier 3)

These are the patterns the spec/code reviewers caught. The next agent's reviewers will likely catch the same shapes — pre-empt them:

1. **`except BaseException` swallows `asyncio.CancelledError`.** Use `except Exception as exc:  # noqa: BLE001`. The codebase convention is `Exception` everywhere except event-loop watchdogs (`sync_engine._loop`).
2. **`max()` vs `min()` for batch attempts** — when aggregating across a group, picking max kills younger ops early. Use min for ceiling decisions.
3. **Two-step DB mutations are not atomic.** If you find yourself doing `mark_X` then `mark_Y` in sequence, consolidate into one SQL with both updates.
4. **JS labels MUST byte-match the Python mirror** — Unicode codepoints `✓` (U+2713), `⊘` (U+2298), `⟳` (U+27F3), `…` (U+2026), `▶` (U+25B6), `·` (U+00B7). Test asserts string equality.
5. **`Header(None, alias="HX-Request")`** is the FastAPI pattern. Check `hx_request == "true"`.
6. **Templates expect `with context`** for Jinja includes when the partial references variables from the outer scope.
7. **`asyncio.run()` not `asyncio.get_event_loop()`** in tests under Python 3.14.
8. **Module-level imports** are the codebase convention (tier 1's review established this). Lazy imports inside methods are NOT idiomatic here.
9. **Regex guardrails need `\(`** when banning function calls (otherwise `os.unlink` passed as a callable to `asyncio.to_thread` false-positives).
10. **Sed `-i.bak` resets file-read tracking** in the Edit tool. After a bash `sed`, you have to `Read` again before `Edit`.

---

## Deferred items to absorb (or explicitly defer further)

From tier 1's final review (still open):
- `sync_max_attempts: int = Field(default=10, ge=1)` pydantic constraint (currently no lower bound).
- NOT_FOUND adapter test coverage for `list_clips` and `list_field_definitions` (only `get_clip` is tested).
- `apply_changes` put-clip race window test (clip deleted between GET and PUT).
- Dev-DB cleanup of orphan testbench tables from the reverted PR #9 (out of tier 1 scope by your explicit call).

From tier 2's final reviews (still open):
- `console.warn` on null-selector guards in HTMX swap (`addSelected`, `createFolder`) — silent no-op if the target element isn't found.
- `_poll()` max-retry counter for permanent 5xx (currently infinite retry until page reload).
- `lastElementChild` fragility in `createFolder` — breaks if `_studio_folder_card.html` ever grows sibling root nodes.
- `clipAnnotate.js`/`promptEditor.js` re-audit now that toast exists.
- `review.js:99` `location.reload()` (deferred from tier 2's T2-4 scope).

Three Minor cosmetic items from tier 2 not worth fixing standalone but easy to absorb during the structural cleanup:
- Inconsistent `err.message || err` vs `err.message || String(err)` in toast calls.
- `review.js::applyStay` toast formatted as one-liner without braces (inconsistent with rest of file).
- studio.js test file: `import pytest` unused.

---

## Tier 4 (DB connection pool) — separate, optional

Tier 4 is the SQLite connection pool change (reader pool + dedicated writer with app-level mutex). It's **out of scope for tier 3** and was deliberately deferred during brainstorming because:

1. It touches 120 `ctx.db` call sites mechanically.
2. It has more open design questions than the rest of tier 3 combined (pool size, mutex granularity, transactional reads-that-promote-to-writes).
3. Its perf claims should be benchmarked against a tier-3 baseline before being justified.

Don't start tier 4 until tier 3 is merged. Tier 4 will need its own brainstorming session (via `superpowers:brainstorming`) to nail down the API shape.

---

## Key files to read before starting

1. `docs/specs/2026-05-30-fix-prioritization-design.md` — the umbrella spec. Tier 3 section is your scope.
2. `docs/plans/tier-1-data-loss.md` — pattern reference for how to structure the executable plan (16 tasks, TDD per step).
3. `docs/plans/tier-2-user-feel.md` — newer pattern reference, includes the post-audit revisions that fixed line-ref drift / function-name mismatches.
4. `docs/adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md` — example ADR shape (covers two tasks; tier 3's "context split + route deps" ADR will be similar).
5. `CLAUDE.md` — current rules and primitives. Read all of "Error handling discipline", "Performance discipline", "Frontend error handling". Tier 3 adds new sections; respect the existing ones.
6. `backend/app/context.py` — the AppContext god dataclass. T3-A's centerpiece.
7. `backend/app/services/cache_actions.py:269-517` — the three duplicated evict methods. T3-A's biggest line-count reduction target.
8. `backend/app/static/studio.js` — 499 lines pre-tier-3; the `_x_dataStack[0]` hacks are the targets. T3-B's biggest line-count reduction target.
