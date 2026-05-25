# Shutdown Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a topbar "Shut down" button that gracefully stops the server from the browser, releasing the CatDV license seat without a terminal.

**Architecture:** A `POST /api/connection/shutdown` endpoint schedules a self-`SIGTERM` (uvicorn's own graceful-shutdown trigger) shortly after the HTTP response flushes. uvicorn's existing signal handler runs the FastAPI lifespan shutdown → `AppContext.aclose()`, which stops the connection monitor *before* calling `CatdvClient.logout()` so the seat can't be re-grabbed. The browser receives a full-screen "shutting down" screen that polls `/api/health` until the process is gone. Under `--reload` (dev) the button is disabled because the reloader could respawn the worker.

**Tech Stack:** FastAPI, Jinja2 templates, HTMX 1.9, vanilla JS, pydantic-settings, pytest + `fastapi.testclient.TestClient`.

**Reference spec:** `docs/specs/2026-05-25-shutdown-button-design.md`

---

## File Structure

**Create:**
- `backend/app/shutdown.py` — the graceful-shutdown trigger seam (`request_graceful_shutdown`, `schedule_graceful_shutdown`). Isolated so tests can replace it without signalling pytest.
- `backend/app/templates/_shutdown_screen.html` — full-screen "shutting down" takeover + the `/api/health` poller script.
- `backend/app/templates/icons/_power.svg` — power-glyph icon for the button.
- `tests/integration/test_routes_shutdown.py` — endpoint behaviour + reload gating.
- `tests/unit/test_aclose_ordering.py` — regression guard: logout runs after the monitor is stopped.

**Modify:**
- `backend/app/settings.py` — add `dev_reload: bool = False` (mapped from `DEV_RELOAD`).
- `backend/app/routes/connection.py` — add `POST /shutdown`.
- `backend/app/templates/pages/_topbar_pills.html` — add the button (enabled / disabled-under-reload variants).
- `backend/app/static/app.css` — `.shutdown-btn` + `.shutdown-screen` styles.
- `backend/app/services/catdv_client.py` — log a WARNING when `DELETE /session` fails instead of swallowing it.
- `tests/integration/test_catdv_client_logout.py` — add the logout-failure WARNING test.
- `docs/adr/0024-shutdown-button.md` (create) + `docs/decisions.md` (index row).

---

## Task 1: Settings flag for reload detection

**Files:**
- Modify: `backend/app/settings.py:48-49` (insert near the connection-monitor block)
- Test: `tests/unit/test_settings_dev_reload.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_settings_dev_reload.py`:

```python
from backend.app.settings import Settings


def _base_env(monkeypatch):
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_CATALOG_ID", "1")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")


def test_dev_reload_defaults_false(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.delenv("DEV_RELOAD", raising=False)
    assert Settings(_env_file=None).dev_reload is False


def test_dev_reload_reads_env(monkeypatch):
    _base_env(monkeypatch)
    monkeypatch.setenv("DEV_RELOAD", "1")
    assert Settings(_env_file=None).dev_reload is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_settings_dev_reload.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'dev_reload'`

- [ ] **Step 3: Add the field**

In `backend/app/settings.py`, in the `# connection monitor` block (after line 50), add:

```python
    # connection monitor
    health_probe_interval_s: int = 30
    health_probe_timeout_s: int = 5

    # set by run.sh when launching uvicorn with --reload; disables the
    # in-app shutdown button (the reloader supervisor may respawn the worker)
    dev_reload: bool = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_settings_dev_reload.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/settings.py tests/unit/test_settings_dev_reload.py
git commit -m "feat(settings): add dev_reload flag for shutdown-button gating"
```

---

## Task 2: Shutdown trigger seam

**Files:**
- Create: `backend/app/shutdown.py`
- Test: `tests/unit/test_shutdown_seam.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_shutdown_seam.py`:

```python
import asyncio

import backend.app.shutdown as shutdown_mod


def test_request_graceful_shutdown_sends_sigterm(monkeypatch):
    sent = []
    monkeypatch.setattr(shutdown_mod.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    shutdown_mod.request_graceful_shutdown()
    assert sent == [(shutdown_mod.os.getpid(), shutdown_mod.signal.SIGTERM)]


def test_schedule_graceful_shutdown_defers_via_loop():
    async def run():
        fired = []
        # Replace the trigger so nothing actually signals the test process.
        import backend.app.shutdown as m
        orig = m.request_graceful_shutdown
        m.request_graceful_shutdown = lambda: fired.append(True)
        try:
            m.schedule_graceful_shutdown(delay_s=0.01)
            assert fired == []          # deferred, not immediate
            await asyncio.sleep(0.05)
            assert fired == [True]      # fired after the delay
        finally:
            m.request_graceful_shutdown = orig

    asyncio.run(run())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_shutdown_seam.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.app.shutdown'`

- [ ] **Step 3: Write the module**

Create `backend/app/shutdown.py`:

```python
"""Graceful-shutdown trigger seam.

Isolated in its own module so the shutdown route can fire it and tests can
replace it without signalling the pytest process. The real implementation
sends SIGTERM to our own process — uvicorn's documented graceful-shutdown
trigger, the same path as `kill -TERM` / Ctrl-C. uvicorn then runs the
FastAPI lifespan shutdown, which releases the CatDV seat in
`AppContext.aclose()`.
"""

from __future__ import annotations

import asyncio
import os
import signal


def request_graceful_shutdown() -> None:
    """Send SIGTERM to our own process to start uvicorn's graceful shutdown."""
    os.kill(os.getpid(), signal.SIGTERM)


def schedule_graceful_shutdown(delay_s: float = 0.5) -> None:
    """Defer the SIGTERM by `delay_s` so the HTTP response flushes first.

    The trigger is looked up as a module global at fire time, so tests that
    swap `request_graceful_shutdown` are honoured.
    """
    loop = asyncio.get_running_loop()
    loop.call_later(delay_s, request_graceful_shutdown)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_shutdown_seam.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/app/shutdown.py tests/unit/test_shutdown_seam.py
git commit -m "feat(shutdown): add graceful-shutdown trigger seam"
```

---

## Task 3: Shutdown endpoint

**Files:**
- Modify: `backend/app/routes/connection.py` (add route + import)
- Create: `backend/app/templates/_shutdown_screen.html`
- Test: `tests/integration/test_routes_shutdown.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_routes_shutdown.py`:

```python
import importlib

from fastapi.testclient import TestClient


def _setenv(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("CATDV_BASE_URL", "http://localhost:0")
    monkeypatch.setenv("CATDV_USERNAME", "")
    monkeypatch.setenv("CATDV_PASSWORD", "p")
    monkeypatch.setenv("CATDV_CATALOG_ID", "881507")
    monkeypatch.setenv("GCP_PROJECT_ID", "p")
    monkeypatch.setenv("GCS_BUCKET_NAME", "b")
    monkeypatch.setenv("PROXY_SOURCE", "rest")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _make_app(monkeypatch, tmp_path):
    _setenv(monkeypatch, tmp_path)
    from backend.app import main as main_mod

    importlib.reload(main_mod)
    return main_mod.app


def _patch_trigger(monkeypatch):
    calls = []
    import backend.app.routes.connection as conn_mod

    monkeypatch.setattr(
        conn_mod, "schedule_graceful_shutdown", lambda *a, **k: calls.append(True)
    )
    return calls


def test_shutdown_returns_screen_and_fires_trigger(monkeypatch, tmp_path):
    monkeypatch.delenv("DEV_RELOAD", raising=False)
    app = _make_app(monkeypatch, tmp_path)
    calls = _patch_trigger(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/connection/shutdown")
    assert r.status_code == 200
    assert "Shutting down" in r.text
    assert calls == [True]


def test_shutdown_refused_in_reload_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_RELOAD", "1")
    app = _make_app(monkeypatch, tmp_path)
    calls = _patch_trigger(monkeypatch)
    with TestClient(app) as client:
        r = client.post("/api/connection/shutdown")
    assert r.status_code == 409
    assert calls == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_routes_shutdown.py -v`
Expected: FAIL — `404 Not Found` (route doesn't exist; both asserts fail)

- [ ] **Step 3: Create the screen template**

Create `backend/app/templates/_shutdown_screen.html`:

```html
{# Full-screen takeover shown after the shutdown POST. Polls /api/health
   until the connection is refused (process gone), then reports the seat
   released. window.close() only works for script-opened tabs/PWAs; the
   message is the reliable fallback. #}
<div class="shutdown-screen" id="shutdown-screen">
  <div class="shutdown-card">
    <div class="shutdown-spinner" aria-hidden="true"></div>
    <h1 id="shutdown-title">Shutting down…</h1>
    <p id="shutdown-msg">Releasing the CatDV seat and stopping the server.</p>
  </div>
  <script>
    (function () {
      var title = document.getElementById('shutdown-title');
      var msg = document.getElementById('shutdown-msg');
      var screen = document.getElementById('shutdown-screen');
      var start = Date.now();
      function stopped() {
        screen.classList.add('shutdown-screen--done');
        title.textContent = 'Stopped';
        msg.textContent = 'CatDV seat released. You can close this tab.';
        try { window.close(); } catch (e) {}
      }
      function poll() {
        fetch('/api/health', { cache: 'no-store' })
          .then(function () {
            if (Date.now() - start > 15000) {
              msg.textContent = 'Taking longer than expected — still shutting down…';
            }
            setTimeout(poll, 500);
          })
          .catch(function () { stopped(); });
      }
      setTimeout(poll, 500);
    })();
  </script>
</div>
```

- [ ] **Step 4: Add the route**

In `backend/app/routes/connection.py`, add the import near the top (after the existing `from backend.app.deps import get_ctx`):

```python
from backend.app.shutdown import schedule_graceful_shutdown
```

Then add this route (place it after the `retry_now` handler, before `set_offline`):

```python
@router.post("/shutdown")
async def shutdown(request: Request):
    """Release the CatDV seat and stop the server.

    Schedules a self-SIGTERM a beat after the response flushes; uvicorn's
    graceful shutdown then runs the lifespan teardown (AppContext.aclose),
    which stops the connection monitor before logging out so the seat can't
    be re-grabbed. Refused under --reload (the reloader may respawn us).
    """
    ctx = get_ctx(request)
    if getattr(ctx.settings, "dev_reload", False):
        raise HTTPException(
            status_code=409,
            detail="shutdown disabled in reload mode; stop with Ctrl-C",
        )
    schedule_graceful_shutdown()
    return _templates.TemplateResponse(request, "_shutdown_screen.html", {})
```

(`Request`, `HTTPException`, and `_templates` are already imported in this module.)

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_routes_shutdown.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add backend/app/routes/connection.py backend/app/templates/_shutdown_screen.html tests/integration/test_routes_shutdown.py
git commit -m "feat(shutdown): add POST /api/connection/shutdown endpoint + screen"
```

---

## Task 4: Topbar button + styles

**Files:**
- Create: `backend/app/templates/icons/_power.svg`
- Modify: `backend/app/templates/pages/_topbar_pills.html`
- Modify: `backend/app/static/app.css` (append after the `.pillset` block, line ~108)

No automated test (server-rendered fragment; verified manually in Task 6). This task is markup/CSS only.

- [ ] **Step 1: Create the icon**

Create `backend/app/templates/icons/_power.svg`:

```html
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
     stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 2v10"></path>
  <path d="M18.4 6.6a9 9 0 1 1-12.8 0"></path>
</svg>
```

- [ ] **Step 2: Add the button to the pillset**

In `backend/app/templates/pages/_topbar_pills.html`, add the button inside the `<span class="pillset">`, immediately after the `{% include "_connection_chip.html" %}` line:

```html
<span class="pillset">
  {% include "_connection_chip.html" %}
  {% if _settings.dev_reload %}
    <button type="button" class="shutdown-btn" disabled
            title="Reload mode — stop with Ctrl-C in the terminal">
      {% include "icons/_power.svg" %}<span>Shut down</span>
    </button>
  {% else %}
    <button type="button" class="shutdown-btn"
            hx-post="/api/connection/shutdown"
            hx-target="body" hx-swap="innerHTML"
            hx-confirm="Shut down the annotator and release the CatDV seat?"
            title="Stop the server and release the CatDV seat">
      {% include "icons/_power.svg" %}<span>Shut down</span>
    </button>
  {% endif %}
  <span class="env-pill ok"><span class="led"></span>DEV · {{ request.url.netloc }}</span>
  <span class="env-pill">CATALOG {{ _settings.catdv_catalog_id }}</span>
  <span class="env-pill">READ-ONLY</span>
</span>
```

(`_settings` is already defined at the top of this template.)

- [ ] **Step 3: Add styles**

In `backend/app/static/app.css`, after the `.pillset { ... }` block (around line 108), append:

```css
.shutdown-btn {
  display: inline-flex; align-items: center; gap: 5px;
  height: 22px; padding: 0 8px; border-radius: 11px;
  border: 1px solid var(--line-2);
  background: transparent;
  font-family: var(--f-mono);
  font-size: 10.5px; letter-spacing: 0.04em;
  color: var(--text-2);
  text-transform: uppercase;
  cursor: pointer;
}
.shutdown-btn svg { width: 12px; height: 12px; }
.shutdown-btn:hover:not(:disabled) {
  color: var(--bad);
  border-color: color-mix(in oklab, var(--bad) 45%, transparent);
}
.shutdown-btn:disabled { opacity: 0.4; cursor: not-allowed; }

/* full-screen shutdown takeover */
.shutdown-screen {
  position: fixed; inset: 0; z-index: 9999;
  display: flex; align-items: center; justify-content: center;
  background: var(--bg);
}
.shutdown-card { text-align: center; color: var(--text); font-family: var(--f-sans); }
.shutdown-card h1 { font-size: 18px; margin: 12px 0 6px; }
.shutdown-card p { color: var(--text-2); font-size: 13px; margin: 0; }
.shutdown-spinner {
  width: 28px; height: 28px; margin: 0 auto;
  border: 3px solid var(--line); border-top-color: var(--accent);
  border-radius: 50%;
  animation: shutdown-spin 0.8s linear infinite;
}
.shutdown-screen--done .shutdown-spinner { display: none; }
@keyframes shutdown-spin { to { transform: rotate(360deg); } }
```

- [ ] **Step 4: Commit**

```bash
git add backend/app/templates/icons/_power.svg backend/app/templates/pages/_topbar_pills.html backend/app/static/app.css
git commit -m "feat(shutdown): add topbar shut-down button + screen styles"
```

---

## Task 5: Surface logout failures + lock the aclose ordering

**Files:**
- Modify: `backend/app/services/catdv_client.py:1-12` (add `import logging`), `:76-83` (`logout`)
- Modify: `tests/integration/test_catdv_client_logout.py` (add a test)
- Create: `tests/unit/test_aclose_ordering.py`

- [ ] **Step 1: Write the failing logout-warning test**

Append to `tests/integration/test_catdv_client_logout.py`:

```python
import logging


@pytest.mark.asyncio
async def test_logout_logs_warning_on_delete_failure(caplog):
    with running_fake_catdv() as (base_url, _fake):
        client = CatdvClient(base_url=base_url, username="klientAI", password="secret")
        async with client:
            await client.login()

            async def boom(*args, **kwargs):
                raise RuntimeError("network down")

            client._client.delete = boom  # type: ignore[assignment]
            with caplog.at_level(logging.WARNING):
                await client.logout()
        assert client._logged_in is False
        assert any(
            "seat" in r.message.lower() or "logout" in r.message.lower()
            for r in caplog.records
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_logout.py::test_logout_logs_warning_on_delete_failure -v`
Expected: FAIL — the `RuntimeError` propagates out of `logout()` (no try/except yet)

- [ ] **Step 3: Make logout swallow + warn**

In `backend/app/services/catdv_client.py`, add `import logging` to the imports block (after `import asyncio`):

```python
import asyncio
import logging
```

Replace the `logout` method (lines 76-83) with:

```python
    async def logout(self) -> None:
        """Best-effort DELETE /session so we don't orphan a server-side slot.

        Logs a WARNING (rather than failing silently) if the call errors, so
        a possibly-leaked license seat is at least diagnosable in the journal.
        """
        if self._client is None or not self._logged_in:
            return
        try:
            await self.http.delete(f"{self._base}/catdv/api/9/session")
        except Exception:
            logging.getLogger(__name__).warning(
                "CatDV logout (DELETE /session) failed; the license seat may "
                "remain held until the server times it out",
                exc_info=True,
            )
        finally:
            self._logged_in = False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/integration/test_catdv_client_logout.py -v`
Expected: PASS (all tests in the file, including the new one)

- [ ] **Step 5: Write the aclose-ordering regression test**

Create `tests/unit/test_aclose_ordering.py`:

```python
import pytest

from backend.app.context import AppContext


@pytest.mark.asyncio
async def test_aclose_stops_monitor_before_logout():
    calls: list[str] = []

    class RecStop:
        def __init__(self, name: str) -> None:
            self.name = name

        async def stop(self) -> None:
            calls.append(f"{self.name}.stop")

    class FakeCatdv:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("catdv.logout")

    class FakeDbCm:
        async def __aexit__(self, *exc_info) -> None:
            calls.append("db.close")

    ctx = AppContext(settings=object(), db=object(), db_cm=FakeDbCm())
    ctx.connection_monitor = RecStop("monitor")
    ctx.sync_engine = RecStop("sync")
    ctx.catdv = FakeCatdv()

    await ctx.aclose()

    assert "monitor.stop" in calls and "catdv.logout" in calls
    assert calls.index("monitor.stop") < calls.index("catdv.logout")
    assert calls.index("catdv.logout") < calls.index("db.close")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_aclose_ordering.py -v`
Expected: PASS — `aclose()` already orders monitor-stop → logout → db-close (`context.py`); this test guards against regressions.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/catdv_client.py tests/integration/test_catdv_client_logout.py tests/unit/test_aclose_ordering.py
git commit -m "feat(shutdown): warn on logout failure; guard aclose ordering"
```

---

## Task 6: Manual verification (real app)

**Files:** none (manual).

> **Seat discipline (CLAUDE.md):** check for an existing server first; shut down gracefully when done. This task *is* the graceful-shutdown test, so it ends with the seat released.

- [ ] **Step 1: Confirm no server is already running**

Run:
```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN; /bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
```
Expected: no listener on 8765, no stray uvicorn. If there is one, reuse/stop it first.

- [ ] **Step 2: Start the app**

Run: `./run.sh` (default; `DEV_RELOAD` unset, so the button is enabled).
Open `http://127.0.0.1:8765/` in a browser.

- [ ] **Step 3: Verify the button + confirm dialog**

The topbar shows an enabled "Shut down" button. Click it → a native confirm dialog appears. Cancel → nothing happens, app still running.

- [ ] **Step 4: Verify the shutdown flow**

Click "Shut down" → confirm. The page swaps to the full-screen "Shutting down…" screen. Within ~1s it flips to "Stopped. CatDV seat released. You can close this tab."

In the server terminal, confirm the graceful shutdown lines appear:
```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.   ← seat released
INFO:     Finished server process [...]
```
Confirm there is **no** WARNING about a failed logout (that would mean the seat may be held).

- [ ] **Step 5: Verify the process is gone**

Run:
```bash
/usr/sbin/lsof -nP -iTCP:8765 -sTCP:LISTEN; /bin/ps -ef | /usr/bin/grep -E '(uvicorn|backend\.app)' | /usr/bin/grep -v grep
```
Expected: nothing listening, no uvicorn process. The seat is released.

- [ ] **Step 6: Verify reload mode disables the button**

Run: `DEV_RELOAD=1 ./run.sh`, reload the page. The "Shut down" button is greyed/disabled with the Ctrl-C tooltip. Stop this instance with Ctrl-C in the terminal (and confirm the shutdown-complete lines).

---

## Task 7: ADR + decisions index

**Files:**
- Create: `docs/adr/0024-shutdown-button.md`
- Modify: `docs/decisions.md` (append index row)

- [ ] **Step 1: Write the ADR**

Create `docs/adr/0024-shutdown-button.md`:

```markdown
# 0024. Browser-triggered graceful shutdown (shutdown button)

- **Date:** 2026-05-25
- **Status:** Accepted

## Context

Releasing the CatDV license seat required a terminal: `kill -TERM <pid>`
runs the FastAPI lifespan teardown → `AppContext.aclose()` →
`CatdvClient.logout()` → `DELETE /session`. A leaked seat is held
server-side until idle timeout, locking out the next session (the install
has a 2-seat limit, one usually taken by the human web client). We want to
release the seat and stop the server from the browser.

## Alternatives

- **Standalone "logout" that keeps the app running.** Rejected: the app
  auto-re-authenticates (`_call_json` re-logs in on AUTH; `ConnectionMonitor`
  health-probes every ~30s through the same path), so the seat would be
  re-grabbed within 30s unless we added a re-login suppression latch.
  Logged-out-but-running only helps offline cache browsing — a developer
  activity — so the latch isn't worth it. See [[ADR 0015]], [[ADR 0023]].
- **Owned-server entrypoint** (`uvicorn.Server` in a custom module, set
  `server.should_exit = True`). Rejected: not more bulletproof — uvicorn's
  signal handler literally just sets `should_exit`, so both converge on the
  same `aclose()` path. Costs a new launch module, a systemd `ExecStart`
  change, and losing `--reload` (or maintaining two launch paths).

## Decision

Add `POST /api/connection/shutdown`. It schedules a self-`SIGTERM`
(`os.kill(getpid(), SIGTERM)`) ~0.5s after the response flushes, via an
isolated `backend/app/shutdown.py` seam (so tests can swap it without
signalling pytest). uvicorn's existing signal handler runs the graceful
shutdown. The browser gets a full-screen screen that polls `/api/health`
until the connection is refused, then reports the seat released and tries
`window.close()`.

Safety rests on the existing `aclose()` ordering: stop prefetcher → LRU →
sync engine → **connection monitor → logout → close DB**. The monitor (the
only periodic re-prober) is stopped before logout, and uvicorn refuses new
requests during shutdown, so nothing re-authenticates after the seat is
released. All writes live in the durable `pending_operations` queue, so an
interrupted sync simply retries on next boot — no data loss.

The button is disabled when `DEV_RELOAD` is set: under `uvicorn --reload`
the reloader supervisor may respawn the worker (re-grabbing the seat), and
that developer has a terminal for Ctrl-C anyway. `logout()` now logs a
WARNING on a failed `DELETE /session` instead of swallowing it, so a leaked
seat is diagnosable.

## Consequences

- Operators release the seat from the UI; no terminal needed for the common
  case. Production (systemd `Restart=on-failure`) treats the clean exit as
  success and does not respawn — restart is a manual `systemctl start`.
- `window.close()` only works for script-opened tabs/PWAs; otherwise the
  "you can close this tab" message stands.
- The button is intentionally unavailable under `--reload`.
```

- [ ] **Step 2: Add the index row**

In `docs/decisions.md`, append to the index table (after the 0023 row):

```markdown
| 0024 | 2026-05-25 | [Browser-triggered graceful shutdown (shutdown button)](./adr/0024-shutdown-button.md) |
```

- [ ] **Step 3: Commit**

```bash
git add docs/adr/0024-shutdown-button.md docs/decisions.md
git commit -m "docs(adr): 0024 browser-triggered graceful shutdown"
```

---

## Task 8: Full suite + lint

**Files:** none.

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (including the 5 new test files). No real SIGTERM is sent (the route trigger is monkeypatched in tests).

- [ ] **Step 2: Lint + type-check (matches pre-commit)**

Run: `.venv/bin/ruff format backend tests && .venv/bin/ruff check backend tests && .venv/bin/basedpyright`
Expected: no errors. Fix any formatting/lint issues, then re-run.

- [ ] **Step 3: Commit any lint fixups**

```bash
git add -A
git commit -m "chore(shutdown): formatting + lint"
```
(Skip if nothing changed.)

---

## Self-Review Notes

- **Spec coverage:** endpoint (T3), self-SIGTERM seam (T2), reload gating via `dev_reload` (T1 + T3), button + disabled variant (T4), shutting-down screen + health poll + `window.close()` (T3/T4), logout-failure WARNING (T5), aclose-ordering guarantee (T5 regression test), manual browser verification (T6), ADR (T7). No-data-loss is a property of the existing durable queue + aclose ordering, asserted indirectly by T5 and verified in T6.
- **Test seam:** the route imports `schedule_graceful_shutdown` by name; tests patch `backend.app.routes.connection.schedule_graceful_shutdown`, so no test ever signals the runner.
- **Naming consistency:** `request_graceful_shutdown` / `schedule_graceful_shutdown`, `dev_reload`, `.shutdown-btn`, `.shutdown-screen`, `_shutdown_screen.html`, `_power.svg`, `POST /api/connection/shutdown` are used identically across every task.
```
