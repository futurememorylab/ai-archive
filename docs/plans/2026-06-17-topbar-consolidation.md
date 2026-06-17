# Topbar Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declutter the topbar from 7 stacked right-side elements down to 3 persistent + 2 conditional, fold the email/logout/env/shut-down into one environment-aware user menu, and add a separate "N to review" indicator for drafts awaiting the operator.

**Architecture:** Pure server-rendered Jinja + the existing shared `ui.menu`/`popover()` component and `.pill` vocabulary. Counts (sync + to-review) render inline on full-page loads via the existing `_topbar_sync_context` Jinja context processor — no new endpoints, no new polls. Environment branching reads `settings.{auth_backend,app_env,dev_reload}`, mirroring the existing `_topbar_pills.html` logic.

**Tech Stack:** FastAPI, Jinja2 (`backend.app.routes.pages.templates`), HTMX, Alpine `popover()`, pytest + Starlette `TestClient`.

Spec: `docs/specs/2026-06-17-topbar-consolidation-design.md`

---

## File Structure

- **Modify** `backend/app/routes/pages/templates.py` — extend `_topbar_sync_context` to also inject `review_count`.
- **Modify** `backend/app/templates/pages/_topbar_pills.html` — add the "to review" chip; drop the standalone Shut-down block + env-pill; include the user menu.
- **Create** `backend/app/templates/pages/_user_menu.html` — env-aware user menu (email + env + one session action).
- **Modify** `backend/app/templates/pages/layout.html` — remove the standalone `topbar-user` + `topbar-logout` block (now inside the user menu).
- **Modify** `backend/app/static/app.css` — `.to-review-chip` + `.user-menu-trigger` + `.menu-head` styles; remove now-dead `.topbar-user` / `.topbar-logout` rules.
- **Modify** `tests/integration/test_topbar_shutdown_visibility.py` — Shut-down now lives in the user menu; update the render helper + assertions.
- **Create tests** in `tests/integration/test_routes_pages.py` — to-review chip + user-menu env variants.

Each task is independently testable and committable.

---

### Task 1: "To review" count + topbar chip

**Files:**
- Modify: `backend/app/routes/pages/templates.py` (the `_topbar_sync_context` function)
- Modify: `backend/app/templates/pages/_topbar_pills.html`
- Modify: `backend/app/static/app.css`
- Test: `tests/integration/test_routes_pages.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/integration/test_routes_pages.py` (uses the existing `_make_client`, `install_live_ctx`, `FakeArchive`, `_canonical`):

```python
def test_topbar_to_review_chip_counts_unapplied_drafts(monkeypatch, tmp_path):
    """An un-applied draft surfaces a '👁 N to review' chip linking to the review
    queue; once applied, the chip is gone."""
    import asyncio

    from backend.app.repositories.jobs import JobsRepo
    from backend.app.repositories.prompts import PromptsRepo

    with _make_client(monkeypatch, tmp_path) as client:
        ctx = client.app.state.core_ctx

        async def _seed_unapplied() -> None:
            prompts = PromptsRepo()
            _, vid = await prompts.create_with_initial_version(
                ctx.db, name="p", description=None, body="b",
                target_map={}, output_schema={}, model="m",
            )
            jobs = JobsRepo()
            jid = await jobs.create_job(ctx.db, prompt_version_id=vid, clip_ids=[12041])
            cur = await ctx.db.execute(
                "INSERT INTO annotations "
                "(catdv_clip_id, catdv_clip_name, prompt_version_id, job_id, model, "
                " prompt_used, raw_response, structured_output, clip_snapshot, created_at) "
                "VALUES (12041, 'C', ?, ?, 'm', 'p', '{}', '{}', '{}', '2026-06-02T00:00:00')",
                (vid, jid),
            )
            ann_id = cur.lastrowid
            await ctx.db.execute(
                "INSERT INTO review_items "
                "(annotation_id, studio_run_id, catdv_clip_id, kind, target_identifier, "
                " proposed_value, edited_value, decision, applied_at) "
                "VALUES (?, NULL, 12041, 'marker', NULL, '{}', NULL, 'pending', NULL)",
                (ann_id,),
            )
            await ctx.db.commit()

        asyncio.run(_seed_unapplied())
        install_live_ctx(client.app, archive=FakeArchive((_canonical(),)))

        r = client.get("/")
        assert r.status_code == 200
        assert "to-review-chip" in r.text
        assert "1 to review" in r.text
        assert 'href="/?anno=for_review"' in r.text

        # Apply it → chip disappears.
        async def _apply() -> None:
            await ctx.db.execute(
                "UPDATE review_items SET applied_at = '2026-06-02T01:00:00'"
            )
            await ctx.db.commit()

        asyncio.run(_apply())
        r2 = client.get("/")
        assert r2.status_code == 200
        assert "to-review-chip" not in r2.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages.py::test_topbar_to_review_chip_counts_unapplied_drafts -q`
