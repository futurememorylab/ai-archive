# Tier 2 architecture branch — handover

**Date:** 2026-05-23
**Branch:** `claude/tender-hawking-rp1zc` (10 commits, pushed to origin)
**Source plan:** `docs/plans/2026-05-23-codebase-architecture-tier-2-and-beyond.md`
**Execution summary:** `docs/adr/0022-tier-2-architecture-execution.md`

This note captures what's worth picking up next, in priority order, with
enough context that a future contributor (human or agent) can grab any
item and start without re-reading the whole plan.

---

## Green-state at hand-off

- `pytest`: 626 passed, 3 skipped, 0 failed (1m04s)
- `ruff check backend/`: clean
- `ruff format --check`: clean
- `lint-imports`: 3 contracts kept
- `basedpyright backend/ tests/`: 0 errors / 0 warnings (against baseline)
- `interrogate -c pyproject.toml`: 30.7% — passes (gate at 30)

The branch is rebased on `main` and pushed. PR not opened — open one if/when ready.

---

## Deferred work (from plan §2-§6, in suggested order)

### 1. basedpyright ratchet steps 2–4  *(highest ROI)*

PR E introduced typed `get_ctx`, which surfaced 36 new latent Optional
issues (most are `AppContext` services that are `T | None` in offline
boots). The baseline absorbed them; now drain the baseline.

**Approach.** Promote one rule at a time from baseline → error, fix
all sites, refresh baseline:

```bash
# Pick one rule:
.venv/bin/basedpyright backend/ tests/ | grep reportOptionalMemberAccess | head
# Fix the sites (add `is None` guards, or split AppContext into
# always-present vs optional services).
.venv/bin/basedpyright --writebaseline backend/ tests/
```

Order to attack (by count in current baseline, biggest first):
1. `reportOptionalMemberAccess` (~40) — add guards or 503 in routes
2. `reportArgumentType` (~78) — most are wire-shape JSON dicts; type
   the CatDV adapter return shapes as `TypedDict` / Pydantic
3. `reportOptionalSubscript` (~38) — narrow `Optional[dict]` indexing
4. `reportAttributeAccessIssue` (~58 minus the chunk PR E already
   killed) — remaining ones are likely `Any`-typed JSON paths

When the baseline is < 50, promote `typeCheckingMode` from `"basic"`
to `"standard"`, then `"strict"`. Goal: empty `.basedpyright/baseline.json`.

### 2. Split AppContext into "core" vs "optional services"

Closely related to (1). Right now `AppContext` has both
always-present fields (`db`, `prompts_repo`, `event_bus`) and
maybe-None fields (`archive`, `sync_engine`, `connection_monitor`,
`workspace_manager`, `lru_eviction`, `media_prefetcher`,
`cache_inspector`, `cache_actions`, `proxy_resolver`, `gemini`,
`ai_store`, `catdv`, `_gcs_service`). Routes guard with
`if ctx.X is None: raise HTTPException(503, ...)` ad-hoc.

**Proposal.** Two dataclasses:

```python
@dataclass
class AppCore:
    settings: Settings
    db: aiosqlite.Connection
    # ... all always-present repos + write_queue + event_bus

@dataclass
class ExternalServices:
    archive: ArchiveProvider
    ai_store: AIInputStore
    # ... all the rest (non-Optional here — present iff online)

@dataclass
class AppContext:
    core: AppCore
    external: ExternalServices | None
```

Routes that need external services do `external = get_external(request)`
(raises 503 on None). Eliminates ~30 Optional-deref warnings in one pass.

### 3. ruff C901 (mccabe complexity) + radon  *(plan §2.2)*

Cheap. In `pyproject.toml`:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC", "C90"]

[tool.ruff.lint.mccabe]
max-complexity = 10
```

Expect 5–10 immediate offenders. Hotspots to look at first:
- `backend/app/services/annotator.py::run_job`
- `backend/app/services/cache_inspector.py::_load_*` helpers
- `backend/app/context.py::_build_archive_subsystem` (still 115 LOC
  after PR F — by design, decision tree across `use_catdv × forced_offline
  × login_failed × proxy_source`)

For each, either refactor or annotate `# noqa: C901` with a one-line
justification. Layer `radon cc -s -a backend/app` into CI for trend
tracking (not as a gate).

### 4. vulture dead-code sweep  *(plan §2.3)*

One-shot pass. Likely targets are leftover helpers from PR D's
`routes/pages.py` split and PR F's `context.build()` decomp.

```bash
.venv/bin/pip install vulture
vulture backend/ --min-confidence 80 --exclude backend/migrations
```

Don't add to CI — too many false positives on FastAPI/Pydantic
dynamic-dispatch code.

### 5. Sharpen archive adapters  *(plan §4.3)*

