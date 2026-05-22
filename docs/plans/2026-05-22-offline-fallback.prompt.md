# Remote subagent prompt — implement Offline Fallback Mode

> Paste the block below into a fresh Claude Code session running on a machine
> with this repo checked out. The agent has no memory of the conversation that
> wrote the plan — everything it needs is in the prompt.

---

You're picking up a written implementation plan. Everything you need to do
the work is in the repo at HEAD on `main` — read it, don't guess.

## Repo

`catdv-annotator` (Python 3.12+, FastAPI). You are already in the repo root.
Confirm with `git rev-parse --show-toplevel` and `cat CLAUDE.md` and
`cat ../CLAUDE.md` to load both project- and parent-scope conventions
(CatDV session discipline, decisions.md format, venv usage, network notes).

## Your task

Execute the plan at:

```
docs/plans/2026-05-22-offline-fallback.md
```

This adds an **offline mode** to the annotator: when CatDV is unreachable
(either forced via `CATDV_OFFLINE=true` or detected at runtime by
`ConnectionMonitor`), the app degrades to serving the SQLite clip cache and
the local proxy cache, fails-fast writes into the existing `WriteQueue`,
and shows a state chip in the topbar. The user reconnects via
`POST /api/connection/retry` — there is no background re-probing while
offline.

The spec lives at `docs/specs/2026-05-22-offline-fallback-design.md` —
read it before Task 1. It explains why the connection state machine has
exactly three states (`online` / `offline` / `forced_offline`), why
`SyncEngine` already covers write-queuing (no new write path), why
`apply_changes` fails fast with `RetryableError` instead of silently
no-op'ing, and why auth-fail at startup is treated as offline rather
than fatal. Do **not** re-derive those decisions; they're locked.

## Execution rules

- **One task at a time, in order, with checkpoints.** Use the
  `superpowers:executing-plans` skill to drive the loop. Treat each Task
  in the plan as one batch: write the failing test(s), confirm they fail,
  write the code, confirm they pass, commit.
- **TDD is required** — the plan is structured around it. If a step says
  "write the failing test", run the test and confirm the failure mode
  before writing implementation code. Don't skip ahead.
- **Frequent commits.** Each task ends with a commit step using the exact
  message shown in the plan. Don't squash tasks together.
- **Don't expand scope.** The plan is the spec. If a step looks redundant
  or over-specified, complete it as written. If something is genuinely
  unbuildable as specified, stop and surface the contradiction — don't
  improvise.
- **No new dependencies.** Everything builds on existing modules
  (FastAPI, Jinja2, aiosqlite, httpx, pytest, pytest-asyncio, htmx
  already vendored). If you find yourself reaching for a new library,
  you're off-track.
- **Preserve backward compatibility for existing callers.** The plan
  notes that `ClipCacheRepo.list_by_catalog` is extended via opt-in
  kwargs so `CacheInspector.deep_orphans` keeps working; the new
  `is_online_provider` ctor param on `CatdvArchiveAdapter` defaults to
  `None` so existing adapter tests keep passing. Don't change the
  default behavior of either.

## Verification

- Use the venv: `.venv/bin/pytest` and `.venv/bin/python` (never system
  `python3` — see `~/.claude/CLAUDE.md` global rules).
- After each task: run that task's specific tests with `-v` and confirm
  the expected pass/fail at each step.
- After Task 6 (ConnectionMonitor changes): also run the existing monitor
  tests and adjust them per Task 6 Step 7 — the loop now halts on
  failure, so any test that asserted continuous probing must be updated:
  ```
  .venv/bin/pytest tests/integration/test_connection_monitor.py -v
  ```
- After Task 4 (adapter list_clips offline path): re-run the existing
  adapter tests to confirm no regression:
  ```
  .venv/bin/pytest tests/ -k catdv_adapter -v
  ```
- After Task 9 (integration smoke): run the full unit suite and the new
  integration tests:
  ```
  .venv/bin/pytest tests/unit -q
  .venv/bin/pytest tests/integration/test_clip_cache_list_offline.py \
                   tests/integration/test_catdv_adapter_offline_fallback.py \
                   tests/integration/test_connection_monitor_halt_and_retry.py \
                   tests/integration/test_routes_connection_retry.py \
                   tests/integration/test_offline_mode_e2e.py -q
  ```
  Both must be green. If unrelated VPN-dependent integration tests fail
  (ones that talk to live CatDV at `192.168.1.41:8080`), note them but do
  not attempt to fix — that's out of scope.

## CRITICAL — CatDV / session safety

You will **not** call the live CatDV REST API. All of your work is in
Python source + tests + Jinja templates. Do **not**:

- `ping`, `curl`, `nc`, or otherwise probe `192.168.1.41`.
- Open a `CatdvClient` against the real `CATDV_BASE_URL` from `.env`.
- Start a `uvicorn` / `./run.sh` instance for any reason. The live CatDV
  has a 2-seat license limit; an accidentally leaked `JSESSIONID` locks
  out the operator and the human web client for minutes at a time. The
  parent `CLAUDE.md` documents this in detail under "CatDV session
  discipline (license seats)" — read it.
- Run any test marked `@pytest.mark.live` or `@pytest.mark.requires_vpn`
  (if such markers exist — grep `tests/` to check).

Your tests use `tests/fakes/fake_catdv.py` (`running_fake_catdv`) which
spins up an in-process FastAPI fake on a random localhost port — that is
safe and is the only acceptable "live HTTP" surface in your test runs.

## Operator-only steps — SKIP

The following plan steps require a running browser and/or the
Pragafilm WireGuard VPN being up on the operator's machine. Skip them
and mark each checkbox `N/A — operator-only` in your final report:

