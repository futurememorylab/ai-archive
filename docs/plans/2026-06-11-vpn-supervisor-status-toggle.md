# VPN (onetun) Supervisor + Status/Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator see the cloud WireGuard tunnel's state and turn it on/off from the UI, by moving onetun supervision out of `entrypoint.sh` into an app-owned `VpnSupervisor`, with the on/off choice persisted and defaulting to **off**.

**Architecture:** A `LiveCtx`-scoped `VpnSupervisor` owns onetun as an `asyncio` subprocess (spawn/restart/kill + a health-probe loop). Desired state lives in the `app_meta` KV table (`vpn_desired`, default `off`). A `/api/vpn` router exposes status/enable/disable; the existing connection chip gains a VPN row. The whole feature is gated on `Settings.vpn_managed` (WireGuard configured) — true only on Cloud Run; local dev is untouched. `disable` is a master switch: the route releases the CatDV seat over the still-live tunnel, then the supervisor drops the tunnel.

**Tech Stack:** Python 3.13, FastAPI, pydantic-settings, aiosqlite, asyncio subprocess, Jinja2 + HTMX, pytest/pytest-asyncio.

**Spec:** `docs/specs/2026-06-11-vpn-supervisor-status-toggle-design.md`

---

## File structure

- **Modify** `backend/app/settings.py` — WireGuard/onetun fields + `vpn_managed` property.
- **Modify** `backend/app/repositories/app_meta.py` — `get_vpn_desired` / `set_vpn_desired`.
- **Create** `backend/app/services/vpn_supervisor.py` — `VpnSupervisor`, `VpnStatus`.
- **Modify** `backend/app/context.py` — `vpn_supervisor` field on `LiveCtx`; build it in `_build_sync_subsystem`; teardown in `aclose`.
- **Modify** `backend/app/main.py` — start the supervisor in the lifespan; register the `/api/vpn` router.
- **Create** `backend/app/routes/vpn.py` — `/api/vpn` status/enable/disable.
- **Modify** `backend/app/templates/_connection_chip_inner.html` — VPN row (status + toggle) and disable CatDV controls when VPN off.
- **Modify** `deploy/entrypoint.sh` — remove the onetun block (app owns it now).
- **Create** `docs/adr/0075-onetun-app-supervised.md` + **modify** `docs/decisions.md`.
- **Tests:** `tests/unit/test_vpn_supervisor.py`, `tests/unit/test_app_meta_vpn.py`, `tests/integration/test_vpn_routes.py`, `tests/unit/test_settings_vpn_managed.py`.

---

## Task 1: Settings — WireGuard/onetun fields + `vpn_managed`

**Files:**
- Modify: `backend/app/settings.py`
- Test: `tests/unit/test_settings_vpn_managed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings_vpn_managed.py
import pytest
from backend.app.settings import Settings

_BASE = dict(
    catdv_base_url="http://127.0.0.1:18080",
    catdv_catalog_id=1,
    gcp_project_id="p",
    gcs_bucket_name="b",
)


def test_vpn_unmanaged_when_wg_absent():
    s = Settings(**_BASE)
    assert s.vpn_managed is False
    assert s.onetun_mtu == 1380


def test_vpn_managed_when_all_wg_present():
    s = Settings(
        **_BASE,
        wg_private_key="priv",
        wg_endpoint="gw.example:51820",
        wg_peer_pubkey="pub",
        wg_source_ip="192.168.3.5",
    )
    assert s.vpn_managed is True
    assert s.wg_private_key.get_secret_value() == "priv"


def test_vpn_unmanaged_when_partial_wg():
    s = Settings(**_BASE, wg_private_key="priv", wg_endpoint="gw.example:51820")
    assert s.vpn_managed is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_vpn_managed.py -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'vpn_managed'`).

- [ ] **Step 3: Add the fields + property**

In `backend/app/settings.py`, add the import for `SecretStr`:

```python
from pydantic import Field, SecretStr, model_validator
```

Then add, just below the `dev_reload` field (after line 79):