Expected: FAIL — `"to-review-chip" not in r.text` (chip not implemented yet).

- [ ] **Step 3: Extend the context processor with `review_count`**

In `backend/app/routes/pages/templates.py`, inside `_topbar_sync_context`, within the existing `try:` block, right after the `rows = dict(conn.execute(... pending_operations ...).fetchall())` block and before `conn.close()` runs, add a second query and include it in the returned dict. Replace the existing tail of the `try` (the `counts = {...}` / `offline = ...` lines) with:

```python
            review_row = conn.execute(
                "SELECT COUNT(DISTINCT ri.catdv_clip_id) FROM review_items ri "
                "JOIN annotations a ON a.id = ri.annotation_id "
                "WHERE ri.applied_at IS NULL"
            ).fetchone()
        finally:
            conn.close()
        counts = {
            "queued": rows.get("pending", 0) + rows.get("in_flight", 0),
            "problems": rows.get("failed", 0) + rows.get("conflict", 0),
        }
        review_count = review_row[0] if review_row else 0
        monitor = getattr(getattr(state, "live_ctx", None), "connection_monitor", None)
        offline = monitor is not None and monitor.current_state().value != "online"
    except Exception:  # noqa: BLE001 - must never break page rendering
        return {}
    return {"sync_counts": counts, "offline": offline, "review_count": review_count}
```

(Note: this moves the `review_row` query above the `finally: conn.close()`, so both reads use the one connection. The `rows = dict(...)` line stays where it is; only the lines after it change.)

- [ ] **Step 4: Add the chip to the topbar**

In `backend/app/templates/pages/_topbar_pills.html`, immediately after the closing `</span>` of the `job-indicator` block (the `<span class="job-indicator" ...>...</span>`) and before `{% include "_sync_chip.html" %}`, add:

```html
  {% if review_count is defined and review_count %}
  <a class="pill changed to-review-chip" href="/?anno=for_review"
     title="{{ review_count }} clip(s) with AI drafts awaiting your review">
    <span class="led"></span>👁 {{ review_count }} to review
  </a>
  {% endif %}
```

- [ ] **Step 5: Style the chip**

In `backend/app/static/app.css`, after the `.pill.changed .led { ... }` rule (around line 144), add:

```css
.to-review-chip { text-decoration: none; cursor: pointer; gap: 4px; }
.to-review-chip:hover { filter: brightness(1.15); }
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_routes_pages.py::test_topbar_to_review_chip_counts_unapplied_drafts -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/routes/pages/templates.py backend/app/templates/pages/_topbar_pills.html backend/app/static/app.css tests/integration/test_routes_pages.py
git commit -m "feat(topbar): add 'N to review' indicator for drafts awaiting review"
```

---

### Task 2: Environment-aware user menu + topbar restructure

**Files:**
- Create: `backend/app/templates/pages/_user_menu.html`
- Modify: `backend/app/templates/pages/_topbar_pills.html`
- Modify: `backend/app/templates/pages/layout.html`
- Modify: `backend/app/static/app.css`
- Test: `tests/integration/test_topbar_shutdown_visibility.py`

- [ ] **Step 1: Rewrite the visibility test for the new structure**

Replace the entire contents of `tests/integration/test_topbar_shutdown_visibility.py` with:

