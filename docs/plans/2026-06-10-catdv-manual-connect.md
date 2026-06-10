# CatDV manual connect/disconnect + connection indicator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CatDV connectivity manual and on-demand so the always-on Cloud Run instance holds a CatDV license seat only between an explicit Connect and Disconnect (or an idle timeout), with a single connection indicator that shows Connected / Disconnected / Unreachable.

**Architecture:** Reuse the existing `ConnectionMonitor`, connection routes, SSE/events plumbing, and the connection pill. Add a `manual` boot mode that builds the `CatdvClient` but defers `login()`; the seat truth comes from the client's `logged_in` flag while a seat-free `/api/info` probe supplies reachability. Two new endpoints (`connect`/`disconnect`) drive `login()`/`logout()`; an `IdleDisconnector` background task frees a forgotten seat.

**Tech Stack:** Python 3.13, FastAPI, pydantic-settings, httpx (async), Jinja2 + HTMX, Alpine.js, pytest/pytest-asyncio. Run Python via `.venv/bin/python`. Both gates green per task: `.venv/bin/python -m pytest` and `.venv/bin/lint-imports`.

**Spec:** `docs/specs/2026-06-10-catdv-manual-connect-design.md`

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/settings.py` | runtime config | add `catdv_connect_mode`, `catdv_idle_logout_s` |
| `backend/app/archive/provider.py` | `ProviderHealth` shape | add `reachable: bool = True` |
| `backend/app/archive/providers/catdv/adapter.py` | provider health | set `reachable` on each return |
| `backend/app/services/connection_monitor.py` | state machine | add `disconnected` state, `manual`+`logged_in`, probe mapping, non-halting loop |
| `backend/app/services/catdv_client.py` | CatDV session | `logged_in`/`last_activity` props, `track_activity` stamp |
| `backend/app/services/idle_disconnector.py` | idle seat release | **new** background task |
| `backend/app/context.py` | composition root | manual boot (defer login), monitor wiring, idle task, `LiveCtx.idle_disconnector` |
| `backend/app/main.py` | lifespan + `/api/health` | start idle task; map `disconnected` mode |
| `backend/app/routes/connection.py` | HTTP surface | `connect`/`disconnect` endpoints; `_mode()` adds `disconnected` |
| `backend/app/routes/ui.py` | pill partial | pass `connect_mode` to the pill |
| `backend/app/templates/connection_pill.html` | indicator UI | 4 states + Connect/Disconnect + `hx-indicator` |
| `backend/app/templates/_connection_chip.html` | topbar chip | read-only `disconnected`/`unreachable` labels in manual mode |
| `backend/app/static/toast.js` | toast store | `HX-Trigger: {"toast": …}` bridge |

---

### Task 1: Settings — connect mode + idle timeout

**Files:**
- Modify: `backend/app/settings.py:58-65` (the "connection monitor" block)
- Test: `tests/unit/test_settings_manual_connect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings_manual_connect.py
"""Manual-connect settings: default to manual (the seat-safe Cloud Run
model) and bound idle auto-disconnect. See the manual-connect spec."""

from backend.app.settings import Settings

_REQUIRED = {
    "CATDV_BASE_URL": "http://127.0.0.1:18080",
    "CATDV_CATALOG_ID": "881507",
    "GCP_PROJECT_ID": "catdav",
    "GCS_BUCKET_NAME": "catdav-proxies",
}


def _env(monkeypatch, tmp_path, **extra):
    monkeypatch.chdir(tmp_path)  # no .env in cwd
    for key, value in {**_REQUIRED, **extra}.items():
        monkeypatch.setenv(key, value)


