# Goal — finish Prompt Studio (PR3 polish)

Ship **PR3 (polish)** in `catdv-annotator`. PR1, PR2, and the post-PR2
review_items + shared-player-chrome refactors are on `main`. PR3 is the
only umbrella-spec slice left.

## Read first (in order)
- `CLAUDE.md` — CatDV seat discipline, `.venv` rule, "explore before
  implementing", ADR + manual-acceptance-flows requirements.
- `docs/specs/2026-05-26-prompt-studio-design.md` — umbrella; PR3 is
  the third bullet list under "Slicing into PRs".
- `docs/specs/2026-05-26-prompt-studio-pr2-design.md`
- `docs/plans/2026-05-27-prompt-studio-pr2.md` — mirror this shape.
- ADRs `0033`, `0034`, `0037`, `0038`.
- `docs/design-language.md`, `backend/app/static/app.css` (`:root`,
  `.btn`, `.field`, `.pill`), `backend/app/templates/components/_ui.html`.
- Studio code: `backend/app/templates/pages/studio.html`, `_studio_*.html`,
  `_player*.html`; `backend/app/static/{studio,studio-diff,player,format}.js`;
  `backend/app/routes/pages/studio.py`, `backend/app/routes/studio.py`.

## Required sub-skills (superpowers plugin)
Run in order, each its own pass:
1. `superpowers:brainstorming` — align concrete PR3 deliverables; if
   anything is ambiguous, park it in the spec's Open Questions.
2. `superpowers:writing-plans` — author
   `docs/specs/2026-05-28-prompt-studio-pr3-design.md` (must end with a
   numbered **Manual acceptance flows** section), then
   `docs/plans/2026-05-28-prompt-studio-pr3.md` (task-by-task with TDD
   `- [ ]` checkboxes, same shape as the PR2 plan).
3. `superpowers:executing-plans` (or `subagent-driven-development` for
   independent tasks) — red test → verify red → minimal impl → green →
   commit, small commits.
4. `superpowers:verification-before-completion` — `.venv/bin/python -m
   pytest -x -q` AND walk every manual acceptance flow on a running
   dev server. Green tests alone are not "done".
5. `superpowers:finishing-a-development-branch` — open the PR.

Finish by writing `docs/adr/0039-prompt-studio-pr3-polish.md` (MADR-lite,
see existing ADRs for shape) and appending the entry to
`docs/decisions.md`.

## Scope (PR3)
1. **Run-button state machine**: elapsed-time ticker tightened;
   `✓ Done` flash (~1.2s) on `running → idle`; clicking Run while
   running cancels the job (verify the existing jobs cancel route; add
   one only if missing). Idle label `▶ Run on this clip · v{n}`,
   running `⟳ Running… (M:SS)`.
2. **Empty/error-state polish** across the right pane — no-prompt,
   no-clip, no-run, last-run-error. Use design-language primitives
   only (`muted`, `.pill`, `.btn`, `--danger`/`--panel-2`/`--text-2`).
   Errors wrap and are selectable. Run-in-progress must not collapse
   the player.
3. **Visual audit** against `docs/design-language.md` (NOT the React
   prototype — `CLAUDE.md`'s "explore before implementing" applies).
   Replace hand-rolled buttons, raw hex, inline `style=`, ad-hoc
   fields, `padStart` timecodes with `.btn`/`{{ ui.button(...) }}`,
   tokens, `{{ ui.field(...) }}`, `window.fmtTimecode(...)`. Re-check
   `.pc-vchip`, `.pc-vmenu`, `.pc-diff`, `.cmp-card`, `.range-*` for
   token use. Hover/focus/active states across header, chips,
   dropdown, compare button, diff toggle, clip cards, folder rows.

## Non-goals
No schema changes, no new endpoints (except cancel if missing), no
React-prototype port, no batch runs, no history viewer, no
stacked/unified diff layouts.

## Server discipline
Use the `server-start` / `server-stop` skills — single-instance +
SIGTERM. Never `kill -9`. On `502 Maximum:2`, wait or kick; don't retry.

## Definition of done
- Spec + plan + ADR written; `docs/decisions.md` updated.
- `.venv/bin/python -m pytest -x -q` green.
- Every acceptance flow walked on a live dev server with evidence.
- PR opened off `polish/prompt-studio-pr3` against `main`.

Default to reuse over parallel-evolving; if you extract, cite paths.
