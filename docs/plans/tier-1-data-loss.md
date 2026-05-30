# Tier 1 — Data loss and silent failures: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. TDD discipline (write failing test → confirm fail → implement → confirm pass → commit) per `superpowers:test-driven-development`.

**Goal:** Close seven data-loss / silent-failure issues in `catdv-annotator` and install guardrails (tests, helpers, ADRs, CLAUDE.md rules) so the anti-patterns cannot be reintroduced without CI catching it.

**Architecture:** Two new tiny helper modules (`archive/errors.py` extension and new `services/errors.py`) become the canonical shape for narrowing provider exceptions and humanising user-facing errors. Cache inspector, workspace manager, sync engine, and annotator all route through these helpers. Migrations runner gains a sentinel-aware gap check. CatDV client gains a no-reauth health probe and an allowlist-based query sanitiser. Startup logs a WARNING about the GEMINI_API_KEY browser exposure. Three ADRs document the recurring anti-patterns.

**Tech Stack:** Python 3.13 + FastAPI + aiosqlite (SQLite); pytest + pytest-asyncio; pydantic-settings.

**Pre-flight (executor):**
- Source the worktree via `superpowers:using-git-worktrees`. Branch: `fix/tier-1-data-loss`, based on `main` at the head that contains commit `1816e86 docs(specs): add fix prioritization umbrella spec`. Worktree path: `.claude/worktrees/fix-tier-1-data-loss/`.
- Verify the venv: `.venv/bin/pytest --version` returns 8.x (any).
- Verify baseline tests pass before starting: `.venv/bin/pytest -q`.

**Spec reference:** `docs/specs/2026-05-30-fix-prioritization-design.md` — read the "Tier 1 — Stop the bleeding" section before starting any task.

**ADR numbers assigned:** 0042 (provider-error narrowing), 0043 (API-key exposure), 0044 (migration numbering and the 0011 gap). Highest existing ADR is 0041 (verified 2026-05-30).

---

## File Structure

**New files:**
- `backend/app/services/errors.py` — `humanise(exc) -> str` helper.
- `backend/migrations/0011_REVERTED.txt` — sentinel.
- `docs/adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md`
- `docs/adr/0043-gemini-live-api-key-exposure-accepted-risk.md`
- `docs/adr/0044-migration-numbering-and-the-0011-gap.md`
- `tests/unit/test_provider_error_narrowing.py`
- `tests/unit/test_humanise.py`
- `tests/unit/test_catdv_query_sanitisation.py`
- `tests/integration/test_cache_inspector_orphans_transient.py`
- `tests/integration/test_workspace_manager_transient.py`
- `tests/integration/test_sync_engine_retryable_unknown.py`
- `tests/integration/test_lifespan_api_key_warning.py`
- `tests/integration/test_migrations_gap_check.py`
- `tests/integration/test_catdv_client_health_no_reauth.py`

**Modified files:**
- `backend/app/archive/errors.py` — add `NotFoundError` exception + `is_provider_not_found()` helper.
- `backend/app/archive/providers/catdv/adapter.py` — translate "NOT_FOUND" CatdvError to `NotFoundError`.
- `backend/app/services/cache_inspector.py:322-328` — `list_orphans(deep=True)` narrows exceptions.
- `backend/app/services/workspace_manager.py:132, :160` — `prepare()` narrows exceptions; introduces `cache_state='transient_error'`.
- `backend/app/services/sync_engine.py:202-204` — unknown exceptions → `mark_retryable`; honour `max_attempts`.
- `backend/app/services/annotator.py:114` — route error msg through `humanise()`.
- `backend/app/services/catdv_client.py` — `_call_json(reauth=True)` param; `health()` uses `reauth=False`; replace `q.replace("(", "").replace(")", "")` with allowlist sanitiser.
- `backend/app/settings.py` — new `sync_max_attempts: int = 10`.
- `backend/app/startup.py` — new `warn_browser_secret_exposure(settings)` function.
- `backend/app/main.py` — call `warn_browser_secret_exposure` in `lifespan`.
- `backend/app/migrations_runner.py` — sentinel-aware gap check + orphan-entry warning.
- `README.md` — new "Security caveats" section.
- `CLAUDE.md` — new "Error handling discipline" section at tier close-out.
- `docs/decisions.md` — index updated with 0042/0043/0044.

---

## Task 1: NotFoundError exception + is_provider_not_found helper (T1-1 foundation)

**Files:**
- Modify: `backend/app/archive/errors.py`
- Create: `tests/unit/test_provider_error_narrowing.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_provider_error_narrowing.py`:

```python
"""is_provider_not_found narrows provider exceptions to a documented
'this clip is gone' signal. Used by CacheInspector.list_orphans(deep=True)
and WorkspaceManager.prepare so transient errors don't get treated as
permanent absence (which would orphan / fail clips on a VPN flap)."""

import httpx
import pytest

from backend.app.archive.errors import (
    AuthError,
    FatalProviderError,
    NotFoundError,
    ProviderError,
    RetryableError,
    is_provider_not_found,
)


def test_not_found_error_is_recognised():
    assert is_provider_not_found(NotFoundError("clip 42 not found")) is True


def test_httpx_404_is_recognised():
    request = httpx.Request("GET", "http://example/x")
    response = httpx.Response(404, request=request)
    exc = httpx.HTTPStatusError("404", request=request, response=response)
    assert is_provider_not_found(exc) is True


def test_httpx_500_is_not_recognised():
    request = httpx.Request("GET", "http://example/x")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    assert is_provider_not_found(exc) is False


def test_retryable_error_is_not_recognised():
    assert is_provider_not_found(RetryableError("flaky")) is False


def test_auth_error_is_not_recognised():
    assert is_provider_not_found(AuthError("bad creds")) is False


def test_fatal_provider_error_is_not_recognised():
    """FatalProviderError on its own is not a NotFound signal — it covers
    many failures including connect errors. Only the NotFoundError subclass
    is treated as documented absence."""
    assert is_provider_not_found(FatalProviderError("connection refused")) is False


def test_arbitrary_exception_is_not_recognised():
    assert is_provider_not_found(RuntimeError("anything")) is False


def test_not_found_error_inherits_from_provider_error():
    assert issubclass(NotFoundError, ProviderError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_provider_error_narrowing.py -v`

Expected: `ImportError: cannot import name 'NotFoundError'` (and `is_provider_not_found`) from `backend.app.archive.errors`.

- [ ] **Step 3: Add the exception class and helper**

Append to `backend/app/archive/errors.py`:

```python
import httpx


class NotFoundError(ProviderError):
    """The named clip is documented as absent by the provider (e.g. CatDV 404).

    Distinct from FatalProviderError, which covers any non-retryable failure
    including transport-side ones. Only NotFoundError is safe evidence that
    a clip should be treated as 'gone' (orphaned, evictable, etc.).
    """


def is_provider_not_found(exc: BaseException) -> bool:
    """True iff `exc` is documented evidence that a clip is absent upstream.

    Recognises NotFoundError and httpx.HTTPStatusError with status 404.
    Returns False for transport failures, auth errors, retryable errors,
    and anything else — callers MUST treat False as 'transient, try later',
    never as 'gone'.
    """
    if isinstance(exc, NotFoundError):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
        return True
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_provider_error_narrowing.py -v`

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/errors.py tests/unit/test_provider_error_narrowing.py
git commit -m "feat(archive): add NotFoundError + is_provider_not_found helper

Foundation for T1-1 (narrow provider exceptions, never treat arbitrary
exceptions as evidence of absence). CacheInspector.list_orphans and
WorkspaceManager.prepare will route through this helper in subsequent
commits. Recognises NotFoundError and httpx.HTTPStatusError(404);
returns False for anything else.

Refs: docs/specs/2026-05-30-fix-prioritization-design.md (T1-1)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: CatDV adapter translates 404 / NOT_FOUND to NotFoundError (T1-1)

**Files:**
- Modify: `backend/app/services/catdv_client.py:98-111` (the `_call_json` ERROR-envelope branch)
- Modify: `backend/app/archive/providers/catdv/adapter.py` (translate `CatdvError("NOT_FOUND...")` to `NotFoundError`)
- Create: `tests/integration/test_catdv_adapter_not_found.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_catdv_adapter_not_found.py`:

