# Prompt Studio — PR3 (polish)

**Date:** 2026-05-28
**Status:** Approved (design)
**Supersedes / extends:** `docs/specs/2026-05-26-prompt-studio-design.md`
**Predecessors:**
- PR1 — `docs/adr/0033-prompt-studio-pr1-shell-and-run-loop.md`
- PR2 — `docs/adr/0034-prompt-studio-pr2-version-compare.md`
- post-PR2 — `docs/adr/0037-studio-shared-player-chrome-and-focused-clip-url.md`,
  `docs/adr/0038-studio-output-via-review-items.md`

## Problem

PR1 shipped the studio shell + single-clip run loop. PR2 added the
version-compare prompt-card, line-diff views, and the second range row
on the player overlay. The post-PR2 refactor moved studio output onto
the shared `_anno_panels.html` partial, lifted the studio player onto
the canonical `Alpine.data("player", ...)` component, and seeded the
focused clip in the URL.

What's left is the third bullet list from the umbrella spec — polish.
The Run button's state machine doesn't flash on completion and has no
cancel affordance even though the underlying job-cancel endpoint
already exists. The right-pane empty and error states render as plain
`.muted` text with no shared shape. And the surfaces that PR2 added
(`.pc-vchip`, `.pc-vmenu`, `.pc-diff`, `.cmp-card`, the range overlays)
predate the design-language consolidation in `docs/design-language.md`
— several of them reference tokens that don't exist (`--bg-3`,
`--accent-fade`, `--border`, `--fg-muted`) with raw hex fallbacks, and
the range-overlay colors are hand-coded rgba that don't match the
accent palette.

This spec covers PR3 only. It is the final umbrella slice; there is no
PR4.

## Goals

1. **Run-button state machine.** The button reads
   `▶ Run on this clip · v{n}` when idle, `⟳ Running… (M:SS)` while
   running, and flashes `✓ Done` for ~1.2s on `running → idle`
   (success only) before returning to the idle label. Clicking the
   button while running issues `POST /api/jobs/{job_id}/cancel`,
   stops the poll loop, and returns the UI to idle. The elapsed-time
   ticker is driven from a single `setInterval` and uses
   `window.fmtTimecode`.

2. **Empty- and error-state polish.** Every right-pane empty state
   (no version, no clip focused, no run yet, pending/running, error)
   renders inside the same `.run-empty` / `.run-error` shell with
   design-language tokens (`--text-2`, `--text-3`, `--panel-2`,
   `--bad`). Error messages wrap (`white-space: pre-wrap; word-break:
   break-word`) and are user-selectable. While a run is in progress,
   the player must stay mounted — the run-status partial shows the
   running message, but the studio player slot is untouched.

3. **Visual audit against `docs/design-language.md`.** Every Studio
   surface that still references a non-existent CSS var with a hex
   fallback, a raw rgba color, or an inline `style=` for non-dynamic
   values is migrated to the canonical primitives — `:root` tokens
   (`--accent`, `--line`, `--panel-2`, `--info`, etc.), `.btn` /
   `{{ ui.button(...) }}`, `{{ ui.field(...) }}`, and
   `window.fmtTimecode`. Hover, focus, and active states are
   consistent across header, chips, picker dropdown, compare button,
   diff toggle, clip cards, and folder rows.

## Non-goals (PR3)

- No new database schema, no new migrations.
- No new REST endpoints. The job-cancel endpoint
  (`POST /api/jobs/{job_id}/cancel` from PR4 of the original UI MVP)
  already exists and is wired by the annotator; PR3 only wires it from
  the Studio Run button.
- No port of the React prototype's `styles.css`. CLAUDE.md says the
  design language has diverged on purpose; the source of truth is
  `docs/design-language.md` + `app.css` `:root` tokens.
- No multi-clip batch runs, no run history viewer, no
  stacked/unified diff layouts. (Deferred from the umbrella spec.)
