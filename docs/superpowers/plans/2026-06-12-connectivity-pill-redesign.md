# Connectivity Pill + Dropdown Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat topbar connection chip with the prototype's Status pill + dropdown (VPN tunnel + CatDV Annotator service rows, toggle switches, Retry on error), driven by the existing backend surface plus one honest on-demand VPN re-probe.

**Architecture:** The stable `#connection-chip` container keeps its 5s `/ui/connection-chip` poll and innerHTML-swap, and gains `x-data="popover()"` so the dropdown's open state survives polls and in-dropdown actions. The swapped inner partial (`_connection_chip_inner.html`) is rewritten to render the pill trigger + dropdown panel, deriving all state from the existing `vpn_supervisor.status()` and `connection_monitor` mode. All actions reuse existing endpoints; only `POST /api/vpn/retry` (forcing `VpnSupervisor.probe_now()`) is new.

**Tech Stack:** FastAPI + Jinja2 partials, HTMX (poll + action swaps), Alpine.js `popover()` behaviour, `app.css` design tokens, pytest (`TestClient` + direct `templates.get_template(...).render(...)`).

**Spec:** `docs/specs/2026-06-12-connectivity-pill-redesign-design.md`

---

## File Structure

**Modified:**
- `backend/app/services/vpn_supervisor.py` — add `probe_now()`.
- `backend/app/routes/vpn.py` — add `POST /retry`.
- `backend/app/routes/connection.py` — success-toast `HX-Trigger` on connect/disconnect.
- `backend/app/templates/_connection_chip_inner.html` — rewrite to pill + dropdown; guard injected `vpn`.
- `backend/app/templates/_connection_chip.html` — add `popover()` to the stable container.
- `backend/app/static/htmxAlpine.js` — reinit the chip subtree after each swap.
- `backend/app/static/app.css` — pill / dropdown / switch / service-row styles.
- `backend/app/templates/pages/_topbar_pills.html` — remove CATALOG + READ-ONLY pills.
- `docs/decisions.md` — index the new ADR.

**Created:**
- `docs/adr/NNNN-vpn-on-demand-reprobe-and-connection-pill.md` — record the addition + redesign (`NNNN` = one higher than the current last ADR; check `docs/adr/`).
- `tests/integration/test_vpn_retry_route.py` — the `/api/vpn/retry` route.
- Extends `tests/unit/test_vpn_supervisor.py`, `tests/integration/test_connection_chip_render.py`, `tests/integration/test_routes_connect_disconnect.py`.

**Test entry points:** the venv is `.venv/bin/python` (Python 3.12/3.13). Run pytest as `.venv/bin/python -m pytest`.

---

## Task 1: `VpnSupervisor.probe_now()` — on-demand health re-probe

**Files:**
- Modify: `backend/app/services/vpn_supervisor.py`
- Test: `tests/unit/test_vpn_supervisor.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_vpn_supervisor.py` (the `_make` helper + `FakeProc` already exist in this file):

```python
async def test_probe_now_running_sets_healthy_from_probe():
    sup, state, spawned = _make(desired="on", probe_ok=True)
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.probe_now()
    assert st.healthy is True
    assert sup.status().healthy is True
    await sup.aclose()


async def test_probe_now_running_probe_false_marks_unhealthy():
    sup, state, spawned = _make(desired="on", probe_ok=False)
    await sup.start()
    await asyncio.sleep(0.02)
    st = await sup.probe_now()
    assert st.healthy is False
    assert st.process_running is True   # proc still up; only the probe failed
    await sup.aclose()


async def test_probe_now_not_running_is_unhealthy_noop():
    sup, state, spawned = _make(desired="off")
    await sup.start()
    st = await sup.probe_now()
    assert st.process_running is False
    assert st.healthy is False
    await sup.aclose()


async def test_probe_now_swallows_probe_exception():
    sup, state, spawned = _make(desired="on")
    await sup.start()
    await asyncio.sleep(0.02)

    async def boom():
        raise RuntimeError("probe blew up")

    sup._probe_health = boom            # same best-effort contract as _health_loop
    st = await sup.probe_now()
    assert st.healthy is False
    await sup.aclose()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_vpn_supervisor.py -k probe_now -v`
Expected: FAIL — `AttributeError: 'VpnSupervisor' object has no attribute 'probe_now'`.

- [ ] **Step 3: Implement `probe_now()`**

In `backend/app/services/vpn_supervisor.py`, add this method to the `VpnSupervisor` class, right after `disable()` (before `status()`):

```python
    async def probe_now(self) -> VpnStatus:
        """Force an immediate health re-probe (user-driven 'Retry').

        The health loop already re-probes every ``health_interval_s`` and
        ``_supervise`` auto-respawns a dead proc; this exposes the same probe
        on demand so the UI Retry isn't a no-op. Does NOT bounce the tunnel —
        a wedged proc is auto-respawned; a deliberate fresh tunnel is
        disable()+enable(). Best-effort, mirroring ``_health_loop``.
        """
        async with self._lock:
            if self._proc is not None:
                try:
                    self._healthy = await self._probe_health()
                except Exception:  # noqa: BLE001 — probe is best-effort
                    self._healthy = False
            else:
                self._healthy = False
            return self.status()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/test_vpn_supervisor.py -v`
