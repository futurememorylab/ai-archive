# 0039. Prompt Studio PR3 — Polish

**Date:** 2026-05-28
**Status:** Accepted
**Lifespan:** Feature

## Context

PR1 (shell + single-clip run loop), PR2 (version-compare prompt-card +
line diffs + range overlay), and the post-PR2 refactors (shared player
chrome, output via review_items, focused-clip-in-URL) are merged.
The umbrella spec's third bucket — polish — was left to PR3.

Three concrete polish needs:

1. The Run button has no cancel affordance even though the underlying
   jobs pipeline already supports cancellation
   (`POST /api/jobs/{job_id}/cancel`).
2. Empty/error states across the right pane render as plain `.muted`
   text with no shared shape, and error messages don't wrap or
   user-select cleanly.
3. The PR2-introduced CSS (`.pc-vchip`, `.pc-vmenu`, `.pc-diff`,
   `.cmp-card`, range overlays) predates the consolidation of
   `docs/design-language.md` as the source of truth and references
   tokens that don't exist (`--bg-3`, `--accent-fade`, `--border`,
   `--fg-muted`) with raw hex fallbacks that silently override.

## Alternatives

**A. Add a separate Cancel button next to Run.** Rejected. Two
buttons for one operation widen the surface; the umbrella spec
positions the Run button as "the single live control" for the
focused clip. The annotator already supports cooperative cancel
between job items, so reusing the same click target — dispatching
on the `running` flag — is a one-method addition.

**B. Define the cur/cmp range colors as new semantic tokens
(`--info-strong`, `--accent-strong`).** Rejected. The two colors
are studio-specific affordances — they live above the timeline,
not in semantic UI roles. Naming them `--range-cur` / `--range-cmp`
documents their purpose; using `color-mix(in oklab, var(--info) 45%,
transparent)` keeps them tracked to the palette without inventing a
parallel naming scheme.

**C. Port the React prototype's `styles.css` verbatim.** Rejected
per CLAUDE.md "Frontend: explore before implementing" — the design
language has diverged on purpose since PR1. The prototype is a
reference of intent; the in-codebase tokens are the source of
truth.

**D. Add a server-side `is_cancellable` boolean to the studio run
response so the frontend can decide.** Rejected. The frontend
already knows the state (`running === true` ⇒ cancellable). Adding
a server hint is wire-format churn for no information gain.

**E. Make cancel preempt the in-flight Gemini call mid-stream.**
Rejected as out of scope. The annotator's cooperative cancel checks
between job items; a single-clip Studio run is one item, so cancel
won't interrupt the live Gemini call. Documented as a known race
in the spec (manual flow 4) — clicking cancel during a 5-second
Gemini reply may land after the result completes; the frontend
treats that as a non-flash transition back to idle.

## Decision

1. **Cancel via existing jobs endpoint.** `POST /api/studio/runs`
   already returns `job_id`; the frontend captures it, and clicking
   the Run button while `running === true` issues
   `POST /api/jobs/{job_id}/cancel`. No new server route.

2. **`✓ Done` flash on success only.** A `doneFlashUntilMs`
   timestamp on `studioPage()`, cleared by the 1Hz ticker. No flash
   on error or cancelled status — error is communicated via the
   `.run-error` shell in the Output partial; cancelled is silent.

3. **Pure-Python mirror of `runButtonLabel()`** at
   `tests/_helpers/studio_state.py`, kept short enough to be
   verbatim-equivalent to the JS in `studio.js`. Same pattern PR2
   used for `lineDiff()`.

4. **`.run-empty` / `.run-error` gain dedicated CSS rules.** Error
   messages are mono (`--f-mono`), pre-wrap, word-break, and
   user-selectable. Error shell uses `--panel-2` with a `--bad`
   left border.

5. **Two new `:root` tokens — `--range-cur`, `--range-cmp` —
   defined via `color-mix(in oklab, var(--info|--accent) 45%,
   transparent)`.** Same idiom as `--accent-2`. The legend dots
   reuse the source tokens at full opacity.

6. **Phantom-token fallbacks deleted.** Each replaced with the
   closest existing token:
   - `--bg-3 → --hover` (in `.pc-vmenu-item:hover`)
   - `--accent-fade → --accent-2` (in `.pc-vmenu-item.is-current`,
     `.pc-hdr .btn.active`)
   - `--border → --line` (in `.pc-vmenu`, `.pc-diff-table td`,
     `.review-actionbar`)
   - `--fg-muted → --text-3` (in `.review-progress`,
     `.review-item-toggle`)

   Audit gate in `tests/unit/test_studio_css_no_phantom_tokens.py`
   prevents regression.

7. **Raw rgba diff/range colors → `color-mix()` from palette.**
   `.diff-row.diff-del .diff-cell-a`,
   `.diff-row.diff-ins .diff-cell-b`,
   `.ranges.range-cur .range`, `.ranges.range-cmp .range`,
   `.legend-range-cur`, `.legend-range-cmp` all reference tokens
   now. The same audit gate bans the four originating rgba strings.

8. **Folder list refactor.** New-folder input → `.txt sm`; "+ New
   folder" / "Create" buttons → `.btn sm`; inline `style="..."`
   wrapper → `.studio-folder-new` class. The undefined `.mini`
   button modifier (which never had CSS rules) is removed in favor
   of the canonical `.sm` size.

9. **`focus-visible` outlines** on PR2 surfaces — version chip
   button, picker dropdown items, diff-toggle, clip cards — share
   one rule using `--accent`.

## Consequences

- The Run button is now stateful (idle / running / cancelling /
  done). The state machine has eight cases; the unit-test mirror
  covers all of them.
- The cancel flow is cooperative: clicking cancel while
  Gemini is mid-call leaves the studio_run in whatever terminal
  state it eventually reaches. Documented in the spec (flow 4)
  and in the spec's Risks section.
- Visual changes are subtle but real. Phantom-token swaps shift
  several surface colors by a small amount (e.g. `--bg-3` fallback
  `#1f1f1f` → `--hover` `rgba(255,255,255,0.04)` on a dark `--panel`
  is darker than the hex). The manual acceptance flows verify
  every consumer visually.
- The audit gate (`test_studio_css_no_phantom_tokens.py`) is the
  durable contract: any future developer who wires up
  `var(--border, …)` again or pastes back a raw rgba range color
  fails CI. The gate currently lists the four phantom tokens and
  four banned rgba strings; new ones can be appended as they're
  discovered.
- PR3 is the final umbrella slice. The umbrella spec
  (`docs/specs/2026-05-26-prompt-studio-design.md`) closes with
  PR3 shipped; there is no PR4.