```python
    # WireGuard / onetun (cloud only). Today consumed by entrypoint.sh; now
    # read here so the app can supervise onetun and expose a status/toggle.
    # vpn_managed (all four present) gates the whole VPN feature — true on
    # Cloud Run, false in local dev (no tunnel). WG_PRIVATE_KEY is a secret.
    wg_private_key: SecretStr | None = None
    wg_endpoint: str | None = None
    wg_peer_pubkey: str | None = None
    wg_source_ip: str | None = None
    wg_keepalive_s: int = 25
    # onetun tunnel MTU. 1380 = 1460 - 80 (GCP hygiene; see ADR 0074).
    onetun_mtu: int = 1380
    onetun_local_forward: str = "127.0.0.1:18080:192.168.1.41:8080:TCP"
```

Add the property after the `_validate_fs_archive` validator (before `load_settings`):

```python
    @property
    def vpn_managed(self) -> bool:
        """True when WireGuard is configured (cloud). Gates the VPN feature."""
        return bool(
            self.wg_private_key is not None
            and self.wg_private_key.get_secret_value()
            and self.wg_endpoint
            and self.wg_peer_pubkey
            and self.wg_source_ip
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_settings_vpn_managed.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_vpn_managed.py
git commit -m "feat(settings): WireGuard/onetun config + vpn_managed gate"
```

---

## Task 2: `app_meta` — persist desired VPN state

**Files:**
- Modify: `backend/app/repositories/app_meta.py`
- Test: `tests/unit/test_app_meta_vpn.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_app_meta_vpn.py
import aiosqlite
import pytest
from backend.app.repositories.app_meta import get_vpn_desired, set_vpn_desired


@pytest.fixture
async def conn():
    c = await aiosqlite.connect(":memory:")
    await c.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)")
    await c.commit()
    yield c
    await c.close()


async def test_default_is_off_when_absent(conn):
    assert await get_vpn_desired(conn) == "off"


async def test_set_then_get_roundtrip(conn):
    await set_vpn_desired(conn, "on")
    assert await get_vpn_desired(conn) == "on"
    await set_vpn_desired(conn, "off")
    assert await get_vpn_desired(conn) == "off"


async def test_set_rejects_bad_value(conn):
    with pytest.raises(ValueError):
        await set_vpn_desired(conn, "maybe")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_app_meta_vpn.py -v`
Expected: FAIL (`ImportError: cannot import name 'get_vpn_desired'`).

- [ ] **Step 3: Add the helpers**

Append to `backend/app/repositories/app_meta.py`:

```python
_VPN_DESIRED_KEY = "vpn_desired"
_VPN_VALUES = ("on", "off")


async def get_vpn_desired(conn: aiosqlite.Connection) -> str:
    """Return the persisted desired VPN state, defaulting to 'off' (opt-in;
    keeps the cloud from grabbing the shared WG peer key on boot)."""
    cur = await conn.execute(
        "SELECT value FROM app_meta WHERE key = ?", (_VPN_DESIRED_KEY,)
    )
    row = await cur.fetchone()
    return row[0] if row is not None and row[0] in _VPN_VALUES else "off"


async def set_vpn_desired(conn: aiosqlite.Connection, value: str) -> None:
    if value not in _VPN_VALUES:
        raise ValueError(f"vpn_desired must be 'on'|'off', got {value!r}")
    await conn.execute(
        "INSERT INTO app_meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (_VPN_DESIRED_KEY, value),
    )
    await conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_app_meta_vpn.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/app_meta.py tests/unit/test_app_meta_vpn.py
git commit -m "feat(repo): persist vpn_desired in app_meta (default off)"
```

---

## Task 3: `VpnSupervisor` — process lifecycle

**Files:**
- Create: `backend/app/services/vpn_supervisor.py`
- Test: `tests/unit/test_vpn_supervisor.py`

The supervisor owns the onetun subprocess. It is pure process+state: the
CatDV-seat coupling (logout before tunnel-down) is orchestrated by the
route in Task 7, not here. `spawn` and `probe_health` are injected so tests
never run real onetun.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_vpn_supervisor.py
import asyncio
import pytest
from backend.app.services.vpn_supervisor import VpnSupervisor