Expected: PASS (all, including the four new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/vpn_supervisor.py tests/unit/test_vpn_supervisor.py
git commit -m "feat(vpn): add VpnSupervisor.probe_now() on-demand health re-probe

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `POST /api/vpn/retry` route

**Files:**
- Modify: `backend/app/routes/vpn.py`
- Test: `tests/integration/test_vpn_retry_route.py` (create) + `tests/integration/test_vpn_routes.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_vpn_routes.py` (reuses its `_make_app`):

```python
def test_retry_unmanaged_409(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        r = client.post("/api/vpn/retry")
    assert r.status_code == 409
```

Create `tests/integration/test_vpn_retry_route.py`:

```python
"""POST /api/vpn/retry forces an on-demand VPN health re-probe and returns the
chip partial with a toast. Managed-only (409 when unmanaged)."""

import importlib

from fastapi.testclient import TestClient

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


class FakeSupervisor:
    """Minimal vpn_supervisor stub: probe_now() flips healthy to the seeded value."""

    def __init__(self, healthy_after_probe):
        self._healthy = False
        self._after = healthy_after_probe
        self.probe_called = False

    def status(self):
        from backend.app.services.vpn_supervisor import VpnStatus
        return VpnStatus(managed=True, desired="on",
                         process_running=True, healthy=self._healthy)

    async def probe_now(self):
        self.probe_called = True
        self._healthy = self._after
        return self.status()


def test_retry_managed_reprobes_and_returns_chip(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        sup = FakeSupervisor(healthy_after_probe=True)
        install_live_ctx(client.app, vpn_supervisor=sup)
        r = client.post("/api/vpn/retry", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert sup.probe_called is True
        assert "HX-Trigger" in r.headers              # toast bridge
        # Inner chip partial came back (it renders the VPN row name).
        assert "VPN" in r.text


def test_retry_still_unreachable_toasts(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        sup = FakeSupervisor(healthy_after_probe=False)
        install_live_ctx(client.app, vpn_supervisor=sup)
        r = client.post("/api/vpn/retry", headers={"HX-Request": "true"})
        assert r.status_code == 200
        assert sup.probe_called is True
        assert "HX-Trigger" in r.headers
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_vpn_retry_route.py tests/integration/test_vpn_routes.py::test_retry_unmanaged_409 -v`
Expected: FAIL — 404 (route not registered) / 405.

- [ ] **Step 3: Implement the route**

In `backend/app/routes/vpn.py`, add after the `disable()` route. The `_supervisor`, `_reply`, and `_toast` helpers already exist in this file:

```python
@router.post("/retry")
async def retry(request: Request):
    sup = _supervisor(request)
    if sup is None:
        raise HTTPException(409, "VPN not managed on this deployment")
    st = await sup.probe_now()
    msg = "VPN reachable." if st.healthy else "VPN still unreachable."
    level = "success" if st.healthy else "error"
    return await _reply(request, sup, headers=_toast(msg, level))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_vpn_retry_route.py tests/integration/test_vpn_routes.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/vpn.py tests/integration/test_vpn_retry_route.py tests/integration/test_vpn_routes.py
git commit -m "feat(vpn): POST /api/vpn/retry forces on-demand re-probe

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Success toasts on CatDV connect / disconnect

**Files:**
- Modify: `backend/app/routes/connection.py`
- Test: `tests/integration/test_routes_connect_disconnect.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/integration/test_routes_connect_disconnect.py` (reuses `_make_app`, `FakeClient`, `_install`):

```python
def test_connect_success_emits_toast(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        _install(client.app, c)
        r = client.post("/api/connection/connect")
        assert r.status_code == 200
        assert "HX-Trigger" in r.headers
        assert "CatDV connected" in r.headers["HX-Trigger"]


def test_disconnect_success_emits_toast(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app) as client:
        c = FakeClient()
        c._logged_in = True
        _install(client.app, c)
        r = client.post("/api/connection/disconnect")
        assert r.status_code == 200
        assert "HX-Trigger" in r.headers
        assert "CatDV disconnected" in r.headers["HX-Trigger"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_connect_disconnect.py -k "emits_toast" -v`
Expected: FAIL — `KeyError: 'HX-Trigger'` (success path sends no toast today).

- [ ] **Step 3: Implement the toast headers**

In `backend/app/routes/connection.py`, the `_toast_header(message, level="error")` helper already exists. Update the success returns:

In `connect()`, change the final success line:

```python
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(request, monitor)
```

to:

```python
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(
        request, monitor,
        headers=_toast_header("CatDV connected — browsing live data.", "success"),
    )
```

In `disconnect()`, change:

```python
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(request, monitor)
```

to:

```python
    if monitor is not None:
        await monitor.probe_once()
    return await _pill_or_json(
        request, monitor,
        headers=_toast_header("CatDV disconnected — back to cached clips.", "info"),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_connect_disconnect.py -v`
Expected: PASS (new toast tests + the existing connect/disconnect tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/routes/connection.py tests/integration/test_routes_connect_disconnect.py
git commit -m "feat(connection): success toasts on CatDV connect/disconnect

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Rewrite inner partial — derivation + Status pill

This task replaces `_connection_chip_inner.html` with the pill (and a placeholder for the dropdown, filled in Task 5). It also guards an injected `vpn` so tests and the dropdown can render states directly.

**Files:**
- Modify: `backend/app/templates/_connection_chip_inner.html`
- Test: `tests/integration/test_connection_chip_render.py`

- [ ] **Step 1: Write the failing tests**

Replace the body of `tests/integration/test_connection_chip_render.py` with the following (the old `_render(mode)` is widened to also inject a `vpn` stub). Keep the file's module docstring.

```python
from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _vpn(managed=True, desired="on", healthy=True, process_running=True):
    return SimpleNamespace(managed=managed, desired=desired,
                           healthy=healthy, process_running=process_running)


def _render(mode="disconnected", vpn=None, connect_mode="manual"):
    tmpl = templates.get_template("_connection_chip.html")
    return tmpl.render(mode=mode, vpn=vpn, connect_mode=connect_mode, request=None)


# ---- overall pill ----

def test_pill_online_when_vpn_healthy_and_catdv_online():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "Online" in html
    assert "All connected" in html
    assert "is-online" in html


def test_pill_offline_vpn_off():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "VPN off" in html
    assert "is-offline" in html


def test_pill_error_vpn_unreachable():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "VPN unreachable" in html
    assert "is-error" in html


def test_pill_catdv_disconnected_when_vpn_up():
    html = _render(mode="disconnected", vpn=_vpn(healthy=True))
    assert "CatDV disconnected" in html


def test_pill_catdv_unreachable_when_vpn_up():
    html = _render(mode="offline", vpn=_vpn(healthy=True))
    assert "CatDV unreachable" in html


def test_pill_is_popover_trigger():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert 'class="conn-pill"' in html
    assert '@click="toggle()"' in html


def test_chip_self_polls():
    assert 'hx-get="/ui/connection-chip"' in _render()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: FAIL — current partial has no `conn-pill` / `is-online` / "All connected".

- [ ] **Step 3: Rewrite the inner partial (pill section)**

Replace the entire contents of `backend/app/templates/_connection_chip_inner.html` with the following. (Task 5 fills the dropdown body where marked.) This guards `vpn` like `mode`, derives overall status, and emits the pill. The `state-{key}` class (`is-online` / `is-connecting` / `is-offline` / `is-error`) gates colour in CSS.

```jinja
{# Inner content of the topbar connection chip: the Status pill trigger +
   the dropdown panel. Swapped into the stable #connection-chip container's
   innerHTML by the 5s poll and by connect/disconnect/vpn/retry actions; the
   container owns x-data="popover()" so `open` survives swaps. Rendered
   standalone it computes mode/vpn from the request; callers may inject `mode`
   and `vpn` (a VpnStatus-shaped object or None) to render a specific state. #}
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
{%- if vpn is not defined -%}
  {%- set _live2 = request.app.state.live_ctx if request is defined and request else None -%}
  {%- set _sup = _live2.vpn_supervisor if _live2 else None -%}
  {%- set vpn = _sup.status() if _sup else None -%}
{%- endif -%}

{# ── derivations ─────────────────────────────────────────────────────── #}
{%- set vpn_managed = vpn and vpn.managed -%}
{%- set vpn_up = (not vpn_managed) or (vpn.desired == "on" and vpn.healthy) -%}
{%- set vpn_off = vpn_managed and vpn.desired == "off" -%}
{%- set vpn_err = vpn_managed and vpn.desired == "on" and not vpn.healthy -%}
{%- set catdv_online = mode == "online" -%}
{%- set catdv_err = mode == "offline" or mode == "forced_offline" -%}

{%- if vpn_up and catdv_online -%}
  {%- set ov_key, ov_label, ov_state = "online", "Online", "is-online" -%}
{%- elif vpn_err -%}
  {%- set ov_key, ov_label, ov_state = "error", "Offline", "is-error" -%}
{%- elif vpn_off -%}
  {%- set ov_key, ov_label, ov_state = "offline", "Offline", "is-offline" -%}
{%- elif catdv_err -%}
  {%- set ov_key, ov_label, ov_state = "error", "Offline", "is-error" -%}
{%- else -%}
  {%- set ov_key, ov_label, ov_state = "offline", "Offline", "is-offline" -%}
{%- endif -%}

{%- if vpn_off -%}{%- set ov_sub = "VPN off" -%}
{%- elif vpn_err -%}{%- set ov_sub = "VPN unreachable" -%}
{%- elif mode == "forced_offline" -%}{%- set ov_sub = "Offline (forced)" -%}
{%- elif catdv_online -%}{%- set ov_sub = "All connected" -%}
{%- elif catdv_err -%}{%- set ov_sub = "CatDV unreachable" -%}
{%- else -%}{%- set ov_sub = "CatDV disconnected" -%}
{%- endif -%}

<button type="button" class="conn-pill {{ ov_state }}" @click="toggle()"
        :class="open && 'open'" title="Connection status — click for details">
  <span class="conn-dot" aria-hidden="true"></span>
  <span class="conn-lbl">{{ ov_label }}</span>
  <span class="conn-sep" aria-hidden="true"></span>
  <span class="conn-sub">{{ ov_sub }}</span>
  <span class="conn-cv" aria-hidden="true">▾</span>
  <span class="htmx-indicator conn-spin" aria-hidden="true"></span>
</button>

<div class="popover-panel conn-dropdown align-right {{ ov_state }}"
     x-show="open" x-cloak
     @click.outside="close()" @keydown.escape.window="close()">
  {# DROPDOWN BODY — filled in Task 5 #}
  <div class="conn-head"><span class="conn-head-t">Connection</span>
    <span class="conn-head-s">{{ ov_label }}</span></div>
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/_connection_chip_inner.html tests/integration/test_connection_chip_render.py
git commit -m "feat(ui): connection chip Status pill (overall state derivation)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Dropdown body — VPN row, CatDV row, gate, footer

**Files:**
- Modify: `backend/app/templates/_connection_chip_inner.html`
- Test: `tests/integration/test_connection_chip_render.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/integration/test_connection_chip_render.py`:

```python
# ---- VPN row ----

def test_vpn_row_off_offers_enable_switch():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "VPN tunnel" in html
    assert "/api/vpn/enable" in html


def test_vpn_row_on_offers_disable_switch():
    html = _render(mode="online", vpn=_vpn(desired="on", healthy=True))
    assert "/api/vpn/disable" in html


def test_vpn_row_unreachable_offers_retry():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "/api/vpn/retry" in html
    assert "Retry" in html


def test_vpn_row_hidden_when_unmanaged():
    html = _render(mode="disconnected", vpn=None)
    assert "VPN tunnel" not in html
    assert "CatDV Annotator" in html          # CatDV row still present


# ---- CatDV row ----

def test_catdv_row_connected_offers_disconnect():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "/api/connection/disconnect" in html


def test_catdv_row_disconnected_offers_connect():
    html = _render(mode="disconnected", vpn=_vpn(healthy=True))
    assert "/api/connection/connect" in html


def test_catdv_row_unreachable_offers_retry():
    html = _render(mode="offline", vpn=_vpn(healthy=True))
    assert "/api/connection/retry" in html
    assert "Retry" in html


def test_catdv_row_gated_when_vpn_off():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "Requires VPN" in html
    assert "can only connect once the VPN tunnel is up" in html
    # gated → no live connect action on the CatDV switch
    assert "/api/connection/connect" not in html


def test_catdv_row_gated_when_vpn_unreachable():
    html = _render(mode="disconnected", vpn=_vpn(desired="on", healthy=False))
    assert "Requires VPN" in html


# ---- footer ----

def test_footer_shows_catalog_and_readonly():
    html = _render(mode="online", vpn=_vpn(healthy=True))
    assert "READ-ONLY" in html
    assert "live" in html


def test_footer_cached_when_offline():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert "cached" in html
```

Note `test_footer_shows_catalog_and_readonly` does not assert the catalog id, because with `request=None` the settings lookup is skipped (see footer markup below — it renders the catalog only when `request` is present).

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: FAIL — the dropdown body is still the Task-4 placeholder.

- [ ] **Step 3: Fill the dropdown body**

In `backend/app/templates/_connection_chip_inner.html`, replace the placeholder block:

```jinja
  {# DROPDOWN BODY — filled in Task 5 #}
  <div class="conn-head"><span class="conn-head-t">Connection</span>
    <span class="conn-head-s">{{ ov_label }}</span></div>
```

with:

```jinja
  <div class="conn-head">
    <span class="conn-head-t">Connection</span>
    <span class="conn-head-s">{{ ov_label }}</span>
  </div>

  {%- set _catalog = request.app.state.core_ctx.settings.catdv_catalog_id
        if request is defined and request else None -%}

  {% if vpn_managed %}
  <div class="conn-svc{% if vpn.healthy %} on{% elif vpn_err %} err{% endif %}">
    <span class="conn-svc-ic">{% include "icons/_shield.svg" ignore missing %}</span>
    <div class="conn-svc-body">
      <div class="conn-svc-name">VPN tunnel</div>
      <div class="conn-svc-state">
        {% if vpn_off %}<span class="s-dot off"></span>Off
        {% elif vpn.healthy %}<span class="s-dot on"></span>Connected
        {% else %}<span class="s-dot err"></span>Unreachable{% endif %}
      </div>
    </div>
    {% if vpn_err %}
      <button type="button" class="btn ghost sm conn-retry"
              hx-post="/api/vpn/retry" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip" title="Re-check the tunnel now">Retry</button>
    {% elif vpn.healthy %}
      <button type="button" class="conn-switch on"
              hx-post="/api/vpn/disable" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip"
              hx-confirm="Turn the VPN off? This drops the CatDV connection and cedes the tunnel."
              aria-label="Turn VPN off"></button>
    {% else %}
      <button type="button" class="conn-switch"
              hx-post="/api/vpn/enable" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip" aria-label="Turn VPN on"></button>
    {% endif %}
  </div>
  {% endif %}

  {%- set catdv_gated = vpn_managed and not vpn_up -%}
  <div class="conn-svc dep{% if vpn_up and catdv_online %} linked{% endif %}{% if catdv_online %} on{% elif catdv_err %} err{% endif %}{% if catdv_gated %} disabled{% endif %}">
    <span class="conn-svc-ic">{% include "icons/_server.svg" ignore missing %}</span>
    <div class="conn-svc-body">
      <div class="conn-svc-name">CatDV Annotator</div>
      <div class="conn-svc-state">
        {% if catdv_gated %}<span class="s-dot off"></span>Requires VPN
        {% elif mode == "forced_offline" %}<span class="s-dot err"></span>Offline (forced)
        {% elif catdv_online %}<span class="s-dot on"></span>Connected
        {% elif catdv_err %}<span class="s-dot err"></span>Unreachable
        {% else %}<span class="s-dot off"></span>Disconnected{% endif %}
      </div>
    </div>
    {% if catdv_gated or mode == "forced_offline" %}
      <span class="conn-switch dis" aria-disabled="true"></span>
    {% elif catdv_err %}
      <button type="button" class="btn ghost sm conn-retry"
              hx-post="/api/connection/retry" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip" title="Re-probe CatDV now">Retry</button>
    {% elif catdv_online %}
      <button type="button" class="conn-switch on"
              hx-post="/api/connection/disconnect" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip" aria-label="Disconnect CatDV"></button>
    {% else %}
      <button type="button" class="conn-switch"
              hx-post="/api/connection/connect" hx-target="#connection-chip" hx-swap="innerHTML"
              hx-indicator="#connection-chip" aria-label="Connect CatDV"></button>
    {% endif %}
  </div>

  {% if catdv_gated %}
  <div class="conn-hint">CatDV can only connect once the VPN tunnel is up.</div>
  {% endif %}

  <div class="conn-foot">
    <span class="conn-foot-meta">
      {% if _catalog %}CATALOG {{ _catalog }} · {% endif %}READ-ONLY
    </span>
    <span class="conn-foot-mode">{{ "live" if (vpn_up and catdv_online) else "cached" }}</span>
  </div>
```

Note: the `{% include "icons/_shield.svg" ignore missing %}` / `_server.svg` use `ignore missing` so a missing icon file degrades to an empty tile rather than erroring. Step 3b creates them.

- [ ] **Step 3b: Add the two icons (if absent)**

Check: `ls backend/app/templates/icons/_shield.svg backend/app/templates/icons/_server.svg`. If either is missing, create it.

`backend/app/templates/icons/_shield.svg`:

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6l7-3z"/><path d="M9.2 12l2 2 3.6-4"/></svg>
```

`backend/app/templates/icons/_server.svg`:

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/></svg>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py -v`
Expected: PASS (all pill + dropdown cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/_connection_chip_inner.html backend/app/templates/icons/_shield.svg backend/app/templates/icons/_server.svg tests/integration/test_connection_chip_render.py
git commit -m "feat(ui): connection dropdown — VPN/CatDV rows, gate, footer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Wire the popover container + lifecycle reinit

**Files:**
- Modify: `backend/app/templates/_connection_chip.html`
- Modify: `backend/app/static/htmxAlpine.js`
- Test: `tests/integration/test_connection_chip_render.py` + `tests/unit/test_htmx_alpine_single_lifecycle.py` (must still pass)

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_connection_chip_render.py`:

```python
def test_container_is_popover_with_xdata():
    html = _render(mode="disconnected", vpn=_vpn(desired="off", healthy=False))
    assert 'x-data="popover()"' in html
    assert "popover" in html            # container class
    # the dropdown panel binds to the parent popover scope
    assert 'x-show="open"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py::test_container_is_popover_with_xdata -v`
Expected: FAIL — container has no `x-data="popover()"` yet.

- [ ] **Step 3: Add popover to the stable container**

Replace `backend/app/templates/_connection_chip.html` contents with:

```jinja
{# Topbar connection control. STABLE container: the 5s poll and the
   Connect/Disconnect/Retry/VPN actions swap its innerHTML (the pill trigger +
   dropdown panel live in _connection_chip_inner.html), so the polling element
   is never destroyed. It owns x-data="popover()" so the dropdown's `open`
   state survives every innerHTML swap; htmxAlpine re-inits the swapped subtree
   (see static/htmxAlpine.js htmx:afterSwap branch) so @click/x-show rebind. #}
<div id="connection-chip" class="conn-chip popover" x-data="popover()"
     hx-get="/ui/connection-chip" hx-trigger="every 5s"
     hx-target="this" hx-swap="innerHTML">{% include "_connection_chip_inner.html" %}</div>
```

- [ ] **Step 4: Add the reinit branch to htmxAlpine.js**

In `backend/app/static/htmxAlpine.js`, inside the existing `document.body.addEventListener('htmx:afterSwap', (evt) => { ... })` listener, add this block near the top of the handler (right after the function opens, before the `studio` store lookup):

```javascript
  // Connection chip: the stable #connection-chip container owns
  // x-data="popover()"; its innerHTML (pill trigger + dropdown panel) is
  // swapped by the 5s poll and by connect/disconnect/vpn/retry actions.
  // Re-init the swapped subtree so @click="toggle()" / x-show="open" rebind;
  // the parent popover scope (and its `open` flag) is preserved because
  // initTree skips already-initialized roots.
  if (evt.target && evt.target.id === 'connection-chip') {
    window.Alpine?.initTree(evt.target);
  }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/integration/test_connection_chip_render.py tests/unit/test_htmx_alpine_single_lifecycle.py -v`
Expected: PASS — render test passes AND the single-lifecycle guard still passes (initTree stays inside htmxAlpine.js).

- [ ] **Step 6: Commit**

```bash
git add backend/app/templates/_connection_chip.html backend/app/static/htmxAlpine.js tests/integration/test_connection_chip_render.py
git commit -m "feat(ui): popover() on connection chip + reinit swapped subtree

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Styles — pill, dropdown, switch, service rows

CSS is not unit-tested; verify visually in Task 10. Map every colour to existing tokens.

**Files:**
- Modify: `backend/app/static/app.css`

- [ ] **Step 1: Append the connection-pill styles**

Append to the end of `backend/app/static/app.css`:

```css
/* ============================================================
   CONNECTION PILL + DROPDOWN  (spec 2026-06-12)
   State colours map to existing tokens:
     online → --good, connecting → --accent, error → --bad, offline → --text-3
   ============================================================ */
.conn-chip { position: relative; display: inline-flex; }
.conn-chip.is-online    { --c: var(--good); }
.conn-chip.is-connecting{ --c: var(--accent); }
.conn-chip.is-error     { --c: var(--bad); }
.conn-chip.is-offline   { --c: var(--text-3); }

.conn-pill {
  display: inline-flex; align-items: center; gap: 8px;
  height: 28px; padding: 0 10px;
  border-radius: 999px; border: 1px solid color-mix(in oklab, var(--c) 45%, transparent);
  background: color-mix(in oklab, var(--c) 12%, transparent);
  color: var(--text); font: inherit; line-height: 1; transition: filter .15s;
}
.conn-pill:hover { filter: brightness(1.12); }
.conn-pill .conn-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--c); flex: none; position: relative; }
.conn-pill .conn-lbl { font-weight: 600; font-size: 12.5px; color: var(--c); white-space: nowrap; }
.conn-pill .conn-sub { font-size: 11.5px; color: var(--text-2); white-space: nowrap; }
.conn-pill .conn-sep { width: 1px; height: 13px; background: color-mix(in oklab, var(--c) 45%, transparent); }
.conn-pill .conn-cv  { color: var(--text-3); transition: transform .15s; font-size: 10px; }
.conn-pill.open .conn-cv { transform: rotate(180deg); }

/* connecting pulse on the dot — driven only while a POST is in flight */
.conn-pill .conn-spin { display: none; }
.conn-pill.htmx-request .conn-dot::after {
  content: ''; position: absolute; inset: -4px; border-radius: 50%;
  background: var(--accent); opacity: .5; animation: connPing 1.2s ease-out infinite;
}
@keyframes connPing { 0% { transform: scale(.6); opacity: .55; } 80%, 100% { transform: scale(2.1); opacity: 0; } }

/* dropdown — reuses .popover-panel positioning */
.conn-dropdown { width: 300px; padding: 6px; }
.conn-head { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 6px; }
.conn-head-t { font-size: 11px; letter-spacing: .1em; text-transform: uppercase; color: var(--text-3); }
.conn-head-s { font-size: 12px; font-weight: 600; color: var(--c); }

.conn-svc { display: flex; align-items: center; gap: 10px; padding: 9px 8px; border-radius: 8px; position: relative; }
.conn-svc + .conn-svc { margin-top: 2px; }
.conn-svc.dep::before {
  content: ''; position: absolute; left: 20px; top: -7px; height: 12px; width: 2px;
  background: var(--line-3);
}
.conn-svc.dep.linked::before { background: color-mix(in oklab, var(--good) 55%, transparent); }
.conn-svc-ic {
  width: 28px; height: 28px; border-radius: 8px; flex: none; display: grid; place-items: center;
  background: var(--surface); border: 1px solid var(--line-2); color: var(--text-3);
}
.conn-svc.on  .conn-svc-ic { color: var(--good); border-color: color-mix(in oklab, var(--good) 45%, transparent); background: color-mix(in oklab, var(--good) 12%, transparent); }
.conn-svc.err .conn-svc-ic { color: var(--bad);  border-color: color-mix(in oklab, var(--bad) 45%, transparent);  background: color-mix(in oklab, var(--bad) 12%, transparent); }
.conn-svc-ic svg { width: 16px; height: 16px; }
.conn-svc-body { flex: 1; min-width: 0; }
.conn-svc-name { font-size: 13px; font-weight: 600; color: var(--text); }
.conn-svc-state { font-size: 11.5px; margin-top: 1px; display: flex; align-items: center; gap: 6px; color: var(--text-2); }
.conn-svc.disabled .conn-svc-name, .conn-svc.disabled .conn-svc-state { color: var(--text-3); }
.conn-svc-state .s-dot { width: 6px; height: 6px; border-radius: 50%; flex: none; }
.conn-svc-state .s-dot.on  { background: var(--good); }
.conn-svc-state .s-dot.err { background: var(--bad); }
.conn-svc-state .s-dot.off { background: var(--text-3); }

/* toggle switch (HTMX <button>, not a *-btn class) */
.conn-switch { width: 36px; height: 21px; border-radius: 999px; flex: none; position: relative; cursor: pointer;
  background: var(--surface); border: 1px solid var(--line-3); transition: .15s; padding: 0; }
.conn-switch::after { content: ''; position: absolute; top: 2px; left: 2px; width: 15px; height: 15px;
  border-radius: 50%; background: var(--text-3); transition: .15s; }
.conn-switch.on { background: color-mix(in oklab, var(--good) 18%, transparent); border-color: color-mix(in oklab, var(--good) 45%, transparent); }
.conn-switch.on::after { left: 17px; background: var(--good); }
.conn-switch.dis { opacity: .4; cursor: not-allowed; }

.conn-retry { gap: 5px; }
.conn-hint { font-size: 11px; color: var(--text-3); padding: 2px 8px 6px; }
.conn-foot { display: flex; align-items: center; justify-content: space-between;
  padding: 8px; margin-top: 4px; border-top: 1px solid var(--line); }
.conn-foot-meta { font-family: var(--f-mono); font-size: 10.5px; letter-spacing: .04em; color: var(--text-3); text-transform: uppercase; }
.conn-foot-mode { font-family: var(--f-mono); font-size: 10.5px; color: var(--text-3); }
.conn-dropdown.is-online .conn-foot-mode { color: var(--good); }
```

- [ ] **Step 2: Sanity-check the CSS parses (no test, quick grep)**

Run: `.venv/bin/python -c "import pathlib,re; s=pathlib.read_text if False else open('backend/app/static/app.css').read(); print('braces balanced:', s.count('{')==s.count('}'))"`
Expected: `braces balanced: True`

- [ ] **Step 3: Commit**

```bash
git add backend/app/static/app.css
git commit -m "feat(ui): styles for connection pill, dropdown, toggle switch

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: Topbar consolidation — remove CATALOG + READ-ONLY pills

**Files:**
- Modify: `backend/app/templates/pages/_topbar_pills.html`
- Test: `tests/integration/` — add a small render assertion (new file).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_topbar_pills_consolidation.py`:

```python
"""CATALOG + READ-ONLY moved into the connection dropdown footer; the standalone
topbar env-pills for them are gone. The DEV env pill stays."""

import importlib

from fastapi.testclient import TestClient


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


def test_topbar_pills_has_no_standalone_catalog_or_readonly():
    from backend.app.routes.pages.templates import templates
    html = templates.get_template("pages/_topbar_pills.html").render(
        request=None,
    ) if False else None
    # Render through the macro file directly is awkward (needs request); assert
    # on the source instead — the standalone env-pills must be removed.
    src = open("backend/app/templates/pages/_topbar_pills.html").read()
    assert 'CATALOG {{ _settings.catdv_catalog_id }}' not in src
    assert '>READ-ONLY<' not in src
```

(The `if False else None` keeps the import line as documentation of why a full render isn't used here — the partial reads `request.app.state`, so we assert on source.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_topbar_pills_consolidation.py -v`
Expected: FAIL — both pills still present in source.

- [ ] **Step 3: Remove the two pills**

In `backend/app/templates/pages/_topbar_pills.html`, delete these two lines (the last two `env-pill` spans), keeping the `DEV · {{ request.url.netloc }}` pill:

```jinja
  <span class="env-pill">CATALOG {{ _settings.catdv_catalog_id }}</span>
  <span class="env-pill">READ-ONLY</span>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_topbar_pills_consolidation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/templates/pages/_topbar_pills.html tests/integration/test_topbar_pills_consolidation.py
git commit -m "feat(ui): move CATALOG/READ-ONLY into connection dropdown footer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: ADR + decisions index

**Files:**
- Create: `docs/adr/NNNN-vpn-on-demand-reprobe-and-connection-pill.md`
- Modify: `docs/decisions.md`

- [ ] **Step 1: Determine the next ADR number**

Run: `ls docs/adr/ | grep -E '^[0-9]+' | sort | tail -3`
Use one higher than the highest. Substitute for `NNNN` (zero-padded to 4) below.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/NNNN-vpn-on-demand-reprobe-and-connection-pill.md`:

```markdown
# NNNN. Connection pill redesign + on-demand VPN re-probe

**Date:** 2026-06-12
**Status:** Accepted

## Context

The topbar connection chip was a flat row of dots + text buttons. The
connectivity prototype (Claude Design handoff) redesigns it as a Status pill
that opens a dropdown with one row per service (VPN tunnel, CatDV Annotator),
toggle switches, a VPN→CatDV dependency gate, and Retry affordances. The
backend connect/disconnect is synchronous request→response with no persistent
"connecting" state, and VPN health is only re-probed on the supervisor's timer
(`_health_loop`) — there was no way for a user to force a re-check.

## Alternatives

- **Optimistic client-side connecting state** — rejected: it would show a state
  the server doesn't hold; we represent "connecting" via the in-flight HTMX
  request only.
- **Wire the VPN Retry to `/api/vpn/enable`** — rejected: `enable()` is a no-op
  when `desired` is already `on`, so the button would lie.
- **Make Retry bounce the tunnel** — rejected: a wedged proc is already
  auto-respawned by `_supervise`; a deliberate fresh tunnel is disable()+enable().

## Decision

- Add `VpnSupervisor.probe_now()` — an on-demand, lock-guarded, best-effort
  re-probe (same probe the health loop runs) — and `POST /api/vpn/retry` that
  calls it and returns the chip partial with a toast.
- Rewrite `_connection_chip_inner.html` as the pill + dropdown, deriving all
  state from `vpn_supervisor.status()` + `connection_monitor` mode. The stable
  `#connection-chip` container owns `x-data="popover()"`; htmxAlpine re-inits the
  swapped subtree so the dropdown survives the 5s poll.
- Surface attempt-time failures (seat busy, login rejected) as toasts, with the
  row falling back to its Connect action — no invented persistent state.
- Move CATALOG + READ-ONLY from standalone topbar pills into the dropdown footer.

## Consequences

- The VPN Retry is honest (forces a real re-probe) without restarting the
  tunnel, preserving the seat/VPN discipline.
- One new external-action endpoint; mirrors the existing enable/disable shape
  (409 when unmanaged, chip partial + toast on success).
- All seat-release rules are unchanged: disconnect = logout, VPN disable = logout
  then drop tunnel.
```

- [ ] **Step 3: Index it in decisions.md**

Add a row to the table in `docs/decisions.md` (match the existing column shape; check the file first):

```markdown
| NNNN | Connection pill redesign + on-demand VPN re-probe | Accepted | 2026-06-12 |
```

- [ ] **Step 4: Commit**

```bash
git add docs/adr/NNNN-vpn-on-demand-reprobe-and-connection-pill.md docs/decisions.md
git commit -m "docs(adr): connection pill redesign + VPN on-demand re-probe

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Full verification + manual acceptance

**Files:** none (verification only).

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions). Pay attention to `test_design_language_guard.py`, `test_htmx_alpine_single_lifecycle.py`, `test_templates_shared.py`, and the clips-page perf guards.

- [ ] **Step 2: Run the import-linter contracts**

Run: `.venv/bin/lint-imports`
Expected: contracts pass (routes don't import httpx; repos don't import services). The new route only touches the supervisor/templates, so this should be unaffected — confirm.

- [ ] **Step 3: Design-language guard explicitly**

Run: `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py -v`
Expected: PASS. The new classes (`conn-pill`, `conn-dropdown`, `conn-svc`, `conn-switch`, `conn-retry`) do not end in `btn`/`menu`, introduce no `modal-*` or `*-card`, and the Retry buttons use `.btn ghost sm`. If the guard trips, route the offending class onto `.btn`/`popover` per the assert message.

- [ ] **Step 4: Manual acceptance — start the server**

Use the `server-start` skill (single-instance + graceful-shutdown discipline; port 8765). Then walk the 9 flows in the spec's "Manual acceptance flows" section. Minimum to tick before calling done:
  - Flow 1 happy path (VPN off → on → CatDV on → pill "Online · All connected", footer "live").
  - Flow 2 seat release (CatDV off → toast + footer "cached"; confirm `DELETE …/session` in the server log).
  - Flow 3 VPN gate (CatDV switch disabled + hint when VPN off).
  - Flow 5 VPN Retry (unreachable → Retry → re-probe; does NOT bounce tunnel).
  - Flow 7 local dev (VPN unmanaged → only CatDV row).
  - Flow 8 dropdown survives the 5s poll while open.
  - Flow 9 CATALOG/READ-ONLY gone from topbar, present in footer.

Note: locally `vpn.managed=false`, so flows 1/2/5 (VPN rows) need the cloud/managed config or a stubbed supervisor; verify the VPN-row logic via the render tests (Tasks 4–5) if a managed VPN isn't available in your env, and exercise flows 2/3(CatDV)/7/8/9 locally.

- [ ] **Step 5: Stop the server gracefully**

Use the `server-stop` skill (SIGTERM only; confirm the "Application shutdown complete" seat-release line). Never `kill -9`.

- [ ] **Step 6: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: verification fixups for connection pill redesign

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review notes (author)

- **Spec coverage:** pill (T4), dropdown rows + gate + footer (T5), VPN Retry endpoint + probe_now (T1/T2), success toasts (T3), popover lifecycle (T6), CSS (T7), topbar consolidation (T8), ADR (T9), manual flows (T10). All spec sections mapped.
- **VPN-managed in tests:** render tests inject a `vpn` stub (SimpleNamespace) so the managed two-row layout is covered without a real WireGuard tunnel; the route test stubs `vpn_supervisor`.
- **Guard safety:** new class names avoid `*-btn`/`*-menu`/`modal-*`/`*-card`; `initTree` stays in `htmxAlpine.js`; reuses `popover()` + `.popover-panel`.
- **Honest states:** no persistent "seat busy"/"connecting" server state invented; both come from toasts / in-flight indicator respectively, per the approved spec.
```