def test_connect_mode_defaults_manual(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("CATDV_CONNECT_MODE", raising=False)
    assert Settings().catdv_connect_mode == "manual"


def test_connect_mode_overridable_to_auto(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path, CATDV_CONNECT_MODE="auto")
    assert Settings().catdv_connect_mode == "auto"


def test_idle_logout_defaults_900_overridable(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.delenv("CATDV_IDLE_LOGOUT_S", raising=False)
    assert Settings().catdv_idle_logout_s == 900
    monkeypatch.setenv("CATDV_IDLE_LOGOUT_S", "60")
    assert Settings().catdv_idle_logout_s == 60
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_manual_connect.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'catdv_connect_mode'`.

- [ ] **Step 3: Add the settings**

In `backend/app/settings.py`, inside the `# connection monitor` block (after `catdv_startup_login_timeout_s`, around line 65), add:

```python
    # CatDV connection lifecycle. "manual" (default): build the client but
    # do NOT log in at boot — the operator clicks Connect to spend a seat
    # and Disconnect to release it (the Cloud Run instance is always-on, so
    # auto-login would hold a seat 24/7). "auto": log in at startup (legacy
    # behavior, for local dev). CATDV_OFFLINE=true still wins (no client).
    catdv_connect_mode: Literal["auto", "manual"] = "manual"
    # Auto-disconnect (logout, freeing the seat) after this many seconds
    # with no operator-driven CatDV API call. The 5s pill poll and the
    # background health probe do NOT count as activity.
    catdv_idle_logout_s: int = 900
```

`Literal` is already imported at `settings.py:6`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_manual_connect.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_manual_connect.py
git commit -m "Settings: catdv_connect_mode (default manual) + catdv_idle_logout_s"
```

---

### Task 2: `ProviderHealth.reachable` + catdv adapter sets it

**Files:**
- Modify: `backend/app/archive/provider.py:32-36`
- Modify: `backend/app/archive/providers/catdv/adapter.py:82-109`
- Test: `tests/unit/test_catdv_adapter_health_reachable.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_catdv_adapter_health_reachable.py
"""adapter.health() reports `reachable` so the monitor can tell
'tunnel up, logged out' (reachable) from 'tunnel down' (raised). See spec."""

import pytest

from backend.app.archive.providers.catdv.adapter import CatdvArchiveProvider
from backend.app.services.catdv_client import (
    CatdvAuthError,
    CatdvBusyError,
    CatdvError,
)


class _Client:
    def __init__(self, exc):
        self._exc = exc

    async def health(self):
        if self._exc is not None:
            raise self._exc
        return {}


def _provider(client):
    p = CatdvArchiveProvider.__new__(CatdvArchiveProvider)
    p._client = client
    p._is_online_provider = lambda: True
    return p


@pytest.mark.asyncio
async def test_auth_envelope_is_reachable():
    h = await _provider(_Client(CatdvAuthError("no session"))).health()
    assert h.ok is False and h.reachable is True


@pytest.mark.asyncio
async def test_busy_is_reachable():
    h = await _provider(_Client(CatdvBusyError("max sessions"))).health()
    assert h.ok is False and h.reachable is True


@pytest.mark.asyncio
async def test_generic_error_is_not_reachable():
    h = await _provider(_Client(CatdvError("bad base url"))).health()
    assert h.ok is False and h.reachable is False


@pytest.mark.asyncio
async def test_absent_client_is_not_reachable():
    h = await _provider(None).health()
    assert h.ok is False and h.reachable is False


@pytest.mark.asyncio
async def test_ok_is_reachable():
    h = await _provider(_Client(None)).health()
    assert h.ok is True and h.reachable is True
```

> Note: if `CatdvArchiveProvider.__new__` + attribute assignment doesn't
> match the real constructor's private names, open `adapter.py` and use the
> exact field names (`self._client`, `self._is_online_provider`) shown at
> `adapter.py:78,90-94`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_adapter_health_reachable.py -v`
Expected: FAIL — `AttributeError: 'ProviderHealth' object has no attribute 'reachable'`.

- [ ] **Step 3: Add the field and set it**

In `backend/app/archive/provider.py`, change the `ProviderHealth` dataclass (lines 32-36) to:

```python
@dataclass(frozen=True)
class ProviderHealth:
    ok: bool
    latency_ms: float | None = None
    detail: str | None = None
    # True when the provider answered at all (even an error envelope);
    # False when it could not be reached (transport) or has no client.
    # The connection monitor maps reachable-but-not-ok → "disconnected".
    reachable: bool = True
```

In `backend/app/archive/providers/catdv/adapter.py` (the `health()` body, lines 90-107), set `reachable` on each return:

```python
        if self._client is None:
            return ProviderHealth(ok=False, reachable=False, detail="offline")
        t0 = perf_counter()
        try:
            await self._client.health()
        except CatdvAuthError as exc:
            return ProviderHealth(ok=False, reachable=True, detail=f"auth: {exc}")
        except CatdvBusyError as exc:
            return ProviderHealth(ok=False, reachable=True, detail=f"busy: {exc}")
        except CatdvError as exc:
            return ProviderHealth(ok=False, reachable=False, detail=str(exc))
        latency_ms = (perf_counter() - t0) * 1000.0
        return ProviderHealth(ok=True, latency_ms=latency_ms)
```

(The `ok=True` return keeps `reachable`'s default of `True`. A transport
error from `self._client.health()` is **not** caught here — it propagates
and the monitor treats the raise as unreachable.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_adapter_health_reachable.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/archive/provider.py backend/app/archive/providers/catdv/adapter.py tests/unit/test_catdv_adapter_health_reachable.py
git commit -m "ProviderHealth.reachable: distinguish logged-out vs unreachable"
```

---

### Task 3: `ConnectionState.disconnected` + monitor manual mode

**Files:**
- Modify: `backend/app/services/connection_monitor.py` (enum line 18-22; `__init__` 30-54; `probe_once` 78-103; `_loop` 144-156)
- Test: `tests/integration/test_connection_monitor_manual.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_connection_monitor_manual.py
"""Manual-mode monitor: seat truth from logged_in(), reachability from the
probe; the loop keeps probing (no halt) so the indicator tracks the tunnel."""

import asyncio

import pytest

from backend.app.services.connection_monitor import (
    ConnectionMonitor,
    ConnectionState,
)
from backend.app.services.events import EventBus


class _Health:
    def __init__(self, ok, reachable):
        self.ok = ok
        self.reachable = reachable


class StubProvider:
    """health() returns a _Health, or raises to simulate an unreachable tunnel."""

    def __init__(self, *, ok=True, reachable=True, raises=False):
        self.ok = ok
        self.reachable = reachable
        self.raises = raises
        self.calls = 0

    async def health(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError("connect error")
        return _Health(self.ok, self.reachable)


def _monitor(db, provider, *, logged_in):
    return ConnectionMonitor(
        provider=provider,
        db_provider=lambda: db,
        interval_s=0.05,
        timeout_s=0.5,
        event_bus=EventBus(),
        manual=True,
        logged_in=lambda: logged_in[0],
        initial_state=ConnectionState.disconnected,
    )


@pytest.mark.asyncio
async def test_logged_in_and_ok_is_online(db):
    logged_in = [True]
    m = _monitor(db, StubProvider(ok=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.online


@pytest.mark.asyncio
async def test_reachable_but_logged_out_is_disconnected(db):
    logged_in = [False]
    # ok=True from a public /api/info must NOT read as online when logged out
    m = _monitor(db, StubProvider(ok=True, reachable=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.disconnected


@pytest.mark.asyncio
async def test_unreachable_is_offline(db):
    logged_in = [False]
    m = _monitor(db, StubProvider(raises=True), logged_in=logged_in)
    assert await m.probe_once() == ConnectionState.offline


@pytest.mark.asyncio
async def test_manual_loop_does_not_halt_on_non_online(db):
    logged_in = [False]
    provider = StubProvider(ok=False, reachable=True)  # disconnected
    m = _monitor(db, provider, logged_in=logged_in)
    await m.start()
    await asyncio.sleep(0.25)
    await m.stop()
    assert provider.calls >= 3  # kept probing despite being non-online
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_monitor_manual.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'manual'` (and `ConnectionState.disconnected` missing).

- [ ] **Step 3: Implement**

In `backend/app/services/connection_monitor.py`:

(a) Add to the enum (after line 21):

```python
class ConnectionState(StrEnum):
    online = "online"
    degraded = "degraded"
    offline = "offline"
    syncing = "syncing"
    disconnected = "disconnected"
```

(b) Extend `__init__` signature and body. Add the two params (after `initial_state`, around line 40) and store them:

```python
        forced_offline: bool = False,
        initial_state: ConnectionState = ConnectionState.online,
        manual: bool = False,
        logged_in: "Callable[[], bool] | None" = None,
    ) -> None:
        ...
        self._manual = manual
        self._logged_in = logged_in or (lambda: True)
```

(Insert the two `self._...` lines next to the other assignments, e.g. just
after `self._forced_offline = forced_offline` at line 50. `Callable` is
already imported at line 10.)

(c) Replace the `else:` arm of `probe_once` (lines 92-98) with the
seat-aware mapping:

```python
        else:
            ok = getattr(health, "ok", True) if health is not None else True
            reachable = getattr(health, "reachable", True) if health is not None else True
            if ok and self._logged_in():
                new_state = ConnectionState.online
            elif self._manual and (ok or reachable):
                new_state = ConnectionState.disconnected
                detail = "reachable; not logged in"
            elif ok:
                new_state = ConnectionState.online
            else:
                new_state = ConnectionState.offline
                detail = getattr(health, "detail", None) or "health probe not ok"
```

(The `elif ok:` arm preserves legacy non-manual behavior: with the default
`logged_in` of `lambda: True`, `ok` always lands on `online`, so existing
auto-mode tests are unchanged.)

(d) In `_loop` (lines 150-152), only halt when **not** manual:

```python
            if not self._manual and state != ConnectionState.online:
                # auto mode: halt — user must explicitly retry_now() to resume
                return
```

- [ ] **Step 4: Run tests to verify they pass (new + regression)**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_monitor_manual.py tests/integration/test_connection_monitor_halt_and_retry.py -v`
Expected: PASS (4 new + 5 existing). The existing halt/retry tests must stay green (they use `manual=False`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/connection_monitor.py tests/integration/test_connection_monitor_manual.py
git commit -m "ConnectionMonitor: manual mode + disconnected state (seat from logged_in)"
```

---

### Task 4: `CatdvClient` — `logged_in`/`last_activity` + activity stamp

**Files:**
- Modify: `backend/app/services/catdv_client.py` (`__init__` 51-60; `login` 81-92; `_call_json` 116-131; `_call_json_with_params` 161-175; `health` 282; add properties)
- Test: `tests/unit/test_catdv_client_activity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_catdv_client_activity.py
"""last_activity is stamped by real API calls but NOT by the health probe,
so idle auto-disconnect can't be starved by the 30s background probe."""

import httpx
import pytest

from backend.app.services.catdv_client import CatdvClient


def _envelope_ok():
    return {"status": "OK", "data": {}}


@pytest.mark.asyncio
async def test_logged_in_property_reflects_login(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    async with client:
        assert client.logged_in is False

        async def fake_post(url, **kw):
            return httpx.Response(200, json={"status": "OK"}, request=httpx.Request("POST", url))

        monkeypatch.setattr(client.http, "post", fake_post)
        await client.login()
        assert client.logged_in is True


@pytest.mark.asyncio
async def test_real_call_stamps_activity_but_health_does_not(monkeypatch):
    client = CatdvClient("http://example.invalid", "u", "p")
    async with client:
        client._logged_in = True
        client._last_activity = 0.0

        async def fake_request(method, url, **kw):
            return httpx.Response(200, json=_envelope_ok(), request=httpx.Request(method, url))

        monkeypatch.setattr(client.http, "request", fake_request)

        # health() must not stamp activity
        await client.health()
        assert client.last_activity == 0.0

        # a real call must stamp it
        await client.get_clip(1)
        assert client.last_activity > 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_client_activity.py -v`
Expected: FAIL — `AttributeError: 'CatdvClient' object has no attribute 'logged_in'`.

- [ ] **Step 3: Implement**

In `backend/app/services/catdv_client.py`:

(a) Add `import time` near the top imports (after `import re`, line 8).

(b) In `__init__` (after line 60 `self._logged_in = False`):

```python
        self._last_activity: float = 0.0
```

(c) Add two properties (just after the `http` property, around line 79):

```python
    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def last_activity(self) -> float:
        """Monotonic timestamp of the last operator-driven API call (0.0 if
        none yet). The health probe deliberately does not update this."""
        return self._last_activity
```

(d) In `login()` (line 92), also stamp activity on success:

```python
            self._logged_in = True
            self._last_activity = time.monotonic()
```

(e) Give `_call_json` a `track_activity` flag and stamp on success.
Change the signature (line 116) and add the stamp before `return env`:

```python
    async def _call_json(
        self, method: str, path: str, *, json: Any = None, reauth: bool = True,
        track_activity: bool = True,
    ) -> Envelope:
```
and immediately before `return env` (line 131):
```python
        if track_activity:
            self._last_activity = time.monotonic()
        return env
```

(f) Same for `_call_json_with_params` (signature line 161, stamp before
`return env` at line 175):

```python
    async def _call_json_with_params(
        self, method: str, path: str, *, params: dict[str, str] | None = None,
        track_activity: bool = True,
    ) -> Envelope:
```
```python
        if track_activity:
            self._last_activity = time.monotonic()
        return env
```

(g) `health()` (line 282) opts out of the stamp:

```python
        env = await self._call_json("GET", "/catdv/api/info", reauth=False, track_activity=False)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_catdv_client_activity.py tests/unit/test_catdv_logout_timeout.py -v`
Expected: PASS (2 new + 1 existing logout test still green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catdv_client.py tests/unit/test_catdv_client_activity.py
git commit -m "CatdvClient: logged_in/last_activity + activity stamp (health excluded)"
```

---

### Task 5: `IdleDisconnector` service

**Files:**
- Create: `backend/app/services/idle_disconnector.py`
- Test: `tests/unit/test_idle_disconnector.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_idle_disconnector.py
"""IdleDisconnector logs out (freeing the seat) and re-probes once the
client has been idle past the threshold; activity within it is a no-op."""

import pytest

from backend.app.services.idle_disconnector import IdleDisconnector


class FakeClient:
    def __init__(self, *, logged_in, last_activity):
        self._logged_in = logged_in
        self._last_activity = last_activity
        self.logout_calls = 0

    @property
    def logged_in(self):
        return self._logged_in

    @property
    def last_activity(self):
        return self._last_activity

    async def logout(self):
        self.logout_calls += 1
        self._logged_in = False


class FakeMonitor:
    def __init__(self):
        self.probes = 0

    async def probe_once(self):
        self.probes += 1


def _now(value):
    return lambda: value


@pytest.mark.asyncio
async def test_idle_past_threshold_disconnects():
    client = FakeClient(logged_in=True, last_activity=0.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=900, clock=_now(901.0)
    )
    assert await idle.check_once() is True
    assert client.logout_calls == 1
    assert monitor.probes == 1


@pytest.mark.asyncio
async def test_recent_activity_is_noop():
    client = FakeClient(logged_in=True, last_activity=500.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=900, clock=_now(900.0)
    )
    assert await idle.check_once() is False
    assert client.logout_calls == 0


@pytest.mark.asyncio
async def test_not_logged_in_is_noop():
    client = FakeClient(logged_in=False, last_activity=0.0)
    monitor = FakeMonitor()
    idle = IdleDisconnector(
        client=client, monitor=monitor, idle_timeout_s=1, clock=_now(9999.0)
    )
    assert await idle.check_once() is False
    assert client.logout_calls == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_idle_disconnector.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.services.idle_disconnector'`.

- [ ] **Step 3: Create the service**

```python
# backend/app/services/idle_disconnector.py
"""Releases the CatDV seat after a period of operator inactivity.

The Cloud Run instance is always-on, so a forgotten Connect would hold a
seat indefinitely. This task logs out (DELETE /session) and re-probes the
monitor (→ "disconnected") once last_activity is older than the threshold.
Activity is operator-driven CatDV calls only; the health probe and the
pill poll do not reset it (see CatdvClient.last_activity)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any


class IdleDisconnector:
    def __init__(
        self,
        *,
        client: Any,
        monitor: Any,
        idle_timeout_s: float,
        check_interval_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._monitor = monitor
        self._idle = float(idle_timeout_s)
        self._interval = float(check_interval_s)
        self._clock = clock
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def check_once(self) -> bool:
        """One idle check. Returns True iff it disconnected this call."""
        if not self._client.logged_in:
            return False
        if self._clock() - self._client.last_activity <= self._idle:
            return False
        await self._client.logout()
        await self._monitor.probe_once()
        return True

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except TimeoutError:
            self._task.cancel()
        finally:
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self.check_once()
            except Exception:  # noqa: BLE001 — watchdog loop must not die
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except TimeoutError:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_idle_disconnector.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/idle_disconnector.py tests/unit/test_idle_disconnector.py
git commit -m "IdleDisconnector: release the CatDV seat after inactivity"
```

---

### Task 6: Composition — defer login in manual mode + wire monitor & idle task

**Files:**
- Modify: `backend/app/context.py` (`_OnlineFlags` 365-374; `_build_archive_subsystem` 469-471, 491-552, 657; `LiveCtx` 219-222 + `aclose` 349-362; `_build_sync_subsystem` 676-687)
- Modify: `backend/app/main.py` lifespan (74-79)
- Test: `tests/integration/test_context_manual_boot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_context_manual_boot.py
"""Manual mode builds the CatdvClient but must NOT log in at boot; auto mode
preserves the legacy startup login. We assert on login attempts via a stub."""

import importlib

import pytest


def _setenv(monkeypatch, tmp_path, connect_mode):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "u")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CATDV_CONNECT_MODE", connect_mode)


@pytest.mark.asyncio
async def test_manual_mode_does_not_login_at_boot(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path, "manual")
    login_calls = {"n": 0}

    from backend.app.services import catdv_client as cc

    async def fake_login(self):
        login_calls["n"] += 1
        self._logged_in = True

    monkeypatch.setattr(cc.CatdvClient, "login", fake_login)

    from backend.app import context as ctx_mod

    importlib.reload(ctx_mod)
    from backend.app.settings import Settings

    core, live = await ctx_mod.build_context(Settings(), init_external=True)
    try:
        assert live is not None
        assert live.catdv is not None       # client built
        assert live.catdv.logged_in is False
        assert login_calls["n"] == 0        # but NOT logged in
        from backend.app.services.connection_monitor import ConnectionState

        assert live.connection_monitor.current_state() == ConnectionState.disconnected
        assert live.idle_disconnector is not None
    finally:
        await (live or core).aclose()
```

> If `build_context`'s import path differs, confirm with
> `grep -n "async def build_context" backend/app/context.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_context_manual_boot.py -v`
Expected: FAIL — either `login_calls["n"] == 1` (still logs in) or `AttributeError: ... 'idle_disconnector'`.

- [ ] **Step 3: Implement the composition changes**

(a) `_OnlineFlags` (context.py:365-374) — add a `manual` field:

```python
class _OnlineFlags(NamedTuple):
    forced_offline: bool
    login_failed: bool
    manual: bool = False
```

(b) `_build_archive_subsystem` — read the mode and gate the startup login.
After line 470 (`forced_offline = ...`) add:

```python
    connect_mode = getattr(settings, "catdv_connect_mode", "manual")
    manual = use_catdv and not forced_offline and connect_mode == "manual"
```

Wrap the startup-login block (the whole `try: await asyncio.wait_for(catdv.login(), ...)` … `login_failed = True` at lines 509-552) so it runs only in auto mode. Change the line just after `await catdv.__aenter__()` (line 497) and the comment block to:

```python
        await catdv.__aenter__()
        if connect_mode == "auto":
            # Auto mode: force one login round-trip so an unreachable host
            # or bad credentials degrade us to offline cleanly at startup.
            try:
                await asyncio.wait_for(
                    catdv.login(),
                    timeout=settings.catdv_startup_login_timeout_s,
                )
            except CatdvAuthError as exc:
                ...  # unchanged body
            except CatdvBusyError as exc:
                ...  # unchanged body
            except Exception as exc:  # noqa: BLE001
                ...  # unchanged body
        # Manual mode: client is built but stays logged out until the
        # operator clicks Connect (POST /api/connection/connect).
```

(Keep the three `except` bodies exactly as they are at lines 514-552, just
indented one level deeper under `if connect_mode == "auto":`.)

(c) The flags return (line 657):

```python
        flags=_OnlineFlags(
            forced_offline=forced_offline, login_failed=login_failed, manual=manual
        ),
```

(d) `LiveCtx` — add the field (after `media_prefetcher`, line 222):

```python
    idle_disconnector: "IdleDisconnector | None" = None
```

Add the import at the top of `context.py` with the other service imports:

```python
from backend.app.services.idle_disconnector import IdleDisconnector
```

(e) `LiveCtx.aclose` — stop the idle task **before** logging out (insert
between `sync_engine.stop()` at line 357 and `connection_monitor.stop()`):

```python
        if self.idle_disconnector is not None:
            await self.idle_disconnector.stop()
```

(f) `_build_sync_subsystem` — pass `manual`, `logged_in`, the right
`initial_state`, and build the idle task. Replace the `ConnectionMonitor(...)`
construction (lines 676-684) with:

```python
    from backend.app.services.connection_monitor import ConnectionState

    if flags.manual:
        initial_state = ConnectionState.disconnected
    elif flags.login_failed:
        initial_state = ConnectionState.offline
    else:
        initial_state = ConnectionState.online

    connection_monitor = ConnectionMonitor(
        provider=arch.archive,
        db_provider=lambda: core.db,
        interval_s=float(settings.health_probe_interval_s),
        timeout_s=float(settings.health_probe_timeout_s),
        event_bus=core.event_bus,
        forced_offline=flags.forced_offline,
        initial_state=initial_state,
        manual=flags.manual,
        logged_in=(lambda: arch.catdv.logged_in) if arch.catdv is not None else None,
    )
```

Then, just before the `LiveCtx(...)` assembly at the end of
`_build_sync_subsystem`, build the idle task (only in manual mode with a
client):

```python
    idle_disconnector = None
    if flags.manual and arch.catdv is not None:
        from backend.app.services.idle_disconnector import IdleDisconnector

        idle_disconnector = IdleDisconnector(
            client=arch.catdv,
            monitor=connection_monitor,
            idle_timeout_s=float(settings.catdv_idle_logout_s),
        )
```

and pass `idle_disconnector=idle_disconnector` into the `LiveCtx(...)`
constructor call.

> Find the `LiveCtx(` constructor call in `_build_sync_subsystem` with
> `grep -n "LiveCtx(" backend/app/context.py` and add the kwarg there.

(g) `main.py` lifespan — start the idle task with the others (after
`connection_monitor.start()`, line 75):

```python
        if live.idle_disconnector is not None:
            await live.idle_disconnector.start()
```

- [ ] **Step 4: Run tests (new + regressions)**

Run: `.venv/bin/python -m pytest tests/integration/test_context_manual_boot.py tests/integration/test_context_boot_recovery.py tests/unit/test_aclose_ordering.py -v`
Expected: PASS. If `test_aclose_ordering` pins an exact teardown sequence, update it to include the idle-disconnector stop in its expected order.

- [ ] **Step 5: Full suite + lint**

Run: `.venv/bin/python -m pytest && .venv/bin/lint-imports`
Expected: all green. (`auto` mode is now opt-in; tests that relied on
default auto-login may need `CATDV_CONNECT_MODE=auto` in their env — fix
any that fail by adding it, since manual is the new default.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py backend/app/main.py tests/integration/test_context_manual_boot.py
git commit -m "Composition: defer CatDV login in manual mode; wire idle disconnector"
```

---

### Task 7: `connect` / `disconnect` endpoints + mode mapping

**Files:**
- Modify: `backend/app/routes/connection.py` (`_mode` 25-32; add two routes)
- Modify: `backend/app/main.py` `/api/health` (155-165)
- Test: `tests/integration/test_routes_connect_disconnect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_routes_connect_disconnect.py
"""connect → login() + online; disconnect → logout() + disconnected;
login failures map to status codes + an HX-Trigger toast, never a seat."""

import importlib

import pytest
from fastapi.testclient import TestClient

from backend.app.services.catdv_client import CatdvBusyError
from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from tests._helpers.live_ctx import install_live_ctx


def _make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class FakeClient:
    def __init__(self, *, login_exc=None):
        self._logged_in = False
        self._login_exc = login_exc

    @property
    def logged_in(self):
        return self._logged_in

    async def login(self):
        if self._login_exc is not None:
            raise self._login_exc
        self._logged_in = True

    async def logout(self):
        self._logged_in = False


def _install(app, client):
    ctx = app.state.core_ctx

    class P:
        async def health(self):
            class H:
                ok = client.logged_in
                reachable = True
            return H()

    monitor = ConnectionMonitor(
        provider=P(), db_provider=lambda: ctx.db, interval_s=99999.0,
        event_bus=ctx.event_bus, manual=True, logged_in=lambda: client.logged_in,
        initial_state=ConnectionState.disconnected,
    )
    install_live_ctx(app, connection_monitor=monitor, catdv=client)
    return monitor


def test_connect_success_goes_online(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 200
        assert c.logged_in is True
        assert client.get("/api/connection/state").json()["state"] == "online"


def test_connect_busy_maps_409_and_no_seat(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient(login_exc=CatdvBusyError("max sessions"))
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 409
        assert c.logged_in is False
        assert "HX-Trigger" in r.headers  # toast bridge


def test_disconnect_frees_seat(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        c._logged_in = True
        _install(client.app, c)
        r = client.post("/api/connection/disconnect")
        assert r.status_code == 200
        assert c.logged_in is False
        assert client.get("/api/connection/state").json()["state"] == "disconnected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_connect_disconnect.py -v`
Expected: FAIL — 404 (routes not defined).

- [ ] **Step 3: Implement the endpoints + mode mapping**

In `backend/app/routes/connection.py`:

(a) Extend `_mode()` (lines 25-32) to surface `disconnected`:

```python
def _mode(monitor) -> str:
    if monitor is None:
        return "online"
    if getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        return "forced_offline"
    from backend.app.services.connection_monitor import ConnectionState

    state = monitor.current_state()
    if state == ConnectionState.online:
        return "online"
    if state == ConnectionState.disconnected:
        return "disconnected"
    return "offline"
```

(b) Add the imports near the top of the file (after the existing
`from backend.app.deps import get_core_ctx` at line 18):

```python
import json as _json

from backend.app.deps import get_live_ctx
from backend.app.services.catdv_client import CatdvAuthError, CatdvBusyError
from backend.app.services.errors import humanise
```

(c) Add the two routes (after `set_online`, before `stream_events`):

```python
def _toast_header(message: str, level: str = "error") -> dict[str, str]:
    return {"HX-Trigger": _json.dumps({"toast": {"message": message, "level": level}})}


@router.post("/connect")
async def connect(request: Request):
    live = get_live_ctx(request)  # 503 if fully offline
    monitor = _monitor(request)
    if live.catdv is None:
        raise HTTPException(status_code=409, detail="CatDV not configured")
    try:
        await live.catdv.login()
    except CatdvBusyError as exc:
        return _pill_or_json(request, monitor, status_code=409,
                             headers=_toast_header(f"CatDV seat busy: {humanise(exc)}"))
    except CatdvAuthError as exc:
        return _pill_or_json(request, monitor, status_code=401,
                             headers=_toast_header(f"CatDV login rejected: {humanise(exc)}"))
    except Exception as exc:  # noqa: BLE001 — transport / unreachable
        return _pill_or_json(request, monitor, status_code=502,
                             headers=_toast_header(f"CatDV unreachable: {humanise(exc)}"))
    if monitor is not None:
        await monitor.probe_once()
    return _pill_or_json(request, monitor)


@router.post("/disconnect")
async def disconnect(request: Request):
    live = get_live_ctx(request)
    monitor = _monitor(request)
    if live.catdv is not None:
        await live.catdv.logout()
    if monitor is not None:
        await monitor.probe_once()
    return _pill_or_json(request, monitor)
```

(d) Add the small response helper used above (put it next to `_monitor`):

```python
def _pill_or_json(request: Request, monitor, *, status_code: int = 200,
                  headers: dict[str, str] | None = None):
    if request.headers.get("HX-Request") == "true":
        from backend.app.routes.ui import _pill_context

        return _templates.TemplateResponse(
            request, "connection_pill.html", _pill_context(request),
            status_code=status_code, headers=headers,
        )
    body = {"state": str(monitor.current_state().value) if monitor else "online",
            "mode": _mode(monitor)}
    from fastapi.responses import JSONResponse

    return JSONResponse(body, status_code=status_code, headers=headers)
```

(`_pill_context` is added in Task 9; until then the non-HTMX JSON branch is
what the tests above exercise — they send no `HX-Request` header, so the
JSON branch runs and `HX-Trigger` is still attached.)

In `backend/app/main.py` `/api/health` (lines 159-164), map the new state:

```python
    if monitor is None:
        mode = "online"
    elif getattr(monitor, "is_forced", False) or getattr(monitor, "_forced_offline", False):
        mode = "forced_offline"
    elif monitor.current_state() == ConnectionState.online:
        mode = "online"
    elif monitor.current_state() == ConnectionState.disconnected:
        mode = "disconnected"
    else:
        mode = "offline"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_connect_disconnect.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint + commit**

Run: `.venv/bin/lint-imports` (routes must not import `httpx`; we import
only `catdv_client` symbols and `errors.humanise`, which is fine).

```bash
git add backend/app/routes/connection.py backend/app/main.py tests/integration/test_routes_connect_disconnect.py
git commit -m "Routes: /api/connection/connect + /disconnect; disconnected mode"
```

---

### Task 8: `toast.js` HX-Trigger → toast bridge

**Files:**
- Modify: `backend/app/static/toast.js` (end of the `alpine:init` handler)
- Test: `tests/unit/test_toast_htmx_bridge.py` (source-level guard — there is no JS test runner here)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_toast_htmx_bridge.py
"""toast.js must bridge HTMX HX-Trigger 'toast' events into the store so
server responses (e.g. failed Connect) surface as toasts."""

from pathlib import Path

SRC = Path("backend/app/static/toast.js").read_text()


def test_listens_for_htmx_toast_event():
    assert "toast" in SRC
    # the bridge listens on the documented HTMX custom-event name
    assert "addEventListener('toast'" in SRC or 'addEventListener("toast"' in SRC


def test_bridge_pushes_into_store():
    assert "Alpine.store('toast').push" in SRC
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_toast_htmx_bridge.py -v`
Expected: FAIL on `test_listens_for_htmx_toast_event` (no listener yet).

- [ ] **Step 3: Add the bridge**

At the end of `backend/app/static/toast.js`, after the `alpine:init`
handler block, add:

```javascript
/* HTMX bridge: a response carrying `HX-Trigger: {"toast": {...}}` fires a
 * DOM event named "toast" with the payload in event.detail. Forward it to
 * the Alpine store so server-driven actions (e.g. failed Connect) toast
 * without bespoke per-button JS. */
document.body.addEventListener('toast', (e) => {
  const d = e.detail || {};
  if (d && d.message) {
    Alpine.store('toast').push(d.message, { level: d.level || 'info' });
  }
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_toast_htmx_bridge.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/static/toast.js tests/unit/test_toast_htmx_bridge.py
git commit -m "toast.js: bridge HTMX HX-Trigger toast events into the store"
```

---

### Task 9: Connection pill — 4 states + Connect/Disconnect

**Files:**
- Modify: `backend/app/templates/connection_pill.html`
- Modify: `backend/app/routes/ui.py:18-31` (extract `_pill_context`, pass `mode`/`connect_mode`)
- Test: `tests/integration/test_connection_pill_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_connection_pill_render.py
"""The pill renders Connect when disconnected, Disconnect when online, and
disables Connect when unreachable."""

import importlib

from fastapi.testclient import TestClient

from backend.app.services.connection_monitor import ConnectionMonitor, ConnectionState
from tests._helpers.live_ctx import install_live_ctx


def _make_app(monkeypatch, tmp_path):
    for k, v in {
        "APP_ENV": "dev", "CATDV_BASE_URL": "http://localhost:0",
        "CATDV_USERNAME": "", "CATDV_PASSWORD": "p", "CATDV_CATALOG_ID": "881507",
        "GCP_PROJECT_ID": "p", "GCS_BUCKET_NAME": "b", "DATA_DIR": str(tmp_path),
    }.items():
        monkeypatch.setenv(k, v)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


class _Monitor:
    is_forced = False
    _forced_offline = False

    def __init__(self, state):
        self._state = state

    def current_state(self):
        return self._state


def _pill(monkeypatch, tmp_path, state):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        install_live_ctx(client.app, connection_monitor=_Monitor(state))
        return client.get("/ui/connection-pill").text


def test_disconnected_shows_connect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.disconnected)
    assert "/api/connection/connect" in html
    assert "Connect" in html


def test_online_shows_disconnect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.online)
    assert "/api/connection/disconnect" in html
    assert "Disconnect" in html


def test_unreachable_disables_connect(monkeypatch, tmp_path):
    html = _pill(monkeypatch, tmp_path, ConnectionState.offline)
    assert "disabled" in html
    assert "Unreachable" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_pill_render.py -v`
Expected: FAIL — the current pill renders only online/degraded/offline/syncing with Work-online/offline buttons.

- [ ] **Step 3: Implement the pill + context helper**

In `backend/app/routes/ui.py`, extract a context builder and use it
(replace lines 18-31):

```python
def _pill_context(request: Request) -> dict:
    live = request.app.state.live_ctx
    settings = request.app.state.core_ctx.settings
    state = "online"
    if live is not None:
        state = str(live.connection_monitor.current_state().value)
    return {
        "state": state,
        "connect_mode": getattr(settings, "catdv_connect_mode", "manual"),
    }


@router.get("/connection-pill", response_class=HTMLResponse)
async def connection_pill(request: Request):
    ctx = get_core_ctx(request)
    rows = await ctx.pending_ops_repo.list_pending(ctx.db)
    context = _pill_context(request)
    context["pending_count"] = len(rows)
    return templates.TemplateResponse(request, "connection_pill.html", context)
```

Replace `backend/app/templates/connection_pill.html` with:

```jinja
{# Connection pill (top-right). HTMX polls /ui/connection-pill every 5s.
   In manual connect_mode the primary action is Connect/Disconnect; the
   in-flight POST shows a spinner via hx-indicator. #}
<div id="connection-pill"
     class="connection-pill state-{{ state }}"
     hx-get="/ui/connection-pill"
     hx-trigger="every 5s"
     hx-swap="outerHTML">
  {% if state == "online" %}
    <span class="dot">●</span> Connected
  {% elif state == "disconnected" %}
    <span class="dot">○</span> Disconnected
  {% elif state == "degraded" %}
    <span class="dot">◐</span> degraded
  {% elif state == "syncing" %}
    <span class="dot">↻</span> syncing
  {% else %}
    <span class="dot">○</span> Unreachable
  {% endif %}
  <span class="htmx-indicator" aria-hidden="true">↻</span>

  <div class="menu">
    {% if connect_mode == "manual" %}
      {% if state == "online" %}
        <button hx-post="/api/connection/disconnect"
                hx-target="#connection-pill" hx-swap="outerHTML"
                hx-indicator="#connection-pill">Disconnect</button>
      {% elif state == "disconnected" %}
        <button hx-post="/api/connection/connect"
                hx-target="#connection-pill" hx-swap="outerHTML"
                hx-indicator="#connection-pill">Connect</button>
      {% else %}{# offline / unreachable #}
        <button disabled title="VPN tunnel down">Connect</button>
      {% endif %}
    {% else %}
      {% if state == "offline" %}
        <button hx-post="/api/connection/online"
                hx-target="#connection-pill" hx-swap="outerHTML">Work online</button>
      {% else %}
        <button hx-post="/api/connection/offline"
                hx-target="#connection-pill" hx-swap="outerHTML">Work offline</button>
      {% endif %}
    {% endif %}
    <button hx-post="/api/sync/run"
            hx-target="#sync-drawer" hx-swap="outerHTML">Sync now ({{ pending_count }})</button>
  </div>
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_pill_render.py tests/integration/test_routes_connect_disconnect.py -v`
Expected: PASS (the connect/disconnect HTMX branch now resolves `_pill_context`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/ui.py backend/app/templates/connection_pill.html tests/integration/test_connection_pill_render.py
git commit -m "Connection pill: 4 states + manual Connect/Disconnect"
```

---

### Task 10: Topbar chip — read-only disconnected/unreachable in manual mode

**Files:**
- Modify: `backend/app/templates/_connection_chip.html`
- Test: `tests/integration/test_connection_chip_render.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_connection_chip_render.py
"""In manual mode the chip is read-only — no /api/connection/retry button
(retry only probes; it cannot log in). It shows Connected/Disconnected/
Unreachable labels."""

from backend.app.routes.pages.templates import templates


def _render(mode):
    tmpl = templates.get_template("_connection_chip.html")
    return tmpl.render(mode=mode, connect_mode="manual", request=None)


def test_disconnected_label_no_retry():
    html = _render("disconnected")
    assert "Disconnected" in html
    assert "/api/connection/retry" not in html


def test_unreachable_label():
    html = _render("offline")
    assert "Unreachable" in html


def test_connected_label():
    html = _render("online")
    assert "Connected" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: FAIL — current chip says "Online"/"Offline — click to reconnect" and includes the `/api/connection/retry` button.

- [ ] **Step 3: Implement the chip**

Replace `backend/app/templates/_connection_chip.html` with a version that
honours `connect_mode` (default the template var so includes without it
still work). Add a `connect_mode` default in the front-matter `{%- set -%}`
block and branch on it:

```jinja
{# Topbar connection chip. Read-only in manual connect_mode (the pill owns
   Connect/Disconnect); the legacy auto-mode Reconnect button is kept for
   auto mode. mode is one of online|disconnected|offline|forced_offline. #}
{%- if connect_mode is not defined -%}
  {%- set connect_mode = request.app.state.core_ctx.settings.catdv_connect_mode
        if request is defined and request else "manual" -%}
{%- endif -%}
{%- if mode is not defined -%}
  {%- set _live = request.app.state.live_ctx if request is defined else None -%}
  {%- set _monitor = _live.connection_monitor if _live else None -%}
  {%- if _monitor is none -%}
    {%- set mode = "online" -%}
  {%- elif _monitor.is_forced -%}
    {%- set mode = "forced_offline" -%}
  {%- elif _monitor.current_state().value == "online" -%}
    {%- set mode = "online" -%}
  {%- elif _monitor.current_state().value == "disconnected" -%}
    {%- set mode = "disconnected" -%}
  {%- else -%}
    {%- set mode = "offline" -%}
  {%- endif -%}
{%- endif -%}
<div id="connection-chip" class="conn-chip conn-chip--{{ mode }}">
  {% if mode == "online" %}
    <span class="dot dot--green" aria-hidden="true">●</span><span>Connected</span>
  {% elif mode == "disconnected" %}
    <span class="dot dot--grey" aria-hidden="true">○</span><span>Disconnected</span>
  {% elif mode == "forced_offline" %}
    <span class="dot dot--red" aria-hidden="true"
          title="Set CATDV_OFFLINE=false and restart to reconnect">●</span><span>Offline (forced)</span>
  {% elif connect_mode == "manual" %}{# offline == unreachable, read-only #}
    <span class="dot dot--yellow" aria-hidden="true" title="VPN tunnel down">●</span><span>Unreachable</span>
  {% else %}{# auto mode: keep the Reconnect probe button #}
    <button type="button" hx-post="/api/connection/retry"
            hx-target="#connection-chip" hx-swap="outerHTML"
            title="Click to try reconnecting">
      <span class="dot dot--yellow" aria-hidden="true">●</span>
      <span>Offline — click to reconnect</span>
    </button>
  {% endif %}
</div>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Full suite + lint + commit**

Run: `.venv/bin/python -m pytest && .venv/bin/lint-imports`
Expected: all green.

```bash
git add backend/app/templates/_connection_chip.html tests/integration/test_connection_chip_render.py
git commit -m "Connection chip: read-only Connected/Disconnected/Unreachable in manual mode"
```

---

### Task 11: ADR + deploy config + decisions index

**Files:**
- Create: `docs/adr/0068-catdv-manual-connect.md`
- Modify: `docs/decisions.md` (append row 0068)
- Modify: `deploy/cloudrun.env.yaml` (add `CATDV_CONNECT_MODE`)
- No test (docs/config).

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0068-catdv-manual-connect.md` (MADR-lite, matching ADR
0066/0067): `# 0068. CatDV connection is manual on-demand on Cloud Run`,
`**Date:** 2026-06-10`, `**Status:** Accepted`, then `## Context` (always-on
instance would hold a seat 24/7), `## Alternatives` (auto-login kept for
local dev; a separate tunnel pinger rejected — one seat-free probe +
`logged_in` flag suffices), `## Decision` (manual default; Connect/Disconnect
endpoints; idle auto-disconnect; `disconnected` state; `ProviderHealth.reachable`),
`## Consequences` (the deployed service must run `CATDV_CONNECT_MODE=manual`
+ `CATDV_OFFLINE=false`; the pill is the seat control; `/api/info` auth
behavior is irrelevant because the seat truth is `logged_in`).

- [ ] **Step 2: Update the decisions index**

In `docs/decisions.md`, append after the 0067 row:

```markdown
| 0068 | 2026-06-10 | [CatDV connection is manual on-demand on Cloud Run](./adr/0068-catdv-manual-connect.md) |
```

- [ ] **Step 3: Update the deploy config**

In `deploy/cloudrun.env.yaml`, add under the CatDV block (near
`CATDV_OFFLINE`):

```yaml
# Manual connect-on-demand: boot disconnected (no seat); the operator
# clicks Connect to spend a seat and Disconnect to release it. See ADR 0068.
CATDV_CONNECT_MODE: "manual"
```

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0068-catdv-manual-connect.md docs/decisions.md deploy/cloudrun.env.yaml
git commit -m "ADR 0068 + deploy config: CatDV manual connect-on-demand"
```

---

## Self-review

**Spec coverage:**
- Boot modes (`auto`/`manual`/`offline`) → Task 1 (setting) + Task 6 (deferred login).
- Probe → state mapping with `logged_in` authority + `reachable` → Task 2 + Task 3.
- `disconnected` state → Task 3; mode surfaced in routes/health → Task 7.
- Connect/Disconnect endpoints + toast errors → Task 7 + Task 8.
- Idle auto-disconnect → Task 5 + Task 6 wiring.
- One pill, 4 states, `hx-indicator` transient → Task 9; chip read-only → Task 10.
- ADR + deploy config (mandated by CLAUDE.md) → Task 11.
- Manual acceptance flows (spec) are exercised on the deployed service after merge; Task 6/Task 10 full-suite steps guard the offline contract.

**Placeholder scan:** none — every code step shows complete code. The ADR
body (Task 11) is described section-by-section rather than verbatim because
it is prose, not code.

**Type/name consistency:** `catdv_connect_mode`, `catdv_idle_logout_s`,
`ProviderHealth.reachable`, `ConnectionState.disconnected`,
`ConnectionMonitor(manual=, logged_in=)`, `CatdvClient.logged_in` /
`.last_activity` / `track_activity=`, `IdleDisconnector(client=, monitor=,
idle_timeout_s=, check_interval_s=, clock=)` with `.check_once()` /
`.start()` / `.stop()`, `LiveCtx.idle_disconnector`, `_pill_context`,
`_toast_header`, `_pill_or_json` — all used consistently across tasks.

**Known follow-ups (out of scope):** `PROXY_SOURCE=filesystem` would call
`fetch_media_store_map(catdv)` at boot (needs login) — the deploy uses
`rest`, so unaffected; if filesystem is ever used with manual mode, defer
that fetch to first-connect. No SSE→toast for idle (pill poll surfaces it).