class FakeProc:
    def __init__(self):
        self.returncode = None
        self._exit = asyncio.Event()
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15
        self._exit.set()

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._exit.set()

    async def wait(self):
        await self._exit.wait()
        return self.returncode or 0


def _make(desired="off", spawned=None, probe_ok=True):
    state = {"desired": desired}
    spawned = spawned if spawned is not None else []

    async def spawn():
        p = FakeProc()
        spawned.append(p)
        return p

    async def get_desired():
        return state["desired"]

    async def set_desired(v):
        state["desired"] = v

    async def probe_health():
        return probe_ok

    sup = VpnSupervisor(
        spawn=spawn, get_desired=get_desired, set_desired=set_desired,
        probe_health=probe_health, restart_backoff_s=0.01,
        kill_timeout_s=0.2, health_interval_s=0.01,
    )
    return sup, state, spawned


async def test_start_off_does_not_spawn():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    await asyncio.sleep(0.02)
    assert spawned == []
    assert sup.status().process_running is False
    assert sup.status().desired == "off"
    await sup.aclose()


async def test_start_on_spawns():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    assert len(spawned) == 1
    assert sup.status().process_running is True
    await sup.aclose()


async def test_enable_persists_and_spawns():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    st = await sup.enable()
    await asyncio.sleep(0.02)
    assert state["desired"] == "on"
    assert st.desired == "on"
    assert sup.status().process_running is True
    await sup.aclose()


async def test_disable_persists_and_kills():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.disable()
    assert state["desired"] == "off"
    assert st.process_running is False
    assert spawned[0].terminated is True
    await sup.aclose()


async def test_restart_on_crash_while_on():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    spawned[0].returncode = 1
    spawned[0]._exit.set()          # simulate onetun crashing
    await asyncio.sleep(0.05)       # backoff is 0.01s
    assert len(spawned) >= 2        # respawned
    await sup.aclose()


async def test_no_restart_after_disable():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)
    await sup.disable()
    n = len(spawned)
    await asyncio.sleep(0.05)
    assert len(spawned) == n        # stayed down
    await sup.aclose()