- **Task 8 Step 3** (manual smoke with `./run.sh` and `curl localhost:8765`)
  — operator runs this after merge.
- **Task 10 Step 8** (UI smoke: open `http://localhost:8765/` in a
  browser, verify red chip / hidden buttons / banner visually) —
  operator runs this after merge.

For everything else in Tasks 8 and 10 — the Python wiring in
`context.py`, the Jinja templates, the CSS, the route changes —
implement and commit normally. Those parts are exercised by the
integration tests in Task 9 (`test_offline_mode_e2e.py`).

## Spec gaps you may hit and what to do

1. **`_ROW_COLS` may not include `blob_json`** (Task 2 Step 3). Grep
   `backend/app/repositories/clip_cache.py` for the actual column list.
   The cached canonical clip is stored as JSON in some column —
   typically `blob_json`, but verify and adjust the SQL `LIKE` clause
   and the `_clip_from_json(row_dict[...])` lookup to the real column
   name. If the column doesn't exist, fall back to searching `name`
   only — flag it in your report.

2. **`FieldDefCacheRepo` may not have a `list_all` method** (Task 3
   Step 4). Grep the repo class for whatever method returns all defs
   ignoring TTL. If only a TTL-respecting `list()` exists, add a sibling
   `list_all_stale(...)` method that does the same query without the TTL
   filter — keep the change minimal.

3. **The `/api/health` route file** isn't named in the plan. Grep for
   `"/api/health"` or `@router.get("/health")` in `backend/app/routes/`
   to locate it (Task 7 Step 1).

4. **Topbar template injection point** (Task 10 Step 5). Grep
   `backend/app/templates/` for an existing topbar, header, or layout
   include. If `base.html` doesn't have a header section to drop the
   chip into, add a small one rather than restructuring the existing
   layout. Flag the spot you chose.

5. **`build_app(ctx)` may not be the entrypoint name** in
   `backend/app/main.py` (Task 9 Step 1). Adapt the e2e test import to
   whatever the actual app factory is called.

For any of the above, if the codebase shape is meaningfully different
from what the plan assumes, **fix the test/implementation to match
reality and document the deviation in your final report**. Do not
restructure the plan's overall architecture.

## Definition of done

- Tasks 1–7, 9, 11 in the plan are all checked off.
- Task 8: all steps except Step 3 checked off; Step 3 marked
  `N/A — operator-only`.
- Task 10: all steps except Step 8 checked off; Step 8 marked
  `N/A — operator-only`.
- `git log --oneline` shows one commit per task, with the exact
  messages from the plan:
  - `feat(settings): add CATDV_OFFLINE env flag`
  - `feat(clip_cache): paginated, searchable list_by_catalog for offline mode`
  - `feat(catdv-adapter): stale-cache fallback + offline guard via is_online_provider`
  - `feat(catdv-adapter): offline list_clips serves from cache_repo`
  - `feat(proxy): LocalCacheOnlyResolver for offline mode`
  - `feat(connection-monitor): halt loop on failure, add retry_now, forced_offline ctor flag`
  - `feat(routes): POST /api/connection/retry + mode field in /state and /api/health`
  - `feat(context): boot offline on CATDV_OFFLINE or login failure`
  - `test(offline): end-to-end smoke for forced + auth-fail-degraded offline modes`
  - `feat(ui): connection chip + hide CatDV actions when offline`
  - `docs(offline): document CATDV_OFFLINE + auto-fallback + reconnect chip`
- `.venv/bin/pytest tests/unit -q` is green.
- `.venv/bin/pytest tests/integration/test_clip_cache_list_offline.py
  tests/integration/test_catdv_adapter_offline_fallback.py
  tests/integration/test_connection_monitor_halt_and_retry.py
  tests/integration/test_routes_connection_retry.py
  tests/integration/test_offline_mode_e2e.py -q` is green.
- `git status` is clean.
- Files created exist:
  - `tests/integration/test_clip_cache_list_offline.py`
  - `tests/integration/test_catdv_adapter_offline_fallback.py`
  - `tests/unit/test_local_cache_only_resolver.py`
  - `tests/integration/test_connection_monitor_halt_and_retry.py`
  - `tests/integration/test_routes_connection_retry.py`
  - `tests/integration/test_offline_mode_e2e.py`
  - `tests/unit/test_settings_offline.py`
  - `backend/app/templates/_connection_chip.html`
  - `backend/app/templates/clip_not_cached.html`

## Reporting back

When done, post a single message summarising:

1. The commit hashes you produced (one line each: `<hash>  <subject>`).
2. The output of the final `.venv/bin/pytest tests/unit -q` run (last 5 lines).
3. The output of the final integration test run from the Definition of
   Done section (last 5 lines).
4. Anything you encountered that the plan didn't anticipate, with the
   resolution you chose. Be specific — "had to adjust signature X
   because Y" is useful; "made some small tweaks" is not. In particular,
   flag if:
   - The `_ROW_COLS` constant didn't include the JSON blob column you
     expected (Task 2 Step 3).
   - `FieldDefCacheRepo` lacked a stale-read method and you added one
     (Task 3 Step 4).
   - The `/api/health` route lives in an unexpected file (Task 7 Step 5).
   - The base/topbar template needed structural changes to host the
     connection chip (Task 10 Step 5).
   - Existing `tests/integration/test_connection_monitor.py` tests broke
     in ways the plan didn't predict — list the assertions you changed
     and why.
   - You couldn't make a test pass and chose to skip it with `xfail` —
     this should be rare; explain.
5. The list of operator-only checkboxes left unchecked, all annotated
   `N/A — operator-only`:
   - Task 8 Step 3
   - Task 10 Step 8

Do not open a PR. Leave the work on the current branch (`main`); the
operator will review the commits and decide how to integrate.
