# UI Consolidation Rollout Plan

**Date:** 2026-06-09
**Spec:** `docs/specs/2026-06-09-popover-menu-module-and-design-language-guard-design.md`
**ADR:** `docs/adr/0062-popover-menu-module-and-design-language-guard.md`

Execution plan for finishing the menu consolidation and widening the
enforcement guard so the whole UI is frozen against further growth. Maps
the work to parallel-safe units for subagent execution.

## Done (PR 1, branch `feat/popover-menu-module`)

- `static/popover.js` (`popover()` two-mode behavior), `.popover-panel`/
  `.menu` CSS, `ui.menu`/`menu_item`/`menu_sep`/`menu_header` macros.
- Guard `tests/unit/test_design_language_guard.py` (btn/menu classes +
  JS formatters).
- Migrated 2 pilot menus (prompt version picker, studio version chip);
  deleted their CSS; `design-language.md` §8; ADR 0062.
- 570 tests green.

## Remaining work

### Phase 0 — Widen the guard (FIRST, blocks everything) — owner: lead

Freeze the unguarded axes the menu work doesn't touch, so growth stops
everywhere now (grandfather all current names; migrate later):

- **Modal classes** (`modal` / `modal-*`): grandfather the 12 current
  tokens (`_bulk_annotate_modal`, `_studio_archive_picker`,
  `_prompt_detail`, `batches`).
- **Card classes** (`*-card`): grandfather `nb-card`, `ri-card`,
  `shutdown-card` (permanent), `studio-clip-card`, `studio-prompt-card`,
  `cmp-card`, `modal-card`.
- **Inline `style=` on form controls** (`<input|textarea|select>`):
  ban outright (current count 0; `components/_ui.html`'s sanctioned
  textarea `min-height` is exempt).
- **Regex fix**: the class-attr scanner must handle attrs containing
  inner quotes (`class="x{% if s == 'cmp' %} cmp-card{% endif %}"`),
  which the PR-1 regex misses.

This is foundational (every later unit depends on the widened guard's
grandfather lists) and needs inventory judgement → done by the lead, not
a subagent. **Committed before any worktree is created** so subagents
branch from a base that contains it.

### Phase 1 — Parallel-safe menu migrations — owner: subagents (worktree)

Two menus are cleanly independent (own JS file, no shared component
scope). Each runs in its own git worktree and merges without touching
the other's files:

| Unit | Template(s) | JS | Independent of |
|---|---|---|---|
| **P1a annotate-menu** (async) | `_annotate_dropdown.html` | `clipAnnotate.js` | studio.js, promptEditor.js |
| **P1b actions-menu** (row kebab) | `clips.html` | (inline / row_select.js) | studio.js, promptEditor.js |

Each unit: migrate template onto `popover()`/`.menu`/`ui.menu_item`
(keep async body for P1a); delete its bespoke CSS block from `app.css`;
remove its token(s) from `GRANDFATHERED`; add/extend an integration-test
assertion; run the offline test subset green.

### Phase 2 — Coupled studio/prompt core — owner: lead (sequential)

These three share `studio.js` and/or `promptEditor.js`, so they are NOT
parallelised — done sequentially to keep those files coherent:

- **tmpl-menu** (`_prompt_menu.html`, promptEditor scope; mixed
  form/link/action rows → `menu_item` variants).
- **hdr-tmenu** (`_studio_header.html`): migrate to **hosted mode** on
  `studioPage` and drop the `data-prompt-switch` interceptor in
  `studio.js` (the menu reads `focusedClipId` directly — the real
  shadowing fix).
- **model-menu** (`_prompt_detail.html` + `_studio_prompt_card.html`,
  selection, `modelOpen` in both `promptEditor.js` and `studio.js`).
- **player.js timecode** — CORRECTION (found in implementation): `tc()` is a
  frame-accurate SMPTE formatter (`hh:mm:ss:ff`), not an `m:ss` duplicate of
  `fmtTimecode`. It stays; `player.js` is a *permanent* formatter exception.

### Phase 3 — Reconcile + close out — owner: lead

- Merge Phase-1 worktrees; reconcile the non-overlapping `app.css`
  deletions and `GRANDFATHERED` removals (auto-mergeable; verify).
- `GRANDFATHERED` (menu/btn) shrinks to the permanent exceptions
  (`shutdown-btn`, `rail-btn`).
- Full suite green; update spec/ADR if any decision shifted.

## Shared-file ownership (collision control)

- `app.css`: each unit deletes a **different, non-adjacent** block
  (actions-menu ~L365, model-menu ~L1079, tmpl-menu ~L1121,
  annotate-menu ~L1279, hdr-tmenu ~L1992) → git auto-merges; lead
  verifies at Phase 3.
- `test_design_language_guard.py`: each unit removes a **different line**
  from `GRANDFATHERED` → non-overlapping; lead verifies.
- `studio.js` / `promptEditor.js`: touched only by Phase 2 (lead,
  sequential) — never by a subagent.

## Subagent execution protocol

- **Isolation:** `worktree`. Branch base = the commit that includes
  Phase 0 + PR 1.
- **No dev server, no CatDV seat.** All verification is offline: unit
  tests + `TestClient` integration tests. Never start `uvicorn`/
  `backend.app`; never hit `192.168.1.41`.
- **Run tests via the parent venv** (worktrees have no `.venv`):
  `/Users/adambarta/git/catdv-annotator/.venv/bin/python -m pytest …`
  with the worktree as cwd (imports resolve against cwd).
- **Reuse, don't invent:** follow `docs/design-language.md` §8; use
  `popover()` + `.menu` classes + `ui.menu_item`. No new `*-menu`/`*-btn`
  class — the guard will fail you.
- **Scope discipline:** touch only the files listed for your unit + your
  own `app.css` block + your own `GRANDFATHERED` line + your own test.
  Do not edit `studio.js`, `promptEditor.js`, or another unit's files.
- **Acceptance:** the unit's integration test asserts the new
  `popover()`/`menu-item` markup and the absence of the old class; the
  guard's dead-entry check passes (token removed from list AND template);
  the offline subset is green.

## Manual acceptance (deferred, needs dev server — single seat)

Per spec flows #1–#6: a live click-through of each migrated menu (opens,
click-outside/Esc dismiss, rows act) + player/studio timecode. Run once
at the end of Phase 3 following the CLAUDE.md seat discipline (one
instance, `SIGTERM` shutdown).