```python
"""CatDV adapter raises NotFoundError on documented 'not found' responses.

NotFoundError is the only exception type CacheInspector / WorkspaceManager
should treat as evidence a clip is absent. CatdvError generally means 'the
server said no' for many reasons; only the NOT_FOUND subset deserves the
narrower type."""

import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.archive.providers.catdv.adapter import CatdvArchiveProvider
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_get_clip_raises_not_found_for_missing_clip(monkeypatch):
    with running_fake_catdv() as (base_url, fake):
        # Do NOT add clip 999 to fake.clips; fake_catdv returns NOT_FOUND envelope.
        from backend.app.services.catdv_client import CatdvClient

        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            adapter = CatdvArchiveProvider(
                catdv_client=client,
                clip_cache_repo=None,
                field_def_cache_repo=None,
                clip_list_cache_repo=None,
                db_provider=lambda: None,
                is_online_provider=lambda: True,
            )
            with pytest.raises(NotFoundError):
                await adapter.get_clip("999")
```

If the fake_catdv module does not yet return a NOT_FOUND envelope shape for unknown clip IDs, the test will fail at the `pytest.raises(NotFoundError)` boundary regardless; in that case extend `tests/fakes/fake_catdv.py` to emit `{"status": "ERROR", "errorMessage": "NOT_FOUND: clip 999"}` for unknown IDs as part of this step.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_not_found.py -v`

Expected: FAIL — either `CatdvError` raised (not `NotFoundError`), or import error if `NotFoundError` isn't yet wired in the adapter.

- [ ] **Step 3: Translate NOT_FOUND envelopes at the adapter boundary**

Inspect `backend/app/archive/providers/catdv/adapter.py` near line 125 (the `get_clip` method that re-raises `FatalProviderError`). Add an upstream check for `CatdvError` whose message starts with `"NOT_FOUND"` (the documented prefix the CatDV server uses) and re-raise as `NotFoundError`:

```python
from backend.app.archive.errors import NotFoundError
# ... existing imports

# inside get_clip / get_field_def / etc., wherever CatdvError is caught:
except CatdvError as exc:
    msg = str(exc)
    if msg.startswith("NOT_FOUND") or "not found" in msg.lower():
        raise NotFoundError(msg) from exc
    raise FatalProviderError(msg) from exc
```

If the adapter currently catches a broader exception type, narrow to `CatdvError` first; the FatalProviderError fallback stays for the non-NOT_FOUND branch.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_catdv_adapter_not_found.py -v`

Expected: 1 passed.

Also re-run the full unit suite to confirm no regression: `.venv/bin/pytest tests/unit/ -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/providers/catdv/adapter.py tests/integration/test_catdv_adapter_not_found.py tests/fakes/fake_catdv.py
git commit -m "feat(archive): translate CatDV NOT_FOUND to NotFoundError

Adapter now raises the narrower NotFoundError (added in previous commit)
when the upstream envelope explicitly says NOT_FOUND, instead of the
broad FatalProviderError. This lets downstream callers
(CacheInspector.list_orphans, WorkspaceManager.prepare) safely use
is_provider_not_found() to distinguish 'clip is gone' from 'transport
failed'.

Refs: T1-1

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: CacheInspector.list_orphans(deep=True) narrows exceptions (T1-1)

**Files:**
- Modify: `backend/app/services/cache_inspector.py:283-330` (`list_orphans` method body)
- Create: `tests/integration/test_cache_inspector_orphans_transient.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_cache_inspector_orphans_transient.py`:

```python
"""list_orphans(deep=True) must NOT treat transient provider errors as
evidence a clip is gone. Doing so means a VPN flap could mark hundreds
of legitimately-cached clips as orphans, and the next 'Evict orphans'
action would wipe the data."""

from pathlib import Path

import httpx
import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.services.cache_inspector import CacheInspector

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


class _RaisingProvider:
    """Stub provider that raises whatever exception was configured."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def get_clip(self, pcid: str):
        raise self._exc


async def _seed_one_clip(conn):
    await conn.execute(
        "INSERT INTO clip_cache(provider_id, provider_clip_id, name, canonical_json, fetched_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        ("catdv", "42", "clip 42", '{"id": 42}'),
    )
    await conn.commit()


@pytest.mark.asyncio
async def test_deep_orphan_check_does_not_orphan_on_transient_error(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_one_clip(conn)

        # Provider raises a transport-style error (NOT a NotFoundError).
        request = httpx.Request("GET", "http://example/x")
        response = httpx.Response(500, request=request)
        provider = _RaisingProvider(
            httpx.HTTPStatusError("flaky", request=request, response=response)
        )

        inspector = CacheInspector(
            db_provider=lambda: conn,
            provider=provider,
        )
        orphans = await inspector.list_orphans(deep=True)
        assert orphans == [], (
            f"clip 42 should NOT be orphaned by a transient 500; got {orphans}"
        )


@pytest.mark.asyncio
async def test_deep_orphan_check_orphans_on_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        await _seed_one_clip(conn)

        provider = _RaisingProvider(NotFoundError("clip 42 absent upstream"))

        inspector = CacheInspector(
            db_provider=lambda: conn,
            provider=provider,
        )
        orphans = await inspector.list_orphans(deep=True)
        assert len(orphans) == 1
        assert orphans[0].clip_key == ("catdv", "42")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_cache_inspector_orphans_transient.py -v`

Expected: `test_deep_orphan_check_does_not_orphan_on_transient_error` FAILS — current implementation at line 327 catches `except Exception:` and orphans both clips.

- [ ] **Step 3: Narrow the deep-check exception handling**

Modify `backend/app/services/cache_inspector.py:322-328`. Replace:

```python
        if deep and self._provider is not None:
            cur = await db.execute("SELECT provider_id, provider_clip_id FROM clip_cache")
            for prov, pcid in await cur.fetchall():
                try:
                    await self._provider.get_clip(pcid)
                except Exception:  # noqa: BLE001
                    orphans.add((prov, pcid))
```

with:

```python
        if deep and self._provider is not None:
            from backend.app.archive.errors import is_provider_not_found

            cur = await db.execute("SELECT provider_id, provider_clip_id FROM clip_cache")
            for prov, pcid in await cur.fetchall():
                try:
                    await self._provider.get_clip(pcid)
                except BaseException as exc:
                    # Only documented absence (NotFoundError / 404) is evidence
                    # of orphaning. Transient errors (transport, auth, retryable)
                    # MUST NOT mark the clip orphan — Evict orphans would wipe
                    # legitimately-cached data on a VPN flap. See ADR 0042.
                    if is_provider_not_found(exc):
                        orphans.add((prov, pcid))
                    # else: silently skip; next deep call will retry.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_cache_inspector_orphans_transient.py -v`

Expected: 2 passed.

Re-run inspector unit tests for regression: `.venv/bin/pytest tests/unit/test_cache_inspector_host_local.py tests/integration/test_cache_inspector.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/cache_inspector.py tests/integration/test_cache_inspector_orphans_transient.py
git commit -m "fix(cache): list_orphans(deep=True) does not orphan on transient errors

Replaces 'except Exception:' with is_provider_not_found(exc) narrowing.
Without this, a VPN flap during a deep orphan check marks every cached
clip orphan, and the next 'Evict orphans' action wipes legitimately-
cached data.

Test seeds a clip and points the inspector at a provider that raises an
httpx 500 — clip stays non-orphan. Companion test confirms NotFoundError
still produces an orphan correctly.

Refs: T1-1, will be documented in ADR 0042.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: WorkspaceManager.prepare narrows exceptions (T1-1)

**Files:**
- Modify: `backend/app/services/workspace_manager.py:130-167`
- Create: `tests/integration/test_workspace_manager_transient.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_workspace_manager_transient.py`:

```python
"""WorkspaceManager.prepare must NOT lock a clip into permanent 'error'
state for transient provider failures. A VPN flap mid-prep would
otherwise leave the user with workspace clips that never recover."""

from pathlib import Path

import httpx
import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.workspaces import WorkspacesRepo
from backend.app.services.workspace_manager import WorkspaceManager

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


class _TransientProvider:
    """Raises transport errors on every get_clip; capabilities.media_is_local
    True so we only test the metadata branch."""

    class _Caps:
        media_is_local = True

    capabilities = _Caps()

    async def get_clip(self, pcid: str):
        request = httpx.Request("GET", "http://example/x")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("flaky", request=request, response=response)


class _NotFoundProvider:
    class _Caps:
        media_is_local = True

    capabilities = _Caps()

    async def get_clip(self, pcid: str):
        raise NotFoundError(f"{pcid} not found")


@pytest.mark.asyncio
async def test_prepare_marks_transient_error_not_permanent(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = WorkspacesRepo()
        ws_id = await repo.create(
            conn, name="ws", provider_id="catdv", catalog_id="1"
        )
        await repo.add_clips(conn, ws_id, [("catdv", "42")])

        mgr = WorkspaceManager(
            workspaces_repo=repo,
            provider=_TransientProvider(),
            proxy_resolver=None,
            db_provider=lambda: conn,
        )
        events = await mgr.prepare_all(ws_id)

        states = [ev.state for ev in events]
        assert "error" not in states, (
            f"transient transport error should NOT be terminal; got {states}"
        )
        # The state should be 'transient_error' so the user can retry.
        assert any(ev.state == "transient_error" for ev in events), states


@pytest.mark.asyncio
async def test_prepare_marks_permanent_error_for_not_found(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        repo = WorkspacesRepo()
        ws_id = await repo.create(
            conn, name="ws", provider_id="catdv", catalog_id="1"
        )
        await repo.add_clips(conn, ws_id, [("catdv", "42")])

        mgr = WorkspaceManager(
            workspaces_repo=repo,
            provider=_NotFoundProvider(),
            proxy_resolver=None,
            db_provider=lambda: conn,
        )
        events = await mgr.prepare_all(ws_id)
        assert any(ev.state == "error" for ev in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_workspace_manager_transient.py -v`

Expected: `test_prepare_marks_transient_error_not_permanent` FAILS — current code marks every exception as `error` (terminal).

- [ ] **Step 3: Narrow workspace_manager.prepare**

Modify `backend/app/services/workspace_manager.py:124-171`. Inside `_run`, replace the metadata try/except (lines 130-136) with:

```python
                # 1. metadata
                try:
                    await self._provider.get_clip(key[1])
                except BaseException as exc:
                    from backend.app.archive.errors import is_provider_not_found

                    if is_provider_not_found(exc):
                        await self._repo.set_cache_state(
                            db, ws_id, key, "error", error=f"metadata: {exc}"
                        )
                        yield PrepEvent(clip_key=key, state="error", error=str(exc))
                    else:
                        await self._repo.set_cache_state(
                            db, ws_id, key, "transient_error", error=f"metadata: {exc}"
                        )
                        yield PrepEvent(
                            clip_key=key, state="transient_error", error=str(exc)
                        )
                    continue
```

Do the same for the media branch around line 158-164.

If `WorkspacesRepo.set_cache_state` does not yet accept `'transient_error'`, add it to whatever validation it does (or remove the validation if it's just a string column — check `repositories/workspaces.py` for the constraint).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_workspace_manager_transient.py -v`

Expected: 2 passed.

Re-run workspace tests for regression: `.venv/bin/pytest tests/integration/test_workspace_manager.py tests/integration/test_workspaces_repo.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/workspace_manager.py backend/app/repositories/workspaces.py tests/integration/test_workspace_manager_transient.py
git commit -m "fix(workspace): prepare distinguishes transient from permanent errors

Replaces 'except Exception:' with is_provider_not_found(exc) narrowing
at both metadata and media branches of prepare(). Transient failures
(VPN flap, transport errors) now land the clip in a 'transient_error'
state that's retryable; only documented absence lands the clip in the
existing terminal 'error' state.

Refs: T1-1, ADR 0042.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: SyncEngine — unknown exceptions are retryable, with max_attempts ceiling (T1-2)

**Files:**
- Modify: `backend/app/settings.py` — add `sync_max_attempts`
- Modify: `backend/app/services/sync_engine.py:141-204`
- Create: `tests/integration/test_sync_engine_retryable_unknown.py`

- [ ] **Step 1: Add the settings field**

Modify `backend/app/settings.py`, in the `# sync engine` section (around line 60):

```python
    # sync engine
    sync_retry_base_s: int = 2
    sync_retry_max_s: int = 300
    sync_tick_interval_s: int = 5
    # Maximum attempts before a pending_op flips from 'pending' to 'failed'.
    # Prevents an infinitely-retried row from blocking the queue when the
    # underlying error never resolves. Default 10 ≈ ~17 min worst case at
    # default backoff (2,4,8,16,32,64,128,256,300,300 seconds).
    sync_max_attempts: int = 10
```

- [ ] **Step 2: Write the failing test**

Create `tests/integration/test_sync_engine_retryable_unknown.py`:

```python
"""sync_engine.tick must NOT mark a pending_op 'failed' for unknown
exceptions on the first attempt. Such errors are usually transient
(transport, adapter bug); permanent-fail wipes recoverable writes."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations
from backend.app.repositories.pending_operations import PendingOperationsRepo
from backend.app.repositories.write_log import WriteLogRepo
from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from backend.app.services.sync_engine import SyncEngine

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