Both `archive/providers/catdv/adapter.py` (~600 LOC) and
`archive/providers/fs/adapter.py` (~416 LOC) mix transport with
payload shaping.

**CatDV adapter.** The mapping is already extracted into
`archive/providers/catdv/payload.py` and `mapping.py`. Move the
remaining inline shaping out of `adapter.py` so adapter is
"talk to CatDV", mapping is "translate the response shape." Goal:
adapter under 400 LOC, no `dict[str, Any]` returns from internal
methods.

**FS adapter.** Filesystem walk already factored into
`archive/providers/fs/sidecar.py`. Move remaining inline disk I/O
there. Goal: adapter under 300 LOC.

The contract test
`tests/contract/test_archive_provider.py::test_capabilities_shape`
runs against both — if it still passes, the split is behaviour-preserving.

### 6. Live history helpers — move out of routes/pages/clips.py

PR D left `_build_clip_view_model_for_live` and
`_build_draft_view_model_for_live` in `routes/pages/clips.py` because
`routes/live.py` imports them. That's a route → route import — not
forbidden by import-linter today, but smells wrong.

**Fix.** Move them to `backend/app/services/live_context.py` (which
already exists for related Live-session helpers). Update the imports
in `routes/live.py`. Tests should still pass unchanged.

### 7. interrogate raise gate toward 70%

Plan §3.3 originally asked for `fail-under = 70`. Current is 30%
(see ADR 0022 for why). To raise:

- Start with `backend/app/services/` and `backend/app/routes/` —
  most user-visible, most missing
- Raise `fail-under` in increments of 10
- Add `--ignore-regex "^_"` to exclude private callables — cuts the
  denominator meaningfully
- Repos are CRUD-heavy; consider either a blanket repo docstring rule
  or excluding them with `--ignore-regex` if the SQL is
  self-documenting

### 8. Consider FastAPI `Depends(get_ctx)` pattern

PR E used the explicit-call style (`ctx = get_ctx(request)`) for
minimum churn. The `Depends`-style would be:

```python
from typing import Annotated
from fastapi import Depends
from backend.app.deps import get_ctx
from backend.app.context import AppContext

Ctx = Annotated[AppContext, Depends(get_ctx)]

@router.get("/api/x")
async def handler(ctx: Ctx):
    ...
```

Cleaner; removes `request: Request` from handlers that don't otherwise
need it. Defer until (2) lands — `AppCore` / `ExternalServices` split
will change what gets `Depends`-injected anyway.

---

## Known minor issues (not strictly architecture)

These came up during the session but weren't in the plan:

1. **Two `DeprecationWarning` from websockets in test output.** Pinned
   `websockets` version in google-genai is calling deprecated APIs.
   Not actionable until google-genai bumps; harmless.

2. **`test_raises_when_proxy_unreadable` is skipped under root.**
   PR A added the skip. CI presumably runs as root in our cloud env.
   If a non-root CI lane is set up, the test will re-enable itself
   automatically.

3. **`.env.example` documents `gemini-2.5-pro` but `Settings.gemini_model`
   defaults to `gemini-2.5-flash-lite`.** PR A updated the test to
   match the code. The example file is still misleading; consider
   updating `.env.example` to `gemini-2.5-flash-lite`, or change
   the default — either way pick one.

4. **`backend/app/main.py::health` uses `getattr(request.app.state, "ctx", None)`**
   instead of `get_ctx(request)`. Intentional — health must work
   before lifespan finishes. Documented in PR E commit. Don't "fix" it.

---

## How the test suite is wired

The session-scoped autouse fixture in `tests/conftest.py`
(`_safe_test_env_defaults`) seeds the required `Settings` env vars
with safe values **only if not already set**. This means:

- CI / cloud env without `.env` → fixture provides defaults; tests
  boot the app offline (CATDV_OFFLINE=true), no real CatDV reached.
- Developer with their own `.env` → fixture is a no-op for fields they
  already have set; `CATDV_OFFLINE` is the one belt-and-braces field
  the fixture overrides.
- Tests that need to exercise the "tried to connect but failed" code
  path (`test_catdv_unreachable_at_startup_boots_offline`) explicitly
  override `CATDV_OFFLINE=false` via monkeypatch.

If you add a test that needs real external services, document the env
override clearly — the default is "no external network."

---

## Where to start

If you have **2 hours**: do (3) C901/radon + (4) vulture. Both are
cheap mechanical wins and leave the codebase a touch tighter.

If you have **a day**: do (2) AppContext split + first batch of (1)
basedpyright ratchet. This is the biggest type-safety win available
and finishes what PR E started.

If you have **a week**: do all of (1) through (5). The basedpyright
baseline should be empty and the adapters should be under 400 LOC at
the end.