```python
"""The session-end control in the topbar user menu is environment-aware:
Log out only with real auth (cloud/IAP); Shut down only on a local instance
(disabled under --reload). They are mutually exclusive."""

from types import SimpleNamespace

from backend.app.routes.pages.templates import templates


def _render(*, app_env: str, auth_backend: str, dev_reload: bool) -> str:
    settings = SimpleNamespace(
        app_env=app_env,
        auth_backend=auth_backend,
        dev_reload=dev_reload,
        catdv_catalog_id=881507,
        catdv_connect_mode="manual",
    )
    state = SimpleNamespace(
        core_ctx=SimpleNamespace(settings=settings),
        live_ctx=None,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=state),
        url=SimpleNamespace(netloc="localhost:8765"),
        state=SimpleNamespace(
            current_user=SimpleNamespace(email="dev@localhost", is_authenticated=True)
        ),
        headers={},
    )
    return templates.env.get_template("pages/_topbar_pills.html").render(request=request)


def test_cloud_shows_logout_not_shutdown():
    html = _render(app_env="prod", auth_backend="iap", dev_reload=False)
    assert "CLEAR_LOGIN_COOKIE" in html          # Log out present
    assert "/api/connection/shutdown" not in html  # Shut down absent


def test_local_shows_shutdown_not_logout():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=False)
    assert "/api/connection/shutdown" in html    # Shut down present
    assert "CLEAR_LOGIN_COOKIE" not in html       # Log out absent


def test_local_reload_disables_shutdown():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=True)
    assert "user-menu-trigger" in html
    assert "disabled" in html
    assert "/api/connection/shutdown" not in html  # disabled item carries no hx-post


def test_no_standalone_logout_or_env_pill():
    html = _render(app_env="dev", auth_backend="dev", dev_reload=False)
    assert "topbar-logout" not in html  # logout folded into the menu
    assert "shutdown-btn" not in html   # old standalone button gone
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/integration/test_topbar_shutdown_visibility.py -q`
Expected: FAIL — `_user_menu.html` doesn't exist / `user-menu-trigger` not rendered / old assertions changed.

- [ ] **Step 3: Create the user menu template**

Create `backend/app/templates/pages/_user_menu.html`:

```html
{# Topbar user menu: the single home for who you are + the one environment-
   appropriate "end your session here" action. Log out only with real auth
   (cloud/IAP); Shut down only on a local instance (disabled under --reload,
   forbidden in prod by the route). Built on the shared ui.menu/popover()
   component — see design-language.md §8. #}
{% import "components/_ui.html" as ui %}
{%- set _settings = request.app.state.core_ctx.settings -%}
{%- set _user = (request.state.current_user if request.state is defined else None) -%}
{%- set _email = (_user.email if _user else "") -%}
{%- set _initials = ((_email.split('@')[0][:2]) | upper) if _email else "?" -%}
{%- set _is_iap = _settings.auth_backend == "iap" -%}
{%- set _is_prod = _settings.app_env == "prod" -%}
{%- set _env_label = ("PROD" if _is_prod else "DEV") ~ " · " ~ request.url.netloc -%}
{% call ui.menu(label=_initials, align='right', trigger_cls='user-menu-trigger') %}
  <div class="menu-head">
    <div class="menu-head-email">{{ _email }}</div>
    <div class="menu-head-env">{{ _env_label }}</div>
  </div>
  <div class="menu-sep"></div>
  {% if _is_iap %}
    {{ ui.menu_item('Log out', href='/?gcp-iap-mode=CLEAR_LOGIN_COOKIE', icon='⎋') }}
  {% elif not _is_prod %}
    {% if _settings.dev_reload %}
      {{ ui.menu_item('Shut down', icon='⏻', attrs='disabled title="Reload mode — stop with Ctrl-C in the terminal"') }}
    {% else %}
      {{ ui.menu_item('Shut down & release seat', danger=True, icon='⏻', attrs='hx-post="/api/connection/shutdown" hx-target="body" hx-swap="innerHTML" hx-confirm="Shut down the annotator and release the CatDV seat?"') }}
    {% endif %}
  {% endif %}
{% endcall %}
```

- [ ] **Step 4: Restructure the topbar pills**

In `backend/app/templates/pages/_topbar_pills.html`, replace the block that currently renders the shutdown button + env pill (the `{% if _settings.app_env == "prod" %}` … `{% endif %}` chain AND the `<span class="env-pill ok">…</span>` line) with a single include of the user menu. The pillset's closing structure should become:

```html
  {% include "_sync_chip.html" %}
  {% include "_connection_chip.html" %}
  {% include "pages/_user_menu.html" %}
</span>
```

Leave the `{% set _settings = ... %}` line at the top of the file (the user menu reads settings too, but it re-derives its own; keeping `_settings` is harmless — remove it only if nothing else in the file uses it after this edit).

- [ ] **Step 5: Remove the standalone email + logout from layout**

In `backend/app/templates/pages/layout.html`, delete this block (the `{% if request.state.current_user ... %}` … `{% endif %}` that renders `topbar-user` and `topbar-logout`):