def _make_monitor():
    monitor = AsyncMock(spec=ConnectionMonitor)
    monitor.current_state = lambda: ConnectionState.online
    return monitor


async def _seed_pending_op(conn) -> int:
    repo = PendingOperationsRepo()
    ids = await repo.insert_many(
        conn,
        rows=[{
            "provider_id": "catdv",
            "provider_clip_id": "42",
            "op_kind": "AddMarkers",
            "op_json": '{"kind": "AddMarkers", "markers": []}',
            "origin_annotation_id": None,
            "origin_review_item_ids": None,
            "expected_etag": None,
        }],
    )
    return ids[0]


@pytest.mark.asyncio
async def test_unknown_exception_marks_retryable_not_failed(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_pending_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=RuntimeError("never seen this"))

        engine = SyncEngine(
            provider=provider,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=_make_monitor(),
            db_provider=lambda: conn,
        )
        await engine.drain_once()

        repo = PendingOperationsRepo()
        row = await repo.get(conn, op_id)
        assert row["status"] == "pending", f"expected retryable; got {row['status']}"
        assert row["attempts"] == 1


@pytest.mark.asyncio
async def test_unknown_exception_eventually_fails_at_max_attempts(tmp_path):
    db_path = tmp_path / "test.db"
    async with open_db(db_path) as conn:
        await apply_migrations(conn, MIGRATIONS)
        op_id = await _seed_pending_op(conn)

        provider = AsyncMock()
        provider.id = "catdv"
        provider.apply_changes = AsyncMock(side_effect=RuntimeError("persistent"))

        engine = SyncEngine(
            provider=provider,
            pending_ops_repo=PendingOperationsRepo(),
            write_log_repo=WriteLogRepo(),
            connection_monitor=_make_monitor(),
            db_provider=lambda: conn,
            tick_interval_s=0.01,
            retry_base_s=0.001,
            retry_max_s=0.001,
        )
        # Ten drains, each separated by enough wall time to clear backoff.
        for _ in range(10):
            await engine.drain_once()

        repo = PendingOperationsRepo()
        row = await repo.get(conn, op_id)
        # After max_attempts (default 10), the row should be terminal-failed.
        assert row["status"] == "failed", row
        assert row["attempts"] >= 10
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_sync_engine_retryable_unknown.py -v`

Expected: `test_unknown_exception_marks_retryable_not_failed` FAILS with `status == 'failed'` — current sync_engine.py:202-204 marks unknown exceptions failed.

- [ ] **Step 4: Modify sync_engine to retry unknown exceptions with a ceiling**

In `backend/app/services/sync_engine.py:71-95` (`__init__`) add `max_attempts: int = 10`:

```python
        retry_base_s: float = 2.0,
        retry_max_s: float = 300.0,
        max_attempts: int = 10,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        # ... existing assignments ...
        self._max_attempts = max_attempts