async def test_healthy_reflects_probe():
    sup, state, spawned = _make(desired="on", probe_ok=True)
    await sup.start()
    await asyncio.sleep(0.03)
    assert sup.status().healthy is True
    await sup.aclose()
    assert sup.status().healthy is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_vpn_supervisor.py -v`
Expected: FAIL (`ModuleNotFoundError: backend.app.services.vpn_supervisor`).

- [ ] **Step 3: Write the supervisor**

```python
# backend/app/services/vpn_supervisor.py
"""VpnSupervisor — owns the onetun WireGuard subprocess on the cloud
deployment. Keeps onetun's actual state aligned with a persisted desired
(on/off) state, restarts it on crash, and reports status. Cloud-only:
constructed only when WireGuard is configured (Settings.vpn_managed).

Pure process + state. The CatDV-seat coupling on disable (log out over the
live tunnel, then drop the tunnel) is orchestrated by routes/vpn.py, not
here, so this class stays testable with an injected spawn function."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import NamedTuple, Protocol

logger = logging.getLogger(__name__)


class VpnStatus(NamedTuple):
    managed: bool
    desired: str            # "on" | "off"
    process_running: bool
    healthy: bool


class _Proc(Protocol):
    returncode: int | None

    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    async def wait(self) -> int: ...


SpawnFn = Callable[[], Awaitable[_Proc]]
ProbeFn = Callable[[], Awaitable[bool]]
StrFn = Callable[[], Awaitable[str]]
SetFn = Callable[[str], Awaitable[None]]


class VpnSupervisor:
    def __init__(
        self,
        *,
        spawn: SpawnFn,
        get_desired: StrFn,
        set_desired: SetFn,
        probe_health: ProbeFn,
        restart_backoff_s: float = 2.0,
        kill_timeout_s: float = 5.0,
        health_interval_s: float = 15.0,
    ) -> None:
        self._spawn = spawn
        self._get_desired = get_desired
        self._set_desired = set_desired
        self._probe_health = probe_health
        self._backoff = restart_backoff_s
        self._kill_timeout = kill_timeout_s
        self._health_interval = health_interval_s
        self._proc: _Proc | None = None
        self._supervise_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._desired = "off"
        self._healthy = False
        self._stop = asyncio.Event()

    async def start(self) -> None:
        """Lifespan startup: adopt the persisted desired state."""
        self._desired = await self._get_desired()
        if self._desired == "on":
            self._spin_up()

    async def enable(self) -> VpnStatus:
        await self._set_desired("on")
        self._desired = "on"
        if self._supervise_task is None or self._supervise_task.done():
            self._spin_up()
        return self.status()

    async def disable(self) -> VpnStatus:
        await self._set_desired("off")
        self._desired = "off"
        await self._spin_down()
        return self.status()

    def status(self) -> VpnStatus:
        running = self._proc is not None
        return VpnStatus(
            managed=True,
            desired=self._desired,
            process_running=running,
            healthy=self._healthy if running else False,
        )

    async def aclose(self) -> None:
        await self._spin_down()

    # --- internals --------------------------------------------------

    def _spin_up(self) -> None:
        self._stop.clear()
        self._supervise_task = asyncio.create_task(self._supervise())
        self._health_task = asyncio.create_task(self._health_loop())

    async def _spin_down(self) -> None:
        self._stop.set()
        await self._kill_proc()          # unblocks _supervise's proc.wait()
        for t in (self._supervise_task, self._health_task):
            if t is not None:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        self._supervise_task = None
        self._health_task = None
        self._healthy = False

    async def _supervise(self) -> None:
        while not self._stop.is_set():
            self._proc = await self._spawn()
            rc = await self._proc.wait()
            self._proc = None
            if self._stop.is_set():
                break
            logger.warning("onetun exited rc=%s; restarting in %ss", rc, self._backoff)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._backoff)
            except asyncio.TimeoutError:
                pass

    async def _health_loop(self) -> None:
        while not self._stop.is_set():
            if self._proc is not None:
                try:
                    self._healthy = await self._probe_health()
                except Exception:  # noqa: BLE001 — probe is best-effort
                    self._healthy = False
            else:
                self._healthy = False
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._health_interval)
            except asyncio.TimeoutError:
                pass

    async def _kill_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=self._kill_timeout)
        except asyncio.TimeoutError:
            proc.kill()
        self._proc = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_vpn_supervisor.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/vpn_supervisor.py tests/unit/test_vpn_supervisor.py
git commit -m "feat(vpn): VpnSupervisor owning onetun subprocess (spawn/restart/kill/health)"
```

---

## Task 4: Wire the supervisor into `context.py`

**Files:**
- Modify: `backend/app/context.py:200-227` (LiveCtx fields), `:346-361` (aclose), `:682-795` (`_build_sync_subsystem` + assembly)
- Test: covered by Task 3 (unit) + Task 7 (integration); no new test here.

- [ ] **Step 1: Add the `vpn_supervisor` field to `LiveCtx`**

In `backend/app/context.py`, in the `LiveCtx` optional-field block (after
`idle_disconnector` at line 227), add:

```python
    vpn_supervisor: "VpnSupervisor | None" = None
```

Add the import near the other service imports at the top of the file:

```python
from backend.app.services.vpn_supervisor import VpnSupervisor
```

- [ ] **Step 2: Build the supervisor in `_build_sync_subsystem`**

In `_build_sync_subsystem`, immediately before the `return LiveCtx(...)`
(line 779), add:

```python
    vpn_supervisor = None
    if settings.vpn_managed:
        import os

        from backend.app.services.vpn_supervisor import VpnSupervisor

        def _make_spawn():
            async def _spawn():
                env = {
                    **os.environ,
                    # Pass the key via env, not argv — avoids leaking it in
                    # `ps` and onetun's "private key on CLI" warning.
                    "ONETUN_PRIVATE_KEY": settings.wg_private_key.get_secret_value(),
                }
                return await asyncio.create_subprocess_exec(
                    "onetun",
                    "--endpoint-addr", settings.wg_endpoint,
                    "--endpoint-public-key", settings.wg_peer_pubkey,
                    "--source-peer-ip", settings.wg_source_ip,
                    "--keep-alive", str(settings.wg_keepalive_s),
                    "--max-transmission-unit", str(settings.onetun_mtu),
                    settings.onetun_local_forward,
                    env=env,
                )
            return _spawn

        async def _probe_tunnel() -> bool:
            # Unauthenticated GET /catdv/api/info through the tunnel — tests
            # the tunnel without spending a seat. Bounded by the health probe
            # timeout so a dead tunnel returns False fast.
            if arch.catdv is None:
                return False
            try:
                await asyncio.wait_for(
                    arch.catdv.health(), timeout=float(settings.health_probe_timeout_s)
                )
                return True
            except Exception:  # noqa: BLE001 — any failure ⇒ tunnel not healthy
                return False

        vpn_supervisor = VpnSupervisor(
            spawn=_make_spawn(),
            get_desired=lambda: app_meta.get_vpn_desired(core.db),
            set_desired=lambda v: app_meta.set_vpn_desired(core.db, v),
            probe_health=_probe_tunnel,
        )
```

`asyncio` is already imported inside `_build_archive_subsystem`; add a
top-level `import asyncio` at the head of `context.py` if not already
present, and `from backend.app.repositories import app_meta`.

- [ ] **Step 3: Pass it into the `LiveCtx(...)` constructor**

Add to the `return LiveCtx(...)` call (after `idle_disconnector=idle_disconnector,`):

```python
        vpn_supervisor=vpn_supervisor,
```

- [ ] **Step 4: Tear it down in `aclose`**

In `LiveCtx.aclose` (line 346), add as the **first** teardown step (before
`media_prefetcher`), so the tunnel is dropped last among external deps but
the supervisor's tasks are cancelled cleanly:

```python
        if self.vpn_supervisor is not None:
            await self.vpn_supervisor.aclose()
```

- [ ] **Step 5: Verify nothing broke**

Run: `.venv/bin/python -m pytest tests/unit -q && .venv/bin/lint-imports`
Expected: PASS; no import-contract violations.

- [ ] **Step 6: Commit**

```bash
git add backend/app/context.py
git commit -m "feat(vpn): build VpnSupervisor on LiveCtx when WG configured"
```

---

## Task 5: Start the supervisor in the lifespan

**Files:**
- Modify: `backend/app/main.py:80-86` (lifespan startup)

- [ ] **Step 1: Start it before the connection monitor**

In `backend/app/main.py`, inside `if live is not None:` (line 80), add as
the **first** start call:

```python
        if live.vpn_supervisor is not None:
            await live.vpn_supervisor.start()
```

(Resulting order: vpn_supervisor → connection_monitor → idle_disconnector
→ sync_engine → lru_eviction → media_prefetcher.)

- [ ] **Step 2: Verify the app still boots offline (no WG configured)**

Run: `.venv/bin/python -m pytest tests/integration -q -k "startup or boot or app" `
Expected: PASS (local/test config has no WG ⇒ `vpn_supervisor is None` ⇒ no-op).

- [ ] **Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(vpn): start VpnSupervisor in app lifespan"
```

---

## Task 6: `/api/vpn` routes

**Files:**
- Create: `backend/app/routes/vpn.py`
- Modify: `backend/app/main.py` (register router)
- Test: `tests/integration/test_vpn_routes.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_vpn_routes.py
import pytest
from httpx import ASGITransport, AsyncClient
from backend.app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        yield c


async def test_status_unmanaged_returns_managed_false(client):
    # Local/test config has no WG ⇒ vpn_managed False ⇒ supervisor is None.
    r = await client.get("/api/vpn/status")
    assert r.status_code == 200
    assert r.json()["managed"] is False


async def test_enable_unmanaged_409(client):
    r = await client.post("/api/vpn/enable")
    assert r.status_code == 409


async def test_disable_unmanaged_409(client):
    r = await client.post("/api/vpn/disable")
    assert r.status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_vpn_routes.py -v`
Expected: FAIL (404 — router not registered).

- [ ] **Step 3: Write the router**

```python
# backend/app/routes/vpn.py
"""VPN (onetun tunnel) control surface. Cloud-only: every endpoint returns
409 when the deployment is not VPN-managed (no WireGuard configured).