- No keyboard shortcut work. (Deferred — Studio has no shortcuts yet
  and adding them isn't a polish task.)
- No new tokens beyond two range-overlay tokens
  (`--range-cur`, `--range-cmp`). Anything else that wants polish
  uses the existing tokens.

## Design source

`docs/design-language.md` and `backend/app/static/app.css` (the
`:root` block) are the source of truth. The PR2 surfaces are
audited against them. The React prototype is NOT consulted (CLAUDE.md
"Frontend: explore before implementing": *"do not port the React
prototype's styles.css verbatim — the design language has diverged
on purpose. The prototype is for reference of intent, not pixels."*).

## Locked decisions

| Decision | Choice | Reason |
|---|---|---|
| Cancel mechanism | Reuse existing `POST /api/jobs/{job_id}/cancel` (added in PR4 of the UI MVP). The annotator already checks `live.status == 'cancelled'` between items. | Umbrella spec promises "cancel comes free from the jobs pipeline" — verified. No new endpoint required. |
| Cancel click target | The Run button itself — same button, action switches on `running` state. No separate Cancel button. | Matches the umbrella spec's "Run button is the single live control". A second button doubles the surface for one operation. |
| ✓ Done flash | Pure JS state machine in `studioPage()`: `running → showingDone` for 1200ms, then `showingDone → idle`. No CSS animation. | Predictable timing; testable; survives Alpine re-renders. |
| Done flash on error / cancel? | No. Flash is success-only. Error state is communicated via the Output partial (`.run-error` block). Cancelled state is communicated by the button silently returning to idle. | Avoids false "✓ Done" on terminal-error runs. Mirrors how the React prototype shows ✓ only on `status === 'ok'`. |
| Elapsed-time tick rate | 1 Hz (every 1000ms), bumped from the current 500ms. | Display granularity is whole seconds; ticking twice per second is wasted work and visible jitter when label changes mid-frame. |
| Range-overlay tokens | New `--range-cur` and `--range-cmp` in `:root`, defined as `color-mix(in oklab, var(--info) 45%, transparent)` and `color-mix(in oklab, var(--accent) 45%, transparent)`. | The cur/cmp colors don't map cleanly to existing semantic tokens (info/accent), and the rgba(...) hardcodes lose dark-mode / theme alignment if the palette ever shifts. Two new tokens is the smallest possible delta. |
| Missing-token fallbacks | Delete `var(--bg-3, #1f1f1f)`, `var(--accent-fade, #2b3a4d)`, `var(--border, #2a2a2a)`, `var(--fg-muted, #888)` and replace each with the correct existing token: `--surface`, `--accent-2`, `--line`, `--text-3`. | These tokens have never been defined; the hex fallback is always what actually renders. Removing the fallback also removes the lie. |
| `.run-empty` / `.run-error` styling | Add dedicated classes (no longer pure `.muted` text). `.run-empty` is `--text-3` on the parent's background; `.run-error` is a `--panel-2` card with a `--bad` left border, `--text-2` body, monospace error message. | Empty and error states deserve consistent shape (border-radius, padding, line-height) — currently each one renders subtly differently. |
| Where `cancel()` lives | New method on `studioPage()` in `studio.js`, called from the same Alpine click binding as `runOnFocusedClip()`. The click handler dispatches based on `running`. | Single click target; binding stays declarative. |
| Cancelled-run UI | Frontend immediately stops polling and sets `running=false`. The studio_run row is left to whatever terminal state the backend reaches (likely `cancelled` after the annotator finishes the in-flight Gemini call). Output partial re-renders on `pendingRunSwap++` and shows whichever state the run is in. | Best-effort cancel matching the existing jobs semantics. No new server logic. |

## Architecture

### Components — new and changed

```
NEW templates       (none — all changes are in existing partials)

CHANGED templates
├ pages/_studio_header.html        Run button: idle/running/done labels
│                                   bound to studioPage state machine;
│                                   click dispatches run-or-cancel.
├ pages/_studio_run_output.html    .run-empty / .run-error classes
│                                   instead of raw .muted; error block
│                                   wraps + monospace.
├ pages/_studio_folder_list.html   New-folder input → ui.field(...);
│                                   raw inline styles → CSS class;
│                                   "+ new" button uses ui.button(...).
├ pages/_studio_compare.html       style="display:none" → x-show.

CHANGED static
├ static/studio.js                 1-second elapsed ticker;
│                                   doneFlashUntilMs state;
│                                   cancel() method;
│                                   runOnFocusedClip handles done flash
│                                   on success only.
├ static/app.css                   :root gains --range-cur, --range-cmp;
│                                   removes phantom-token fallbacks;
│                                   .run-empty + .run-error styles;
│                                   .pc-vchip / .pc-vmenu / .pc-diff /
│                                   .cmp-card / .timeline-legend audited
│                                   against tokens.

CHANGED docs
├ docs/design-language.md          Note about the two range tokens
│                                   (one sentence each — they are
│                                   Studio-specific but they belong
│                                   in the design language because
│                                   we want them used consistently
│                                   if a third range row is ever added).
├ docs/decisions.md                Append ADR 0039.

NEW docs
└ docs/adr/0039-prompt-studio-pr3-polish.md
```

No new server routes. No new template partials. No new JS files.
PR3 is pure visual + state-machine polish.

### Bucket 1 — Run-button state machine

**Current state machine (studio.js:126–219).**

`running: false`, `runId: null`, `runStartMs: 0`,
`runningElapsedLabel: '0:00'`. `runOnFocusedClip()` POSTs
`/api/studio/runs`, captures `run_id` (and the response also returns
`job_id` — currently discarded by the frontend), polls
`/api/studio/runs/{id}` every 1s until terminal, and in the `finally`
block sets `running = false` and bumps `pendingRunSwap`. Ticker fires
every 500ms.

**Target state machine.**

```
              click (idle)                     terminal: ok
   ┌────────────────────────────────────────────────────────┐
   ▼                                                        │
[idle]  ──── click(running) ───> [cancelling] ─── DELETE────┤
   ▲              │                  │                       │
   │              ▼                  ▼                       ▼
   │           [running]──────> [done(1.2s)]──────────────>[idle]
   │              │
   │              └─── terminal: error/cancelled ─> [idle]
```

In state:

| State | Button label | Click action | Disabled? |
|---|---|---|---|
| `idle` | `▶ Run on this clip · v{n}` | start | when no focused clip |
| `running` | `⟳ Running… {elapsed}` | cancel | no |
| `cancelling` | `⟳ Cancelling…` | (ignored) | yes |
| `done` (1200ms) | `✓ Done` | (ignored) | yes |

Implemented as two booleans on `studioPage()`:

```js
running: false,            // poll loop active
cancelling: false,         // DELETE in-flight
doneFlashUntilMs: 0,       // performance.now() target; 0 = no flash
runningElapsedLabel: '0:00',
runJobId: null,            // captured from POST response
```

The label is a computed Alpine expression:

```js
runButtonLabel() {
  const now = performance.now();
  if (this.doneFlashUntilMs && now < this.doneFlashUntilMs) {
    return '✓ Done';
  }
  if (this.cancelling) return '⟳ Cancelling…';
  if (this.running) return `⟳ Running… ${this.runningElapsedLabel}`;
  return `▶ Run on this clip · v${this.activeVersionNum ?? '?'}`;
}
```

The single 1-second `setInterval` in `init()` does two jobs:
1. While `running`, recompute `runningElapsedLabel`.
2. While `doneFlashUntilMs > 0` and `now > doneFlashUntilMs`,
   set `doneFlashUntilMs = 0` so the label snaps back to idle.

A method `runOrCancel()` becomes the click target:

```js
async runOrCancel() {
  if (this.cancelling) return;
  if (this.running) return this.cancel();
  return this.runOnFocusedClip();
}

async cancel() {
  if (!this.runJobId || this.cancelling) return;
  this.cancelling = true;
  try {
    await fetch(`/api/jobs/${this.runJobId}/cancel`, { method: 'POST' });
  } catch (err) {
    console.error('cancel failed', err);
  } finally {
    // Stop the poll loop; runOnFocusedClip()'s finally will tidy up.
    this.running = false;
    this.cancelling = false;
    this.pendingRunSwap++;
  }
}
```

`runOnFocusedClip()` is updated to:

1. Capture `job_id` from the POST response (currently discarded).
2. On `status === 'ok'`, set `doneFlashUntilMs = now + 1200`.
3. On error or cancel, do NOT set the flash. The Output partial
   already renders `.run-error` on error and the empty-state shell on
   cancel.

`_poll` is updated to exit early if `!this.running` (so cancel breaks
the loop) and to return the run's terminal status (`'ok' | 'error' |
'cancelled'`) so the caller can decide whether to flash.

### Bucket 2 — Empty / error states

**Current.** `_studio_run_output.html` emits four shapes:

```jinja
<div class="run-empty muted">Unknown version.</div>
<div class="run-empty muted">No run yet. Hit <b>Run</b> to execute v{n}…</div>
<div class="run-empty muted">⟳ Running…</div>
<div class="run-error"><div class="run-error-h">…</div><div class="run-error-msg">…</div></div>
```

`.run-empty` and `.run-error` have no CSS rules of their own — `.muted`
does all the work.

**Target.** Same markup, with dedicated CSS so the shapes are
consistent. Specifically:

```css
.run-empty {
  padding: 12px 14px;
  color: var(--text-3);
  font-size: 12px;
  line-height: 1.5;
}
.run-empty b { color: var(--text-2); font-weight: 600; }
.run-error {
  padding: 12px 14px;
  background: var(--panel-2);
  border-left: 3px solid var(--bad);
  border-radius: var(--r-2);
}
.run-error-h {
  font-size: 12px;
  color: var(--text-2);
  margin-bottom: 4px;
}
.run-error-h b { color: var(--bad); font-weight: 600; }
.run-error-msg {
  font-family: var(--f-mono);
  font-size: 12px;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  user-select: text;
}
```

The `muted` class stays on the empty-state divs (harmless; aligns
with how `.muted` is used elsewhere as a tint).

**Player stays mounted during runs.** This is already true (the run
partial doesn't sit in the same slot as the player) — PR3 adds a
guard test asserting `data-studio-player-slot` remains in the DOM
after `pendingRunSwap` increments. No code change beyond the test.

**No-clip-focused state on the Output tab.** This already exists at
`_studio_prompt_card.html` line 92 (`<div class="muted">Click a clip
in a folder to focus it.</div>`). PR3 wraps it in the same
`.run-empty` shell as the other Output-tab empties.

### Bucket 3 — Visual audit

The audit is a flat list of concrete swaps. Each line is a single
edit; the implementation plan walks them one task at a time.

**Phantom-token fallbacks (delete fallback, swap to real token):**

| File:line | Before | After |
|---|---|---|
| `app.css:1979` | `var(--bg-2, #161616)` | `var(--panel-2)` |
| `app.css:1979` | `var(--border, #2a2a2a)` | `var(--line)` |
| `app.css:1989` | `var(--bg-3, #1f1f1f)` | `var(--hover)` |
| `app.css:1990` | `var(--accent-fade, #2b3a4d)` | `var(--accent-2)` |
| `app.css:1998` | `var(--border, #1f1f1f)` | `var(--line)` |
| `app.css:2009` | `var(--accent, #4a90e2)` | `var(--accent)` (drop fallback) |
| `app.css:2010` | `var(--accent-fade, #2b3a4d)` | `var(--accent-2)` |
| `app.css:2024` | `var(--border, #1f1f1f)` | `var(--line)` |
| `app.css:2025` | `var(--bg-2, #0f1216)` | `var(--bg-2)` (drop fallback) |
| `app.css:2027` | `var(--fg-muted, #888)` | `var(--text-3)` |

**Raw rgba range colors → tokens:**

`:root` gains:

```css
--range-cur: color-mix(in oklab, var(--info)   45%, transparent);
--range-cmp: color-mix(in oklab, var(--accent) 45%, transparent);
```

And the consuming rules:

| File:line | Before | After |
|---|---|---|
| `app.css:2014` | `background: rgba(74, 144, 226, 0.45);` | `background: var(--range-cur);` |
| `app.css:2016` | `background: rgba(220, 140, 60, 0.45);` | `background: var(--range-cmp);` |
| `app.css:2018` | `color: rgba(74, 144, 226, 0.9);`  | `color: var(--info);` |
| `app.css:2019` | `color: rgba(220, 140, 60, 0.9);` | `color: var(--accent);` |

**Raw rgba diff highlights:** the diff-table del/ins backgrounds at
`app.css:2001` / `app.css:2004` (`rgba(220, 60, 60, 0.18)` and
`rgba(60, 180, 90, 0.18)`) become
`color-mix(in oklab, var(--bad) 18%, transparent)` and
`color-mix(in oklab, var(--good) 18%, transparent)` respectively.
This avoids the same palette-drift bug.

**Inline `style=` on hand-rolled UI:**

| File:line | Before | After |
|---|---|---|
| `_studio_compare.html:20` | `style="display:none"` | `x-show="..."` driven by Alpine state already in scope (or `x-cloak` on initial render) |
| `_studio_folder_list.html:14` | `style="display:flex;gap:6px;padding:8px 12px;border-bottom:1px solid var(--line);"` | new class `.studio-folder-new` in app.css |
| `_studio_folder_list.html:15-16` | raw `<input>` with `style="flex:1;font-size:12px;"` | `{{ ui.field(name=..., placeholder="folder name…", model="newFolderName", attrs=...) }}` (Alpine `@keyup.enter` stays) |
| `_studio_folder_list.html:11,17` | `class="btn ghost mini"`, `class="btn primary mini"` | `{{ ui.button(label="…", variant="ghost"|"primary", size="sm", ...) }}` — drop the undefined `.mini` modifier; `.sm` (existing) is the same height |
| `_studio_run_output.html:33` | `style="padding:6px 0;"` | move to `.run-stats { padding: 6px 0; }` block (already exists for `.run-stats`; just remove the inline override) |
| `_studio_clip_card.html:19` | `style="background-image:url('{{ thumb_url }}')"` | unchanged (legitimate dynamic background) |

**Hand-rolled buttons → `.btn`-based:**

`.btn-close-cmp` (`app.css:2011`) is the dimissal `×` on the cmp
card. It already extends `.btn`; the only custom rule is `font-size:
14px; line-height: 1;`. Move those values into `.btn.sm.icon` (a
modifier already used elsewhere for icon buttons) and drop the
custom class. The HTML changes from `class="btn sm icon
btn-close-cmp"` to just `class="btn sm icon"`.

`.studio-show-player` (`app.css:1559`) is the "Show player" button
in the header. Audit only — it predates the `.btn` system. Refactor
to `<button class="btn sm ghost studio-show-player">…</button>` and
move only the layout-specific rules (positioning in the header) to
the custom class; the rest goes to `.btn.ghost.sm`.

`.studio-clip-card .remove-x` (`app.css:1742`) is the per-card
remove-from-folder button. It's a custom class that draws an `×` on
the corner of a card. Acceptable as-is since it's pure CSS-only
(no JS, positioned absolute over the thumbnail) and doesn't share
behavior with `.btn`. Keep it. Document the exception in the ADR.

**Hover / focus / active audit.** The PR2 components have hover
states but no `:focus-visible` styling. Adding it isn't a token
swap — it's adding two-line rules. Scope:

```css
.pc-vchip .btn:focus-visible,
.pc-vmenu-item:focus-visible,
.btn-diff-toggle:focus-visible,
.studio-clip-card:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}
```

These rules go at the bottom of the studio block in `app.css`.

## REST API

No new endpoints. The cancel flow uses the existing
`POST /api/jobs/{job_id}/cancel` (in `backend/app/routes/jobs.py`)
which updates `jobs.status = 'cancelled'`. The annotator already
checks the status before processing each job item
(`backend/app/services/annotator.py:82-84`) and exits early.

The studio_run row itself goes through `complete_ok` /
`complete_error` (annotator.py:128, 175) based on what happened
mid-Gemini-call:
- If the Gemini call completes before the annotator notices the
  cancel, the run becomes `ok` (the studio_run already has its
  output), but the parent job becomes `cancelled`.
- If the Gemini call is mid-flight when the user clicks cancel, the
  studio_run typically stays in `pending` because annotator.py only
  re-checks the cancel flag *between* items and a single-clip
  Studio run has exactly one item.

PR3 does not change this behavior. The frontend treats cancel as
best-effort: it stops polling, refreshes the Output tab, and the
Output partial renders whatever state the studio_run is in (empty
state if still pending; result if the run completed before cancel
was processed).

## Migrations

None.

## Testing strategy

PR3 is mostly CSS + Alpine state. The TDD bar is "every behavioral
change has a test"; CSS changes use snapshot-style or hardcoded
assertions on the template output where it's testable, and a manual
acceptance flow where it isn't.

**Unit / integration tests (added):**

1. `tests/integration/test_studio_run_button_state.py` — asserts the
   `data-state` attribute and the visible label transitions on
   `_studio_header.html`. Render the partial with the page Alpine
   context in three states (idle, running, done-flash) by passing
   different `running` / `doneFlashUntilMs` values into the template
   context. (The state machine itself lives in JS but the bindings
   are in the template — we test the bindings exist and the
   computed label resolves correctly via a small `studio.js` mirror
   in `tests/unit/test_studio_run_button_label.py`.)

2. `tests/unit/test_studio_run_button_label.py` — pure Python mirror
   of `runButtonLabel()`. Cases: idle, idle-while-cancelling,
   running with 0:00 / 1:23 elapsed, done-flash active, done-flash
   expired. Mirror is kept in `tests/_helpers/studio_state.py` so
   future state-machine changes touch one place.

3. `tests/integration/test_studio_cancel_wiring.py` — POST
   `/api/jobs/{id}/cancel` exists and a 200 response transitions
   the job. (Pure regression; no new behavior — this test guards
   the contract the frontend depends on.)

4. `tests/integration/test_studio_run_output_empty_states.py` — for
   each of (no-version, no-run, running, error) the
   `/studio/_run` partial renders the new `.run-empty` /
   `.run-error` shell and includes the design-language tokens via
   the generated style rules. Specifically: asserts the rendered
   HTML contains `class="run-error"` (not `class="muted"`) when
   the run is in error.

5. `tests/integration/test_studio_player_persists_during_run.py` —
   render the page, bump `pendingRunSwap` semantically (i.e.
   re-GET `/studio/_run`) and assert that the page's
   `data-studio-player-slot` is unaffected. This is a guard against
   future regressions; the slot lives in `studio.html`, not in
   `_studio_run_output.html`.

**CSS audit assertions (added):**

6. `tests/unit/test_studio_css_no_phantom_tokens.py` — greps the
   compiled `app.css` for the specific phantom-token fallbacks
   listed above (`--bg-3,`, `--accent-fade,`, `--border,`,
   `--fg-muted,`) and asserts none remain. Greps for raw
   `rgba(74, 144, 226` and `rgba(220, 140, 60` and asserts they
   no longer appear (replaced by `var(--range-*)`).

7. `tests/unit/test_studio_css_has_range_tokens.py` — asserts
   `:root` block contains `--range-cur` and `--range-cmp`.

**Manual smoke tests:** see Manual acceptance flows below.

## Manual acceptance flows

Each flow corresponds to one capability. A reviewer (or the
implementer at the end of the work) walks through them in order
against a fresh local dev server (`server-start` skill). All flows
must pass before the PR is considered done. Test data assumption:
at least one prompt with two versions (one production, one draft)
and a folder containing two cached clips, each of which has at
least one prior successful run on the draft version.

1. **Run button — idle label tracks active version**
   - Open `/studio?prompt_id=N`. Focus a clip in a folder.
   - Run button reads `▶ Run on this clip · v{n}` where `{n}` is
     the cur version_num.
   - Switch cur via the version chip to a different version.
     Run-button label updates immediately to that new `v{n}`.

2. **Run button — running state with ticking elapsed**
   - Click `Run`. Button label switches to `⟳ Running… 0:00`.
   - After 5–10s the trailing M:SS increments at 1Hz, monotonically.
   - During this time, the player below stays mounted and the
     video is unaffected — scrub the timeline, the playhead moves.

3. **Run button — ✓ Done flash on success**
   - Continue from flow 2. When the run completes successfully,
     the button reads `✓ Done` for ~1.2 seconds.
   - The Output tab refreshes with the run result during the same
     moment (it doesn't wait for the flash to clear).
   - After ~1.2s, the button returns to `▶ Run on this clip · v{n}`.

4. **Run button — cancel mid-run**
   - Start a fresh run. While the elapsed label is between 0:01
     and 0:05, click the Run button again.
   - Button shows `⟳ Cancelling…` very briefly, then returns to
     `▶ Run on this clip · v{n}`.
   - The Output tab refreshes; depending on the race, it shows
     either the empty "No run yet" state (if the studio_run was
     still pending) or the completed result.
   - The associated job's status (visible via `/api/jobs?…` or
     by reloading the page) is `cancelled`.

5. **Run button — no ✓ Done on error**
   - Trigger a run that will fail. (Easiest: temporarily break the
     Gemini API key in `.env`, restart, then click Run.)
   - Button switches `running → idle` *without* the ✓ Done flash.
   - The Output tab shows the `.run-error` block — error name
     bold in `--bad`, message body in monospace `--text`, message
     selectable and wraps on a small viewport.

6. **Empty states — all four shapes**
   - Open `/studio` with no `?prompt_id=` → no prompt selected,
     left column is empty, right column collapsed (no version to
     run).
   - Add `?prompt_id=N` (with N a real prompt) but DO NOT focus a
     clip. The Output tab shows `Click a clip in a folder to
     focus it.` inside a `.run-empty` shell.
   - Focus a clip with no prior run → Output tab shows
     `No run yet. Hit Run to execute v{n} on the focused clip.`
     inside a `.run-empty` shell.
   - Trigger a run that errors (see flow 5) → Output tab shows
     `.run-error` shell.
   - Across all four states the player slot stays present and
     the run-button label remains correctly bound.

7. **Visual — range overlay colors via tokens**
   - With cur + cmp visible, the player overlay shows two
     stacked range rows. The cur (top) row is a translucent
     light-blue (`--info` at 45% opacity); the cmp (bottom) row
     is a translucent orange (`--accent` at 45% opacity).
   - The legend below the transport mirrors those colors (
     `--info` for the cur legend dot, `--accent` for cmp).
   - In dev tools, inspect a `.ranges.range-cur .range` element
     and confirm its background is `var(--range-cur)`, not a
     raw rgba string.

8. **Visual — picker dropdown uses real tokens**
   - On either prompt-card, click the version chip. The dropdown
     opens.
   - Hovered items show `--hover` background. The active
     (currently-selected) item shows `--accent-2`. Neither
     resolves to a fallback hex; in dev tools, both rules
     reference `var(--hover)` and `var(--accent-2)`.
   - Focus moves through items with Tab; focused item has the
     accent outline ring.

9. **Visual — diff highlights**
   - With cur + cmp visible and a run on each, toggle
     `Diff vs v{cur}` on the cmp card.
   - Del rows show a faint red wash (the `--bad`-derived mix);
     ins rows show a faint green wash (the `--good`-derived mix).
   - Inspect either highlighted cell — the rule references
     `color-mix(in oklab, var(--bad)…)` / `var(--good)…`, not
     a raw rgba.

10. **Visual — folder list polish**
    - Left column: the "+ new" folder input is the standard
      `.field` height and font size (consistent with other
      inputs on the page). The `+ new` button is `.btn.sm.primary`
      (no `.mini`).
    - Empty-state texts ("No folders yet. Create one above.",
      "Empty folder.") are inside `.muted` text without inline
      padding styles — the surrounding container provides spacing.

11. **Visual — focus rings**
    - Tab through the page from the URL bar:
      - Folder list "+ new" input → standard `.field:focus-visible`
        accent outline.
      - "+ new" button → standard `.btn:focus-visible` accent
        outline.
      - Clip card → 2px `--accent` outline with 1px offset.
      - Version chip on either card → same accent outline.
      - Version-picker item → same accent outline.
      - Run button → standard `.btn.primary:focus-visible` outline.

12. **Regression — clip detail unchanged**
    - Open any clip's detail page (`/clips/{id}`). Player works
      as before — timeline, marker ranges, draft-ranges, playhead.
      Anno panels show Markers / Fields / Notes / History. No
      visual or behavioral diff vs `main` before PR3.

13. **Regression — review-mode pages unchanged**
    - Open `/clips?review=1` (or whichever URL triggers
      review-mode). Action bar background and border still use
      the tokens they did before; nothing renders pink or with
      a default browser style because a phantom token was
      replaced.

## Risks & mitigations

- **Risk:** Replacing `var(--bg-3, #1f1f1f)` etc. silently shifts
  colors because the hex fallback was always what rendered.
  **Mitigation:** Each swap is a deliberate pick of the *closest*
  existing token (`--bg-3 → --hover`; `--accent-fade → --accent-2`;
  `--border → --line`; `--fg-muted → --text-3`). The replacements
  are visually close but not identical — that's the point of the
  polish. Manual flow 7–11 verifies every consumer renders
  correctly.

- **Risk:** Cancel doesn't actually stop the in-flight Gemini call
  if it's already running, so the user clicks "cancel" and then
  sees the Output tab populate a moment later with a result.
  **Mitigation:** Documented behavior; flow 4 names this race.
  Aligning with the umbrella spec's "cancel comes free from the
  jobs pipeline" — the jobs pipeline is intentionally
  cooperative-cancel. Adding mid-Gemini preemption is out of scope.

- **Risk:** The 1Hz ticker makes the displayed elapsed value lag
  behind reality by up to a second.
  **Mitigation:** Acceptable — the display unit is seconds, so
  off-by-one-frame is invisible. The 500ms tick caused visible
  jitter when the label flipped mid-frame and was net-worse.

- **Risk:** The done-flash logic interleaves with `pendingRunSwap`
  in a way that flashes ✓ after the user has navigated away.
  **Mitigation:** `doneFlashUntilMs` is set inside
  `runOnFocusedClip()`'s success branch, then the next ticker
  firing clears it. The flash is bound to the current Alpine
  `studioPage` instance — when navigation happens, the instance
  unmounts and the timer is GC'd. No persistence across pages.

- **Risk:** The state-machine pure-Python mirror diverges from the
  JS implementation, masking a real bug.
  **Mitigation:** Same risk PR2 took with `lineDiff`; mitigation
  is the same — keep both implementations short, put them next
  to each other in the same commit, and review them together.

## Slicing

PR3 lands as a single PR with the following commit boundaries
(suggested, for reviewer convenience):

1. **CSS token plumbing.** Add `--range-cur`, `--range-cmp` to
   `:root`. Replace phantom-token fallbacks. Replace raw rgba
   range/diff colors with tokens. Run the CSS audit tests.
2. **`.run-empty` / `.run-error` styling.** Add the new CSS rules.
   Update the `_studio_run_output.html` markup to remove
   `.muted` from the empty divs where redundant.
3. **Run-button state machine.** Add `runJobId`, `cancelling`,
   `doneFlashUntilMs` to `studioPage`. Add `runButtonLabel()`,
   `runOrCancel()`, `cancel()`. Update `runOnFocusedClip()` to
   capture `job_id` and set the done flash on success. Update
   `_studio_header.html` bindings.
4. **Pure-Python state-machine mirror.** `tests/_helpers/studio_state.py`
   + unit tests.
5. **Folder list refactor.** `_studio_folder_list.html` to
   `ui.field()` / `ui.button()`. Add `.studio-folder-new` class.
6. **Focus-visible outlines.** Add focus-visible rules to the
   studio block in `app.css`.
7. **Manual acceptance flows + ADR + docs/decisions.md update.**

If any of these split poorly under TDD, the implementation plan
will re-slice them; this list is a hint, not a contract.

## Open questions

None blocking.

- Whether to also add a `+ Compare` keyboard shortcut while we're
  in the Studio polish neighborhood — deferred. Keyboards aren't a
  polish task; if there's appetite, file a separate spec.
- Whether `.run-error` should auto-collapse very long error
  messages (CatDV errors can be JSON-blobby). For now they wrap
  and stay selectable; a "show full / show less" toggle is a
  future ask if it ever bites.
- Whether the ✓ Done flash should also fire on `cancelled`-status
  runs. Decided no above (locked decisions); revisit if flow 4
  feels wrong in practice.