```

Modify `_tick` around line 199-204 to replace:

```python
            except ProviderError as exc:
                await self._pending.mark_failed(db, op_ids, error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — unknown adapter bug
                await self._pending.mark_failed(db, op_ids, error=str(exc))
                continue
```

with:

```python
            except ProviderError as exc:
                await self._pending.mark_failed(db, op_ids, error=str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 — unknown adapter bug
                # Default to retryable: an unknown exception is most often
                # transient (transport bug, adapter glitch). The max-attempts
                # ceiling below prevents an infinitely-retried row from
                # blocking the queue. See ADR 0042.
                attempts_so_far = max(int(r.get("attempts") or 0) for r in rows) + 1
                if attempts_so_far >= self._max_attempts:
                    await self._pending.mark_failed(
                        db, op_ids,
                        error=f"{type(exc).__name__}: {exc} (max_attempts reached)",
                    )
                else:
                    await self._pending.mark_retryable(db, op_ids, error=str(exc))
                continue
```

And wire `max_attempts` from settings in `backend/app/context.py:_build_sync_subsystem` around line 416-426:

```python
    ctx.sync_engine = SyncEngine(
        provider=ctx.archive,
        pending_ops_repo=ctx.pending_ops_repo,
        write_log_repo=ctx.write_log_repo,
        connection_monitor=ctx.connection_monitor,
        db_provider=lambda c=ctx: c.db,
        event_bus=ctx.event_bus,
        tick_interval_s=float(settings.sync_tick_interval_s),
        retry_base_s=float(settings.sync_retry_base_s),
        retry_max_s=float(settings.sync_retry_max_s),
        max_attempts=int(settings.sync_max_attempts),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_sync_engine_retryable_unknown.py -v`

Expected: 2 passed.

Re-run sync engine tests for regression: `.venv/bin/pytest tests/integration/test_sync_engine.py -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/settings.py backend/app/services/sync_engine.py backend/app/context.py tests/integration/test_sync_engine_retryable_unknown.py
git commit -m "fix(sync): unknown exceptions are retryable, bounded by max_attempts

Previously the catchall in sync_engine._tick marked unknown exceptions
as terminal-failed on the first attempt, silently losing recoverable
writes. Now unknown exceptions go through mark_retryable until
sync_max_attempts (default 10), then flip to failed with a clear
'max_attempts reached' suffix in last_error.

Refs: T1-2, ADR 0042.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: humanise() helper (T1-3 foundation)

**Files:**
- Create: `backend/app/services/errors.py`
- Create: `tests/unit/test_humanise.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_humanise.py`:

```python
"""humanise(exc) produces actionable, non-empty error strings for the
common exception types that show up in user-facing surfaces (annotator
job errors, sync engine errors). Avoids the 'HTTPStatusError' bare-
class-name failure mode where str(exc) is empty."""

import httpx
import pytest

from backend.app.services.errors import humanise


def test_humanise_handles_httpx_status_error_with_body():
    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(
        500, request=request, text='{"error": "internal", "code": "EFOO"}'
    )
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    msg = humanise(exc)
    assert "500" in msg
    assert "EFOO" in msg or "internal" in msg
    assert msg != "HTTPStatusError"


def test_humanise_handles_httpx_status_error_with_empty_body():
    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(503, request=request, text="")
    exc = httpx.HTTPStatusError("503", request=request, response=response)
    msg = humanise(exc)
    assert "503" in msg


def test_humanise_handles_connect_error():
    exc = httpx.ConnectError("Connection refused")
    msg = humanise(exc)
    assert "refused" in msg.lower() or "connect" in msg.lower()
    assert msg != "ConnectError"


def test_humanise_handles_arbitrary_exception_with_str():
    exc = RuntimeError("specific failure mode")
    msg = humanise(exc)
    assert "specific failure mode" in msg


def test_humanise_handles_arbitrary_exception_without_str():
    class _Mute(Exception):
        def __str__(self) -> str:
            return ""

    exc = _Mute()
    msg = humanise(exc)
    # Falls through to type name so the user is not left with "".
    assert "_Mute" in msg


def test_humanise_truncates_giant_bodies():
    request = httpx.Request("POST", "http://example/x")
    body = "x" * 5000
    response = httpx.Response(500, request=request, text=body)
    exc = httpx.HTTPStatusError("500", request=request, response=response)
    msg = humanise(exc)
    assert len(msg) < 1000, f"got {len(msg)} chars; should be bounded"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_humanise.py -v`

Expected: `ImportError: cannot import name 'humanise' from 'backend.app.services.errors'`.

- [ ] **Step 3: Implement humanise**

Create `backend/app/services/errors.py`:

```python
"""User-facing error-string helpers.

`humanise(exc)` turns any exception into an actionable, non-empty string
suitable for showing the user (job error messages, toast text, etc.).
Required because `str(exc)` is empty or unhelpful for many SDK
exceptions (httpx.HTTPStatusError, google.api_core errors).
"""

from __future__ import annotations

import httpx

_MAX_BODY_CHARS = 400


def humanise(exc: BaseException) -> str:
    """Return an actionable, non-empty error string for the user.

    - httpx.HTTPStatusError: includes status code AND a truncated snippet
      of the response body.
    - httpx.ConnectError / TimeoutException / RequestError: a clear
      transport phrase.
    - other exceptions: str(exc) if non-empty, otherwise the class name.

    Always returns a non-empty string bounded to ~500 characters.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "").strip()
        if len(body) > _MAX_BODY_CHARS:
            body = body[:_MAX_BODY_CHARS] + "…(truncated)"
        return f"HTTP {exc.response.status_code} from {exc.request.url}: {body}" if body \
            else f"HTTP {exc.response.status_code} from {exc.request.url}"
    if isinstance(exc, httpx.TimeoutException):
        return f"transport timeout: {exc}"
    if isinstance(exc, httpx.ConnectError):
        return f"connect failed: {exc}"
    if isinstance(exc, httpx.RequestError):
        return f"transport error ({type(exc).__name__}): {exc}"
    s = str(exc).strip()
    return s if s else type(exc).__name__
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_humanise.py -v`

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/errors.py tests/unit/test_humanise.py
git commit -m "feat(services): add humanise() helper for user-facing error strings

Foundation for T1-3. Standard 'str(exc) or exc.__class__.__name__' is
unhelpful for SDK exceptions (httpx.HTTPStatusError str is empty;
google-cloud errors are similar). humanise() extracts status code +
truncated body for HTTP errors, named transport phrases for connect
/ timeout, falls through to str(exc) or type name otherwise.

Refs: T1-3.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: Wire humanise() into annotator error messages (T1-3)

**Files:**
- Modify: `backend/app/services/annotator.py:107-118`
- Modify: `tests/integration/test_annotator_worker.py` (extend an existing test)

- [ ] **Step 1: Write the failing assertion**

Open `tests/integration/test_annotator_worker.py` and add a new test at the bottom of the file:

```python
@pytest.mark.asyncio
async def test_job_error_message_includes_status_code_for_httpx_failure(tmp_path):
    """T1-3: Annotator-level error messages must carry actionable detail.

    Bare str(httpx.HTTPStatusError) is the empty string; without the
    humanise() wrapper the user sees only 'HTTPStatusError' with no
    status code or body — unactionable."""
    import httpx
    from backend.app.services import annotator as annotator_mod

    request = httpx.Request("POST", "http://example/x")
    response = httpx.Response(503, request=request, text='{"error": "EBUSY"}')
    exc = httpx.HTTPStatusError("503", request=request, response=response)

    # Use the same humanise the annotator uses, then assert the message
    # contains the status code and a body fragment.
    msg = annotator_mod._humanise_error(exc)
    assert "503" in msg
    assert "EBUSY" in msg
```

The test calls `_humanise_error` (a thin private wrapper that the annotator code will import from `services.errors.humanise`); the test fails today because that wrapper doesn't exist.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_annotator_worker.py::test_job_error_message_includes_status_code_for_httpx_failure -v`

Expected: `AttributeError: module 'backend.app.services.annotator' has no attribute '_humanise_error'`.

- [ ] **Step 3: Modify the annotator to use humanise()**

In `backend/app/services/annotator.py`, near the existing imports:

```python
from backend.app.services.errors import humanise as _humanise_error
```

Replace the error-message construction at line 114 (currently `msg = str(exc) or exc.__class__.__name__`) with:

```python
            msg = _humanise_error(exc)
```

Same replacement should apply to the inner studio-run error branches at lines 128-129 (the `studio_runs_repo.complete_error(db, run_id, error=msg)` already uses `msg`, so no change needed there — they pick up the new humanised text automatically).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_annotator_worker.py::test_job_error_message_includes_status_code_for_httpx_failure -v`

Expected: 1 passed.

Re-run the full annotator test file: `.venv/bin/pytest tests/integration/test_annotator_worker.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/annotator.py tests/integration/test_annotator_worker.py
git commit -m "fix(annotator): humanise job error messages

Job errors now route through services.errors.humanise() instead of
str(exc) or exc.__class__.__name__. Users see 'HTTP 503 from <url>:
{...EBUSY...}' instead of bare 'HTTPStatusError'.

Refs: T1-3.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 8: GEMINI_API_KEY browser-exposure WARNING + README (T1-4)

**Files:**
- Modify: `backend/app/startup.py` — add `warn_browser_secret_exposure(settings)`
- Modify: `backend/app/main.py` — call it in `lifespan`
- Modify: `README.md` — add "Security caveats" section
- Create: `tests/integration/test_lifespan_api_key_warning.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_lifespan_api_key_warning.py`:

```python
"""Startup logs a WARNING when GEMINI_API_KEY is configured, because the
key is shipped to the browser by live_sessions.mint_ephemeral_token.
Surfaces an accepted risk (see ADR 0043) that today is documented only
in a code comment."""

import logging
from unittest.mock import MagicMock

import pytest

from backend.app.startup import warn_browser_secret_exposure


def test_warning_fires_when_gemini_api_key_set(caplog):
    settings = MagicMock()
    settings.gemini_api_key = "AIza-redacted"
    caplog.set_level(logging.WARNING)
    warn_browser_secret_exposure(settings)
    relevant = [r for r in caplog.records if "GEMINI_API_KEY" in r.message]
    assert relevant, f"no GEMINI_API_KEY warning in records: {caplog.records}"
    assert relevant[0].levelno == logging.WARNING


def test_no_warning_when_gemini_api_key_unset(caplog):
    settings = MagicMock()
    settings.gemini_api_key = None
    caplog.set_level(logging.WARNING)
    warn_browser_secret_exposure(settings)
    relevant = [r for r in caplog.records if "GEMINI_API_KEY" in r.message]
    assert not relevant
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_lifespan_api_key_warning.py -v`

Expected: `ImportError: cannot import name 'warn_browser_secret_exposure'`.

- [ ] **Step 3: Add the warning function**

Append to `backend/app/startup.py`:

```python
import logging


def warn_browser_secret_exposure(settings) -> None:
    """Log a WARNING when GEMINI_API_KEY is configured.

    live_sessions.mint_ephemeral_token returns the raw key to the
    browser because the ephemeral-token flow (authTokens.create) closes
    the WSS handshake with code 1007 'API key not valid' the moment the
    client sends `setup`. The accepted threat model is single-operator
    local app over VPN — see ADR 0043. This log line ensures the
    exposure is visible to the operator on every boot, not just to
    someone reading the code comment in live_sessions.py.
    """
    if getattr(settings, "gemini_api_key", None):
        logging.getLogger(__name__).warning(
            "GEMINI_API_KEY is configured; the raw key will be exposed to the "
            "browser during Live sessions. This is accepted under the "
            "single-operator local + VPN threat model — see ADR 0043. If your "
            "deployment falls outside that model, unset GEMINI_API_KEY."
        )
```

- [ ] **Step 4: Wire it into the lifespan**

Modify `backend/app/main.py:53-58` (`lifespan`):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = Settings()
    from backend.app.startup import warn_browser_secret_exposure
    warn_browser_secret_exposure(settings)
    init_external = settings.app_env == "prod" or _real_external_enabled(settings)
    # ... rest unchanged
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_lifespan_api_key_warning.py -v`

Expected: 2 passed.

- [ ] **Step 6: Add Security caveats to README**

Open `README.md`. Add a new section before the existing "Architecture & orientation" section:

```markdown
## Security caveats

This is a local app with a deliberately narrow threat model: **single
operator, on the operator's own laptop, behind the project VPN**.

- `GEMINI_API_KEY`, when configured, is **shipped to the browser** by
  the Live-session flow. Real ephemeral-token auth was attempted
  (`authTokens.create`) but Google closes the WSS handshake with code
  1007 "API key not valid" the moment the client sends `setup` — see
  ADR 0043. Until that's resolved upstream, treat the key as
  browser-readable. Do not deploy this app on a shared host, behind a
  public network, or under any model where browser dev-tools access by
  an untrusted user is a concern.
- The boot log emits a `WARNING` naming this exposure every time the
  key is configured, so the operator sees it on every start.
- CatDV credentials (`CATDV_PASSWORD`) live in `.env` and are read
  server-side only; they are NOT exposed to the browser.

If you need to relax these constraints, the live-session auth flow has
to be redesigned. That is a separate project, not a config change.
```

- [ ] **Step 7: Commit**

```bash
git add backend/app/startup.py backend/app/main.py README.md tests/integration/test_lifespan_api_key_warning.py
git commit -m "fix(startup): log WARNING for GEMINI_API_KEY browser exposure

Currently the fact that live_sessions.mint_ephemeral_token returns the
raw key to the browser is documented only in an in-code comment. This
adds (a) a boot-time WARNING that fires whenever the key is set, and
(b) a README Security caveats section naming the threat model and
pointing at ADR 0043.

Refs: T1-4, ADR 0043 (next commit).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 9: ADR 0042 — narrow provider errors

**Files:**
- Create: `docs/adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md`:

```markdown
# 0042. Narrow provider errors — never treat exceptions as "not found"

**Date:** 2026-05-30
**Status:** Accepted

## Context

Two code paths in the codebase historically did `except Exception:` and
treated any caught exception as documented evidence that the upstream
clip was absent:

- `CacheInspector.list_orphans(deep=True)` —
  `backend/app/services/cache_inspector.py:322` (pre-tier-1).
- `WorkspaceManager.prepare` —
  `backend/app/services/workspace_manager.py:132, :160` (pre-tier-1).

`sync_engine._tick`
(`backend/app/services/sync_engine.py:202`) made an adjacent mistake:
it caught arbitrary `Exception` and called `mark_failed`, treating
unknown errors as permanent.

These patterns turn transient failures (VPN flap, transport blip,
seat-cap reached, adapter bug) into permanent side effects (orphan
marking, terminal "error" workspace state, dropped pending writes).
The next action the user takes on those records — "Evict orphans",
re-running prepare, expecting a write to land — silently destroys
recoverable state.

## Alternatives

1. **Status quo: `except Exception:` everywhere.** Cheap; relies on
   the user understanding that "orphan" might mean "transient" — they
   won't.
2. **Per-call retry loops.** Hide transience by retrying inline.
   Doesn't compose with backoff already implemented elsewhere; makes
   each call site responsible for its own retry policy.
3. **Narrow the exception type at the boundary** (chosen). Introduce
   `NotFoundError(ProviderError)`. Adapters raise it for documented
   absence; a helper `is_provider_not_found(exc)` recognises it (plus
   `httpx.HTTPStatusError(404)` for direct httpx-using paths). Every
   "evidence of absence" decision routes through the helper. Unknown
   exceptions remain unknown — they go to retryable, not terminal.

## Decision

- New exception `backend.app.archive.errors.NotFoundError(ProviderError)`.
- New helper `backend.app.archive.errors.is_provider_not_found(exc) -> bool`.
  Returns True iff `exc` is a `NotFoundError` or an
  `httpx.HTTPStatusError` with status 404; False otherwise.
- CatDV adapter translates upstream `NOT_FOUND` envelopes to
  `NotFoundError` at the boundary.
- `CacheInspector.list_orphans(deep=True)` uses the helper. Non-
  NotFound exceptions are silently skipped (the next deep call will
  retry).
- `WorkspaceManager.prepare` uses the helper. Transient failures land
  the clip in a new `cache_state='transient_error'` (retryable);
  documented absence lands the clip in the existing `'error'`
  (terminal).
- `SyncEngine._tick`'s `except Exception:` defaults to
  `mark_retryable` and bumps `attempts`; only at
  `settings.sync_max_attempts` does it flip to `mark_failed` with a
  `(max_attempts reached)` suffix.

## Consequences

- **Positive:** transient errors no longer destroy recoverable state.
  The type system carries the "evidence of absence" semantics, not
  ad-hoc try/except in every call site.
- **Negative:** adapters must remember to raise `NotFoundError` at
  their NOT_FOUND boundary. A grep test could be added if drift
  appears (out of scope for tier 1).
- **Forward-looking:** the same pattern applies to any future
  adapter / external system the codebase grows. Document and reuse.
```

- [ ] **Step 2: Update the decisions index**

Open `docs/decisions.md`. Add an entry for 0042 in the index table (follow the existing format — one row per ADR, ordered by number).

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0042-narrow-provider-errors-never-treat-exceptions-as-not-found.md docs/decisions.md
git commit -m "docs(adr): 0042 — narrow provider errors, never treat exceptions as NotFound

Documents the anti-pattern collapsed by T1-1 and T1-2: 'except Exception:'
being treated as evidence of absence. New NotFoundError + helper now
carry the meaning at the type level.

Refs: T1-1, T1-2.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 10: ADR 0043 — Gemini Live API key browser exposure (accepted risk)

**Files:**
- Create: `docs/adr/0043-gemini-live-api-key-exposure-accepted-risk.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0043-gemini-live-api-key-exposure-accepted-risk.md`:

```markdown
# 0043. Gemini Live API key browser exposure — accepted risk

**Date:** 2026-05-30
**Status:** Accepted

## Context

`backend/app/services/live_sessions.py::mint_ephemeral_token` returns
the raw `settings.gemini_api_key` to the browser, where it is used to
authenticate the WSS handshake to Gemini Live.

The intended design was real ephemeral-token auth via
`https://generativelanguage.googleapis.com/v1alpha/auth_tokens`. The
WSS handshake opens with the ephemeral token, but the moment the
client sends its `setup` frame, the server closes the connection with
code 1007 "API key not valid" — verified empirically across multiple
attempts. The most likely cause is a binding mismatch between the
`setup` bound at mint time and the `setup` sent over WSS, but the
issue is reproducible against Google's documented example.

## Alternatives

1. **Keep grinding on the ephemeral-token flow.** Each attempt has
   cost a day or more of head-scratching with no progress. Continuing
   under the project's deadline pressure is not justified.
2. **Proxy WSS through the backend.** Backend opens the WSS connection
   to Gemini using its server-side credential, browser opens a WSS
   connection to the backend, backend forwards audio frames in both
   directions. Significant work; doubles latency-sensitive audio
   bytes through the Python process; non-trivial backpressure
   handling.
3. **Ship the raw key, narrow the threat model, document loudly**
   (chosen). The app is a single-operator local tool used on the
   operator's own laptop behind a project VPN. The browser is the
   operator's; the network is the operator's. Under that model the
   exposure is acceptable — but it is fragile to any model change.

## Decision

- `mint_ephemeral_token` returns `settings.gemini_api_key` directly.
- A boot-time WARNING (in `backend/app/startup.py::
  warn_browser_secret_exposure`) fires whenever the key is configured.
- `README.md` has a "Security caveats" section naming the exposure
  and the threat model.
- Any deployment outside the single-operator-on-VPN model triggers a
  redesign — proxying via the backend (Alternative 2) is the most
  likely path.

## Consequences

- **Positive:** Live audio works today, against today's Gemini Live
  surface, within the documented threat model. The exposure is
  auditable (boot log + README + this ADR) rather than buried in a
  code comment.
- **Negative:** the risk is real and any change to the threat model
  (multi-user, public network, untrusted browser session) means the
  app cannot ship until the auth flow is rebuilt. A future operator
  who doesn't read this ADR could deploy under the wrong model
  without realising.
- **Forward-looking:** if Google fixes the WSS / `setup` binding for
  ephemeral tokens, swap them in. Otherwise Alternative 2 is the
  fallback when constraints change.
```

- [ ] **Step 2: Update the decisions index**

Open `docs/decisions.md`. Add an entry for 0043.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0043-gemini-live-api-key-exposure-accepted-risk.md docs/decisions.md
git commit -m "docs(adr): 0043 — Gemini Live API key browser exposure (accepted risk)

Records the accepted risk that mint_ephemeral_token returns the raw
GEMINI_API_KEY to the browser, why ephemeral-token auth couldn't be
made to work, and the threat-model constraints under which this
remains acceptable.

Refs: T1-4.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 11: Migration 0011 sentinel + gap check (T1-5)

**Files:**
- Create: `backend/migrations/0011_REVERTED.txt`
- Modify: `backend/app/migrations_runner.py`
- Create: `tests/integration/test_migrations_gap_check.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_migrations_gap_check.py`:

```python
"""apply_migrations refuses to apply a .sql file whose numeric prefix
collides with a .txt sentinel — this catches the future-PR-claiming-
0011 case. Sentinels document deliberately-reserved numbers."""

from pathlib import Path

import pytest

from backend.app.db import open_db
from backend.app.migrations_runner import apply_migrations


@pytest.mark.asyncio
async def test_collision_with_sentinel_raises(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migs = tmp_path / "migs"
    migs.mkdir()
    (migs / "0001_init.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migs / "0011_REVERTED.txt").write_text(
        "0011 was reverted; do not reuse this number."
    )
    (migs / "0011_thing.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

    async with open_db(db_path) as conn:
        with pytest.raises(RuntimeError, match="0011"):
            await apply_migrations(conn, migs)


@pytest.mark.asyncio
async def test_sentinel_without_collision_is_ignored(tmp_path: Path):
    db_path = tmp_path / "test.db"
    migs = tmp_path / "migs"
    migs.mkdir()
    (migs / "0001_init.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migs / "0011_REVERTED.txt").write_text("reserved")
    (migs / "0012_thing.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

    async with open_db(db_path) as conn:
        applied = await apply_migrations(conn, migs)
        assert applied == ["0001_init.sql", "0012_thing.sql"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_migrations_gap_check.py -v`

Expected: `test_collision_with_sentinel_raises` FAILS — current runner happily applies both `.sql` files.

- [ ] **Step 3: Add the sentinel file**

Create `backend/migrations/0011_REVERTED.txt`:

```
0011 was reverted via commit 1065546 ("revert: PR #9 Prompt Studio").

The number is NOT reused. Replacement migrations went forward:
- 0012_prompt_media_kind.sql (PR #8)
- 0013_studio.sql (PR #10)

This .txt sentinel exists so apply_migrations() refuses any future
0011_*.sql file — dev DBs that ran main during the brief window
PR #9 was live still have '0011_studio.sql' in their
schema_migrations table, so reusing the number would create
inconsistent histories across environments.

See ADR 0044 for the full reasoning.
```

- [ ] **Step 4: Modify the runner to enforce the sentinel rule**

Replace `backend/app/migrations_runner.py` with:

```python
"""Simple SQL migrations runner — applies `*.sql` files under a directory
in lexical order, tracking applied names in `schema_migrations`.

Refuses to apply a `.sql` file whose numeric prefix collides with a
`.txt` sentinel in the same directory. Sentinels mark numbers that
were used and reverted (see ADR 0044, sentinel `0011_REVERTED.txt`).
"""

import logging
import re
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_NUM_PREFIX = re.compile(r"^(\d+)_")


def _num_prefix(name: str) -> str | None:
    m = _NUM_PREFIX.match(name)
    return m.group(1) if m else None


async def apply_migrations(conn: aiosqlite.Connection, migrations_dir: Path) -> list[str]:
    """Apply any *.sql files under migrations_dir not already in schema_migrations.

    Refuses to apply a `.sql` file whose numeric prefix matches a `.txt`
    sentinel in the same directory.

    Also warns (does NOT fail) about entries in `schema_migrations` whose
    source files are no longer on disk — surfaces the dev-DB state from
    the PR #9 revert.

    Returns the names that were applied this run.
    """
    await conn.execute(META_TABLE_SQL)
    await conn.commit()

    # Build the sentinel set.
    sentinel_nums = {
        _num_prefix(p.name)
        for p in migrations_dir.glob("*.txt")
        if _num_prefix(p.name) is not None
    }

    # Detect collisions before applying anything.
    for path in sorted(migrations_dir.glob("*.sql")):
        num = _num_prefix(path.name)
        if num is not None and num in sentinel_nums:
            raise RuntimeError(
                f"migration {path.name} collides with reserved number {num} "
                f"(sentinel exists at {num}_REVERTED.txt or similar). "
                f"Use the next available number instead. See ADR 0044."
            )

    cur = await conn.execute("SELECT name FROM schema_migrations")
    applied = {row[0] for row in await cur.fetchall()}

    sql_files = sorted(p for p in migrations_dir.glob("*.sql"))
    sql_file_names = {p.name for p in sql_files}

    # Warn about orphan entries (file deleted but row remains).
    for name in applied - sql_file_names:
        log.warning(
            "schema_migrations contains %s but no matching file on disk; "
            "this is expected for reverted migrations (e.g. 0011_studio.sql). "
            "If unexpected, investigate.",
            name,
        )

    newly_applied: list[str] = []
    for path in sql_files:
        if path.name in applied:
            continue
        sql = path.read_text()
        await conn.executescript(sql)
        await conn.execute("INSERT INTO schema_migrations(name) VALUES (?)", (path.name,))
        await conn.commit()
        newly_applied.append(path.name)
    return newly_applied
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_migrations_gap_check.py tests/integration/test_migrations.py -v`

Expected: 4 passed (2 new + 2 existing).

Also re-run the migration-specific suites for regression:
`.venv/bin/pytest tests/integration/test_migration_0002.py tests/integration/test_migration_0003.py tests/integration/test_migration_0004.py tests/integration/test_migration_0005.py tests/integration/test_migration_0006.py tests/integration/test_migration_0007.py tests/integration/test_migration_0009.py tests/integration/test_migration_0012.py tests/integration/test_migration_0014.py -q`.

- [ ] **Step 6: Commit**

```bash
git add backend/migrations/0011_REVERTED.txt backend/app/migrations_runner.py tests/integration/test_migrations_gap_check.py
git commit -m "fix(migrations): sentinel-aware gap check; document 0011 reversal

Adds backend/migrations/0011_REVERTED.txt sentinel, and updates
apply_migrations() to refuse any .sql file whose numeric prefix
collides with a .txt sentinel. Catches the future-PR-claiming-0011
case where a new 0011 would apply cleanly on fresh installs but
collide with dev DBs that ran main during PR #9.

Also warns (does not fail) about schema_migrations rows whose source
files no longer exist — surfaces the existing 0011_studio.sql entry
on dev DBs without breaking boot.

Refs: T1-5, ADR 0044 (next commit).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 12: ADR 0044 — migration numbering and the 0011 gap

**Files:**
- Create: `docs/adr/0044-migration-numbering-and-the-0011-gap.md`

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0044-migration-numbering-and-the-0011-gap.md`:

```markdown
# 0044. Migration numbering and the 0011 gap

**Date:** 2026-05-30
**Status:** Accepted

## Context

`backend/migrations/` skips from `0010_live_sessions.sql` to
`0012_prompt_media_kind.sql`. The history:

- Commit `b55c8b0` added `0011_studio.sql` on a feature branch.
- Commit `e37b71a` independently added `0011_prompt_media_kind.sql` on
  a different branch (number collision).
- The studio version won the merge race when PR #9 landed on `main`
  (commit `8a9b2bb`).
- Three days later, PR #9 was reverted wholesale (commit `1065546`)
  because the implementation didn't match the intended design.
- Replacements went forward, not back: PR #8 used `0012_prompt_media_kind.sql`,
  PR #10 used `0013_studio.sql`. **Choosing 0012/0013 instead of
  reusing 0011 was correct** — it kept the historical sequence stable
  for any dev machine that had run main during the brief window PR #9
  was live.

The dev machine at `data/app.db` still has `0011_studio.sql` in
`schema_migrations` from that window. Fresh installs see no 0011
file on disk and skip the number cleanly.

## Alternatives

1. **Renumber forward, remove the gap.** Breaks dev DBs that have
   `0011_studio.sql` recorded — they'd re-apply the renumbered
   migrations because the names changed.
2. **Leave the gap as documented in this ADR only.** Future
   contributors might "fix" the gap, or might claim 0011 for a new
   migration without knowing the history.
3. **Sentinel file + runner-level enforcement** (chosen). A
   `0011_REVERTED.txt` file marks the number as reserved.
   `apply_migrations()` raises if any `.sql` file's numeric prefix
   collides with a sentinel. The runner also warns about
   `schema_migrations` entries whose source files are missing,
   surfacing the dev-DB state without breaking boot.

## Decision

- Migration files use a four-digit numeric prefix (`NNNN_<slug>.sql`).
- Reserved or reverted numbers get a `.txt` sentinel (e.g.
  `0011_REVERTED.txt`) documenting why the number is off-limits.
- `apply_migrations()` raises if any `.sql` file's numeric prefix
  matches a sentinel.
- `apply_migrations()` warns (does not fail) about
  `schema_migrations` rows whose source files no longer exist.
- New migrations claim the next unused number, not the lowest unused
  number.

## Consequences

- **Positive:** the 0011 reservation is now enforced by the runner,
  not by convention alone. A future PR claiming 0011 fails loudly
  with a clear error pointing at this ADR.
- **Negative:** the sentinel pattern adds a small amount of folklore
  every contributor must understand. The runner warning on dev DBs
  is informational noise (one line per orphaned entry per boot)
  until the orphan tables are cleaned up — which is out of scope for
  tier 1 (see the spec's open question).
- **Forward-looking:** the same sentinel pattern handles any future
  reverted migration. The ADR also documents that adopting a date-
  prefixed naming scheme (e.g.
  `20260601_0001_<slug>.sql`) or switching to Alembic remains the
  long-term option if number-collision pain returns.
```

- [ ] **Step 2: Update the decisions index**

Add the 0044 entry to `docs/decisions.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0044-migration-numbering-and-the-0011-gap.md docs/decisions.md
git commit -m "docs(adr): 0044 — migration numbering and the 0011 gap

Records the history of the 0011 reversal, why renumbering forward
would break dev DBs, the sentinel-file pattern that enforces the
reservation, and the runner-level warning for orphan
schema_migrations entries.

Refs: T1-5.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 13: CatDV query string sanitisation — allowlist (T1-6)

**Files:**
- Modify: `backend/app/services/catdv_client.py:113-139` (`list_clips`)
- Create: `tests/unit/test_catdv_query_sanitisation.py`

CatDV's documented query language is parenthesised triples joined with
`and`/`or` (see the comment at catdv_client.py:117-123). The server's
exact escape rules are not documented in the public REST reference. The
safest approach for tier 1 is the allowlist: characters outside the
permitted set are removed, leaving query semantics intact.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_catdv_query_sanitisation.py`:

```python
"""list_clips's user-search input must not be able to escape its embedding
in the CatDV query expression. Today's q.replace('(', '').replace(')', '')
only handles parens; quotes, the keywords 'and'/'or', backslashes are not
handled.

We use an allowlist: only alphanumerics, space, hyphen, underscore, and
dot pass. Anything else is stripped. The CatDV server then sees a search
fragment that cannot escape the (clip.name)contains(...) wrapper."""

import pytest

from backend.app.services.catdv_client import _sanitise_query


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("plain text", "plain text"),
        ("plain (with parens)", "plain with parens"),
        ("quote\"injection", "quoteinjection"),
        ("backslash\\here", "backslashhere"),
        ("and-or-keyword", "and-or-keyword"),
        (") or (1)eq(1", " or 1eq1"),
        ("hyphen-and_underscore.dot 123", "hyphen-and_underscore.dot 123"),
        ("", ""),
    ],
)
def test_sanitise_query(raw: str, expected: str):
    assert _sanitise_query(raw) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_catdv_query_sanitisation.py -v`

Expected: `ImportError: cannot import name '_sanitise_query'`.

- [ ] **Step 3: Implement the allowlist sanitiser and use it**

In `backend/app/services/catdv_client.py`, near the top of the file (after the imports):

```python
import re

_QUERY_ALLOWLIST = re.compile(r"[^\w\s\-.]", re.UNICODE)


def _sanitise_query(q: str) -> str:
    """Strip any character not in the conservative allowlist
    (alphanumeric, whitespace, hyphen, underscore, dot).

    The CatDV REST query language is parenthesised triples joined with
    `and`/`or`. The undocumented escape rules make per-character escaping
    unreliable, so we instead remove anything that could let user input
    escape its embedding in `(clip.name)contains(<here>)`.
    """
    return _QUERY_ALLOWLIST.sub("", q)
```

Replace the existing `sanitised = q.replace("(", "").replace(")", "")` line in `list_clips` (around line 126) with:

```python
            sanitised = _sanitise_query(q)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_catdv_query_sanitisation.py tests/integration/test_catdv_client_list.py -v`

Expected: all pass (no regression in the existing client-list test).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/unit/test_catdv_query_sanitisation.py
git commit -m "fix(catdv): replace q.replace() sanitisation with allowlist

Previously list_clips only stripped parens, leaving quotes, backslashes,
and the keywords and/or to flow into the CatDV query expression. New
_sanitise_query() keeps only alphanumeric / space / hyphen / underscore
/ dot — anything that could escape the (clip.name)contains(<here>)
wrapper is removed.

Refs: T1-6.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 14: CatDV health probe — no reauth (T1-7)

**Files:**
- Modify: `backend/app/services/catdv_client.py` — `_call_json(reauth=True)` param + `health()` uses False
- Create: `tests/integration/test_catdv_client_health_no_reauth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_catdv_client_health_no_reauth.py`:

```python
"""health() must NOT trigger a re-login on AUTH envelope. Otherwise the
probe itself can take the CatDV seat that the probe was looking for —
the very thing CLAUDE.md's 'CatDV session discipline' warns about."""

import pytest

from backend.app.services.catdv_client import CatdvAuthError, CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_health_raises_authentication_required_without_relogin():
    with running_fake_catdv() as (base_url, fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()
            # Track login call count after the initial login.
            login_calls_before = fake.login_call_count
            # Force AUTH envelope on the next request.
            import time
            fake.force_auth_until = time.time() + 60

            with pytest.raises(CatdvAuthError):
                await client.health()

            # The health probe must NOT have triggered a re-login.
            assert fake.login_call_count == login_calls_before, (
                f"login_calls before={login_calls_before} after={fake.login_call_count}; "
                "health() should not relogin"
            )
```

If `tests/fakes/fake_catdv.py` does not yet expose `login_call_count`,
add a simple counter to its `login` handler.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_health_no_reauth.py -v`

Expected: FAIL — current `health()` goes through `_call_json` which re-logs in on AUTH.

- [ ] **Step 3: Add reauth parameter and a no-reauth call path**

In `backend/app/services/catdv_client.py`, modify `_call_json` (around line 98):

```python
    async def _call_json(self, method: str, path: str, *, json: Any = None, reauth: bool = True) -> Envelope:
        """Issue a JSON request. Re-login once on AUTH (unless reauth=False); raise on ERROR."""
        url = f"{self._base}{path}"
        resp = await self.http.request(method, url, json=json)
        env = Envelope.model_validate(resp.json())
        if env.requires_reauth:
            if not reauth:
                raise CatdvAuthError(env.error_message or "not authenticated")
            await self.login()
            resp = await self.http.request(method, url, json=json)
            env = Envelope.model_validate(resp.json())
        if env.is_busy:
            raise CatdvBusyError(env.error_message or "CatDV session limit reached")
        if not env.is_ok:
            raise CatdvError(env.error_message or "CatDV ERROR")
        return env
```

Modify `health()` (around line 252) to pass `reauth=False`:

```python
    async def health(self) -> dict[str, Any]:
        """Cheap reachability probe. Returns the envelope `data` payload
        (which may be {}) on OK; raises CatdvAuthError without re-login
        on missing session; raises CatdvError/CatdvBusyError otherwise.

        See ADR-style note in this module: a re-login here would itself
        take the seat the probe is looking for. The connection monitor
        treats any raise as 'offline', so propagating CatdvAuthError is
        the right behaviour — Reconnect button triggers a login when the
        user is ready.
        """
        env = await self._call_json("GET", "/catdv/api/info", reauth=False)
        return env.data or {}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_health_no_reauth.py -v`

Expected: 1 passed.

Re-run the rest of the CatDV client test suite for regression:
`.venv/bin/pytest tests/integration/test_catdv_client_*.py -q`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/fakes/fake_catdv.py tests/integration/test_catdv_client_health_no_reauth.py
git commit -m "fix(catdv): health probe must not re-login (no seat consumption)

Adds reauth=False to _call_json; health() uses it. Previously the
probe would silently re-login on an AUTH envelope, taking the very
seat the probe was checking for — directly contradicting CLAUDE.md's
'CatDV session discipline' section. Now the probe raises CatdvAuthError
without a second HTTP call; ConnectionMonitor's broad catch marks the
state offline, and the user can hit Reconnect when they're ready to
spend a seat.

Refs: T1-7.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 15: Tier-1 close-out — CLAUDE.md discipline section + decisions index sweep

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Add the discipline section to CLAUDE.md**

Open `CLAUDE.md`. Add a new top-level section between the existing
"Cache management" section and "Shell Environment" section:

```markdown
## Error handling discipline

Two helpers exist; route through them.

### Narrowing provider errors

`backend/app/archive/errors.py::is_provider_not_found(exc) -> bool` is
the **only** way to decide "this clip is gone" from a caught exception.
Recognises `NotFoundError` (the explicit type adapters raise for
documented absence) and `httpx.HTTPStatusError(404)`. Anything else is
transient by definition — treat as "try later", never as evidence of
absence.

Bare `except Exception:` is allowed only in event-loop watchdog code
(e.g. `sync_engine._loop`). Anywhere a caller might infer absence,
narrow with `is_provider_not_found(exc)`. Anywhere a caller has to mark
a record terminal (failed, error, orphan), get explicit evidence — do
not assume.

The `sync_engine._tick` catchall defaults to `mark_retryable` and
honours `settings.sync_max_attempts` before flipping to `mark_failed`.
Adding a new external-system caller? Mirror the same shape.

See ADR 0042 for the full rationale.

### User-facing error strings

`backend/app/services/errors.py::humanise(exc) -> str` produces an
actionable, non-empty string for any exception. Used by `annotator`
job error messages today; **all new user-facing surfaces should use it
instead of `str(exc) or exc.__class__.__name__`** — the latter
silently returns `'HTTPStatusError'` for the most common SDK failures.
```

- [ ] **Step 2: Verify the decisions index includes all three ADRs**

Open `docs/decisions.md`. Confirm rows exist for 0042, 0043, 0044 (added in Tasks 9, 10, 12). If any are missing, add them now.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/decisions.md
git commit -m "docs: CLAUDE.md tier-1 close-out — error handling discipline

Adds a top-level 'Error handling discipline' section pointing at
is_provider_not_found and humanise, plus the rule on bare
'except Exception:'. References ADR 0042. Also verifies decisions.md
index has 0042, 0043, 0044.

Refs: tier 1 close-out per docs/specs/2026-05-30-fix-prioritization-design.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 16: Final regression sweep + open the PR

**Files:** none (verification + PR creation)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -q`

Expected: all green. If anything fails, fix in place — do not open the
PR with a failing suite.

- [ ] **Step 2: Run the lint-imports check**

Run: `.venv/bin/lint-imports`

Expected: success. Tier 1 should not have changed any import boundaries.

- [ ] **Step 3: Compute the scorecard**

Run from the worktree root:

```bash
git diff --shortstat main...HEAD
git log --oneline main...HEAD | wc -l
```

Record: lines added vs removed; commit count. For tier 1 the expectation
is roughly net-zero lines (small helpers added, ugly try/except blocks
shrunk) and ~14-15 commits.

Record new shared primitives count = 2 (`is_provider_not_found`,
`humanise`). Duplications killed = 3 (orphan-style anti-pattern in
inspector + workspace + sync engine collapsed onto one helper).

- [ ] **Step 4: Invoke `superpowers:requesting-code-review`**

Trigger the project's code review skill against the worktree before
opening the PR. Address any findings in additional commits on the same
branch.

- [ ] **Step 5: Open the PR via `superpowers:finishing-a-development-branch`**

The skill will run `gh pr create` against `main` with a body that
includes the scorecard from Step 3. PR title:

```
fix(tier-1): data loss and silent failures
```

PR body template:

```markdown
## Summary

Tier 1 of the fix-prioritization plan
(docs/specs/2026-05-30-fix-prioritization-design.md). Closes seven
data-loss / silent-failure issues; installs guardrails so the
anti-patterns cannot be reintroduced without CI catching it.

### Fixes
- T1-1: Provider exceptions are not "not found" (cache inspector + workspace prep) — ADR 0042
- T1-2: Sync engine: unknown exceptions retryable, bounded by max_attempts — ADR 0042
- T1-3: Humanise user-facing job error messages
- T1-4: Make GEMINI_API_KEY browser exposure auditable — ADR 0043
- T1-5: Migration 0011 sentinel + gap check — ADR 0044
- T1-6: CatDV query string allowlist sanitisation
- T1-7: CatDV health probe must not eat a seat

### Scorecard
- Lines added/removed: <fill from Step 3>
- New shared primitives: 2 (`is_provider_not_found`, `humanise`)
- Duplications killed: 3 (inspector / workspace / sync-engine anti-pattern collapsed)
- New ADRs: 3 (0042, 0043, 0044)
- CLAUDE.md additions: 1 section (Error handling discipline)

## Test plan
- [ ] `.venv/bin/pytest -q` — full suite green
- [ ] `.venv/bin/lint-imports` — green
- [ ] Manual acceptance flows from docs/specs/2026-05-30-fix-prioritization-design.md § "Tier 1 acceptance" (flows 1-7)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

---

## Self-Review

**Spec coverage:** Each of the seven tier-1 fixes from the spec
(T1-1 through T1-7) has at least one task. T1-1 spans Tasks 1-4 (helper
+ adapter wiring + inspector + workspace), T1-2 is Task 5, T1-3 is
Tasks 6-7, T1-4 is Task 8, T1-5 is Tasks 11-12, T1-6 is Task 13, T1-7
is Task 14. The tier close-out (CLAUDE.md + decisions index) is Task 15.
The PR-opening sequence is Task 16.

**Placeholder scan:** No "TBD", "TODO (later)", or vague "add error
handling" steps. Every code step has actual code. Every command has the
actual command and the expected outcome.

**Type consistency:** `NotFoundError`, `is_provider_not_found`, and
`humanise` are referenced consistently across tasks. `cache_state` adds
the new value `'transient_error'` in Task 4; the existing
`WorkspacesRepo.set_cache_state` is referenced by name (Task 4 Step 3
asks the executor to inspect the repo and adjust validation if any).
`sync_max_attempts` setting is added in Task 5 and consumed in
`context._build_sync_subsystem` the same step.

**One residual investigation point** in Task 4 Step 3: the executor is
asked to inspect `repositories/workspaces.py` to determine whether
`set_cache_state` validates the state string. This is a deliberate
lookup rather than a placeholder — the alternative (mechanically
modifying the repo without checking) risks breaking other state-
transition tests. The step gives explicit instructions for both
branches (validation exists → extend the allowlist; no validation → no
change needed).

---

**Plan complete and saved to `docs/plans/tier-1-data-loss.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Each task ships with its own commit; tier-1 PR opens automatically after Task 16.

**2. Inline Execution** — Execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints for review.

**Which approach?**