`disable` is a master switch: release the CatDV seat over the still-live
tunnel and pin the monitor offline BEFORE the supervisor drops the tunnel,
so logout traverses a working connection."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request

from backend.app.routes.pages.templates import templates as _templates

router = APIRouter(prefix="/api/vpn", tags=["vpn"])


def _supervisor(request: Request):
    live = request.app.state.live_ctx
    return live.vpn_supervisor if live is not None else None


def _status_dict(sup) -> dict:
    if sup is None:
        return {"managed": False, "desired": "off",
                "process_running": False, "healthy": False}
    s = sup.status()
    return {"managed": s.managed, "desired": s.desired,
            "process_running": s.process_running, "healthy": s.healthy}


def _toast(message: str, level: str = "success") -> dict[str, str]:
    return {"HX-Trigger": json.dumps({"toast": {"message": message, "level": level}})}


async def _reply(request: Request, sup, *, headers: dict | None = None):
    if request.headers.get("HX-Request") == "true":
        return _templates.TemplateResponse(
            request, "_connection_chip_inner.html", {}, headers=headers,
        )
    from fastapi.responses import JSONResponse
    return JSONResponse(_status_dict(sup), headers=headers)


@router.get("/status")
async def status(request: Request):
    return _status_dict(_supervisor(request))