```html
      {% if request.state.current_user and request.state.current_user.is_authenticated %}
      {# Admin nav lives in the rail (gated on is_admin in _rail.html). #}
      <span class="topbar-user" title="Signed in">{{ request.state.current_user.email }}</span>
      {# IAP logout: clearing the IAP cookie forces a fresh Google sign-in. #}
      <a class="btn ghost sm topbar-logout" href="/?gcp-iap-mode=CLEAR_LOGIN_COOKIE" title="Log out">Log out</a>
      {% endif %}
```

The user menu (rendered inside the pills) now owns the email + logout.

- [ ] **Step 6: Style the user-menu trigger + menu header**

In `backend/app/static/app.css`, add near the other topbar/pill rules (e.g. after `.to-review-chip` from Task 1):

```css
/* User menu: a rounded pill trigger consistent with .conn-pill, not a plain btn. */
.user-menu-trigger {
  height: 28px; padding: 0 10px; border-radius: 999px;
  font-family: var(--f-mono); font-size: 11px; letter-spacing: 0.04em;
}
.menu-head { padding: 6px 12px; }
.menu-head-email { color: var(--text); font-size: 12px; }
.menu-head-env {
  color: var(--text-3); font-family: var(--f-mono); font-size: 10.5px;
  letter-spacing: 0.04em; text-transform: uppercase; margin-top: 2px;
}
.menu-sep { height: 1px; background: var(--line-2); margin: 4px 0; }
```

- [ ] **Step 7: Run the visibility test to verify it passes**

Run: `.venv/bin/python -m pytest tests/integration/test_topbar_shutdown_visibility.py -q`
Expected: PASS (all four tests).

- [ ] **Step 8: Commit**

```bash
git add backend/app/templates/pages/_user_menu.html backend/app/templates/pages/_topbar_pills.html backend/app/templates/pages/layout.html backend/app/static/app.css tests/integration/test_topbar_shutdown_visibility.py
git commit -m "feat(topbar): consolidate email/logout/env/shutdown into an env-aware user menu"
```

---

### Task 3: Remove dead CSS + full verification

**Files:**
- Modify: `backend/app/static/app.css`
- Test: whole suite + design-language guard

- [ ] **Step 1: Confirm the old topbar rules are now unused**

Run: `grep -rn "topbar-logout\|topbar-user" backend/app/templates/ backend/app/static/*.js`
Expected: no matches in templates/JS (only the CSS rule definitions remain). If there ARE matches, stop and reconcile before deleting.

- [ ] **Step 2: Delete the dead rules**

In `backend/app/static/app.css`, remove the now-unused `.topbar-user { ... }` and `.topbar-logout { ... }` rules (around lines 2567 and 2572). Leave `.env-pill` and `.shutdown-btn` rules in place ONLY if still referenced elsewhere — run `grep -rn "env-pill\|shutdown-btn" backend/app/templates/` first; if no matches, remove those rules too.

- [ ] **Step 3: Run the design-language guard + topbar tests**

Run: `.venv/bin/python -m pytest tests/unit/test_design_language_guard.py tests/integration/test_topbar_shutdown_visibility.py tests/integration/test_routes_pages.py -q`
Expected: PASS (no hand-rolled `*-menu`/`modal-*` introduced; user menu uses `ui.menu`).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (the prior baseline was 1637 passed, 4 skipped; expect 1639+ with the two new tests).

- [ ] **Step 5: Manual acceptance (from the spec)**

Reload the dev server and hard-refresh. Verify spec flows 1, 2, 4, 6 locally (declutter; user menu shows Shut down not Log out; to-review chip appears/links; no flicker). Flows 3 & 5 (cloud Log out; sync-vs-review independence) are verified by the tests + a staging check if available.

- [ ] **Step 6: Commit**

```bash
git add backend/app/static/app.css
git commit -m "chore(topbar): drop dead topbar-user/topbar-logout CSS after consolidation"
```

---

## Notes for the implementer

- **Do not hand-roll a dropdown.** The user menu MUST use `ui.menu` / `ui.menu_item` + `popover()` (already imported via `components/_ui.html`). `tests/unit/test_design_language_guard.py` fails CI if you introduce a new `*-menu` class.
- **The context processor must never raise.** It runs on every full-page render; keep the broad `except Exception: return {}` — the chip/menu degrade gracefully (chip hidden; menu falls back to the load-fetch counts).
- **Initials** are derived as the first two characters of the email local-part, uppercased (`dev@localhost` → `DE`, `adam@…` → `AD`). Good enough; no avatar service.
- The to-review count is **job-based** (joined to `annotations`) so it matches the `/batches` "awaiting review" metric and excludes studio-run review items.