@router.post("/enable")
async def enable(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    live = request.app.state.live_ctx
    # Allow the monitor to probe the tunnel again now that it's coming up.
    live.connection_monitor.set_manual_offline(False)
    await sup.enable()
    return await _reply(request, sup, headers=_toast("VPN tunnel enabled."))


@router.post("/disable")
async def disable(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    live = request.app.state.live_ctx
    # Master switch: release the seat over the live tunnel, pin offline so
    # the monitor stops probing a dead tunnel, THEN drop the tunnel.
    if live.catdv is not None:
        try:
            await live.catdv.logout()
        except Exception:  # noqa: BLE001 — seat will time out server-side
            pass
    live.connection_monitor.set_manual_offline(True)
    await sup.disable()
    return await _reply(request, sup, headers=_toast("VPN tunnel disabled."))
```

- [ ] **Step 4: Register the router**

In `backend/app/main.py`, add the import beside the other route imports
(near line 20):

```python
from backend.app.routes.vpn import router as vpn_router
```

In `register_routers`, add beside the others (near line 95):

```python
    app.include_router(vpn_router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_vpn_routes.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/vpn.py backend/app/main.py tests/integration/test_vpn_routes.py
git commit -m "feat(vpn): /api/vpn status/enable/disable with master-switch teardown"
```

---

## Task 7: Frontend — VPN row in the connection chip

**Files:**
- Modify: `backend/app/templates/_connection_chip_inner.html`

The chip already computes CatDV `mode` and renders Connect/Disconnect.
Add a VPN row above it that reads the supervisor status, and disable the
CatDV Connect control when the VPN is off. Reuse existing `.btn`/`.dot`
classes — no new vocabulary (keeps the design-language guard green).

- [ ] **Step 1: Compute VPN status at the top of the template**

After the existing `mode` block (the `{%- endif -%}` that closes the `mode`
computation, before `{% if connect_mode == "manual" %}`), insert:

```jinja
{%- set _live = request.app.state.live_ctx if request is defined else None -%}
{%- set _sup = _live.vpn_supervisor if _live else None -%}
{%- set vpn = _sup.status() if _sup else None -%}
```

- [ ] **Step 2: Render the VPN row (only when managed)**

Immediately after the snippet from Step 1, add:

```jinja
{% if vpn and vpn.managed %}
  <span class="vpn-row">
    {% if vpn.desired == "off" %}
      <span class="dot dot--grey" aria-hidden="true" title="WireGuard tunnel is off">○</span><span>VPN off</span>
      <button type="button" class="btn link" hx-post="/api/vpn/enable"
              hx-target="#connection-chip" hx-swap="innerHTML"
              title="Bring the WireGuard tunnel up">Turn on</button>
    {% elif vpn.healthy %}
      <span class="dot dot--green" aria-hidden="true" title="Tunnel up and reaching CatDV">●</span><span>VPN on</span>
      <button type="button" class="btn link"
              hx-post="/api/vpn/disable" hx-confirm="Turn the VPN off? This drops the CatDV connection and cedes the tunnel."
              hx-target="#connection-chip" hx-swap="innerHTML"
              title="Drop the tunnel and release the CatDV seat">Turn off</button>
    {% else %}
      <span class="dot dot--yellow" aria-hidden="true" title="Tunnel process up but not reaching CatDV">●</span><span>VPN on · unreachable</span>
      <button type="button" class="btn link"
              hx-post="/api/vpn/disable" hx-confirm="Turn the VPN off? This drops the CatDV connection and cedes the tunnel."
              hx-target="#connection-chip" hx-swap="innerHTML">Turn off</button>
    {% endif %}
  </span>
{% endif %}
```

- [ ] **Step 3: Disable CatDV Connect while VPN is off**

In the `{% if connect_mode == "manual" %}` block, change the
`{% elif mode == "disconnected" %}` branch so the Connect button is
suppressed when the VPN is off:

Replace:

```jinja
  {% elif mode == "disconnected" %}
    <span class="dot dot--grey" aria-hidden="true">○</span><span>Disconnected</span>
    <button type="button" class="btn link" hx-post="/api/connection/connect"
            hx-target="#connection-chip" hx-swap="innerHTML"
            title="Log in to CatDV (takes the license seat)">Connect</button>
```

with:

```jinja
  {% elif mode == "disconnected" %}
    <span class="dot dot--grey" aria-hidden="true">○</span><span>Disconnected</span>
    {% if vpn and vpn.managed and vpn.desired == "off" %}
      <span class="btn link" aria-disabled="true"
            title="Turn the VPN on first">Connect</span>
    {% else %}
      <button type="button" class="btn link" hx-post="/api/connection/connect"
              hx-target="#connection-chip" hx-swap="innerHTML"
              title="Log in to CatDV (takes the license seat)">Connect</button>
    {% endif %}
```

- [ ] **Step 4: Verify the template renders both ways**

Run: `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/integration -q -k "chip or connection or vpn"`
Expected: PASS. Manually confirm in Task 11 flows.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/_connection_chip_inner.html
git commit -m "feat(ui): VPN status + on/off toggle in the connection chip"
```

---

## Task 8: Remove onetun from the entrypoint

**Files:**
- Modify: `deploy/entrypoint.sh`

- [ ] **Step 1: Delete the onetun block**

Remove the entire `if [ -n "${WG_PRIVATE_KEY:-}" ]; then … fi` block
(the onetun restart loop) and the MTU comment added in ADR 0074. The app
now spawns onetun via `VpnSupervisor`. Update the top-of-file comment to
state that onetun is app-supervised (see ADR 0075). litestream/uvicorn
lines are unchanged.

The resulting file:

```sh
#!/bin/sh
# Container entrypoint. onetun is NO LONGER started here — the app owns it
# via VpnSupervisor (default off; toggle in the UI). See ADR 0075.
#   litestream -- SQLite restore + replication (when LITESTREAM_REPLICA_URL set)
#   uvicorn    -- the app (always; exec'd so it receives SIGTERM)
set -eu

PORT="${PORT:-8765}"
UVICORN="python -m uvicorn backend.app.main:app --host 0.0.0.0 --port $PORT --timeout-graceful-shutdown 3"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
  litestream restore -if-db-not-exists -if-replica-exists "$DB_PATH"
  exec litestream replicate -exec "$UVICORN"
fi

exec $UVICORN
```

- [ ] **Step 2: Sanity-check the script**

Run: `sh -n deploy/entrypoint.sh && echo OK`
Expected: `OK` (no syntax error).

- [ ] **Step 3: Commit**

```bash
git add deploy/entrypoint.sh
git commit -m "refactor(deploy): onetun is app-supervised, drop it from entrypoint"
```

---

## Task 9: ADR 0075 + decisions index

**Files:**
- Create: `docs/adr/0075-onetun-app-supervised.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Write the ADR**

```markdown
# 0075. onetun is app-supervised (VPN status + toggle), default off

**Date:** 2026-06-11
**Status:** Accepted

## Context

ADR 0066/0067 ran onetun from `entrypoint.sh` in a restart loop, opaque to
the app. The operator needed (a) visibility into the tunnel and (b) the
ability to cede it to their Mac (the shared-peer-key collision behind issue
#43, see ADR 0074). "Disconnect" only logs out of CatDV; it does not stop
the tunnel, so the collision persisted.

## Decision

Move onetun supervision into the app (`services/vpn_supervisor.py`,
`LiveCtx`-scoped): spawn/restart/kill as an asyncio subprocess plus a
health-probe loop. Desired on/off state is persisted in `app_meta`
(`vpn_desired`, **default off** — opt-in, mirrors manual-connect ADR 0068).
`/api/vpn` exposes status/enable/disable; the connection chip gains a VPN
row. The feature is gated on `Settings.vpn_managed` (WireGuard configured)
so local dev is unaffected. `disable` is a master switch: the route logs out
of CatDV over the live tunnel and pins the monitor offline before the
supervisor drops the tunnel. The WG private key is passed to onetun via
`ONETUN_PRIVATE_KEY` env (not argv) — closes the "key on CLI" exposure.

## Consequences

- Fresh cloud instances boot with the tunnel **off**; CatDV is unreachable
  until the operator enables it. Intended (no auto-collision).
- onetun lifecycle is tied to the app lifespan — cleaner shutdown than the
  detached entrypoint loop.
- Amends ADR 0066/0067 (entrypoint no longer runs onetun). MTU stays at 1380
  (ADR 0074), now passed as a flag by the supervisor from `Settings.onetun_mtu`.
```

- [ ] **Step 2: Add the index row**

Append to the table in `docs/decisions.md`:

```markdown
| 0075 | 2026-06-11 | [onetun is app-supervised (VPN status + toggle), default off](./adr/0075-onetun-app-supervised.md) |
```

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0075-onetun-app-supervised.md docs/decisions.md
git commit -m "docs: ADR 0075 — app-supervised onetun, VPN status/toggle"
```

---

## Task 10: Full suite + import contracts

- [ ] **Step 1: Run everything**

Run: `.venv/bin/python -m pytest -q && .venv/bin/lint-imports`
Expected: all green; no import-contract violations.

- [ ] **Step 2: Commit any fixes** (only if needed)

```bash
git add -A && git commit -m "test: fix-ups for VPN supervisor feature"
```

---

## Manual acceptance flows

(From the spec — run on a deployed Cloud Run revision with `WG_*` set.)

1. **Fresh deploy boots VPN off** — chip shows *VPN off*, CatDV Connect
   disabled, Mac WireGuard works.
2. **Turn on** — chip → *VPN on*; CatDV Connect enabled; manual Connect
   reaches CatDV.
3. **Turn off** — confirm dialog → seat released, CatDV Connect disabled,
   chip → *VPN off*; Mac reconnects without REKEY_TIMEOUT.
4. **Persistence** — toggle off, restart revision → still *VPN off*; toggle
   on, restart → *VPN on*.
5. **Local dev** — no `WG_*`: VPN row absent; CatDV direct as before.
6. **Tunnel-down distinguishable** — VPN on but handshake failing → chip
   shows *VPN on · unreachable* rather than a bare "offline".

---

## Self-review notes

- **Spec coverage:** gating (Task 1), persistence default-off (Task 2),
  supervisor + health (Task 3), wiring/lifespan (Tasks 4–5), API + master
  switch (Task 6), UI status+toggle+coupling (Task 7), entrypoint removal
  (Task 8), ADR (Task 9). All six acceptance flows map to tasks.
- **Type consistency:** `VpnStatus(managed, desired, process_running,
  healthy)` and `get_vpn_desired/set_vpn_desired` names are used identically
  across service, repo, context, routes, and template.
- **Open follow-ups (out of scope, noted):** the dedicated cloud WG peer key
  (structural collision fix) and the optimistic-apply/sync-surfacing defects
  on #43 are separate work.
