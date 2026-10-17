# Quality gates and agent workflow hardening

**Date:** 2026-10-17
**Status:** Proposed
**Owner:** Peter Hora
**Measured at:** `main` @ `530487d` (#143)

## For the implementing agent

Read `AGENTS.md` first; everything there still applies. This spec is
split into independent PRs (A–I below). Do **one section per PR**, in the
order given under *Rollout*, each on its own short-lived branch.

- Sections marked **HUMAN** are GitHub settings or decisions only Peter
  can make. Don't attempt them; list them in your PR description as
  "needs Peter".
- Don't loosen any threshold, baseline or contract in these PRs. Where
  this spec says "re-baseline", it means **lowering** to the measured
  value, never raising.
- When a step says "verify first", run the command and paste the output
  into the PR description before changing anything.
- If something in the repo contradicts this spec (it was written from a
  read-only clone), stop and ask rather than guessing.

## Problem

The repo already has most of what agent-heavy development needs: a short
`AGENTS.md`, `docs/architecture-invariants.md` as the canon, import
contracts, guard tests, a structural-erosion ratchet, and specs with
acceptance flows. What it lacks is mechanics that stop the gates from
being loosened or bypassed, and a loop that pays complexity down rather
than only holding the line.

Our own history shows the failure mode. While the gates ran only in
pre-commit (honour system), erosion drifted from 0.423 to 0.442 over
about 12 PRs, and #120 had to re-baseline it to 0.435. Since CI started
enforcing the gate, erosion went from 0.436 to 0.427 across 20 PRs. The
gate works when it's enforced. It doesn't yet tighten, and the agent can
still edit the files that define it.

This matches the external evidence. SlopCodeBench (arXiv:2603.24755)
found that prompting for quality improves the first draft but doesn't
slow degradation across iterations; only external measurement does.

For calibration: 0.427 is inside the human upper band (0.41–0.46) cited
in `docs/specs/2026-06-07-erosion-detection-ci-design.md`. The codebase
is not in bad shape. The issue is trend mechanics, not the current level.

## Current state (measured 2026-10-17)

| Area | Finding |
|---|---|
| Erosion | 0.427 measured vs 0.4354 baseline + 0.005 tolerance: ~0.013 of slack that can be re-spent silently |
| Max CC | Cap 30, actual max 29: the cap exerts no pressure |
| Worst function | `services/annotator.py::_process_item`, CC 29, 291 SLOC (highest complexity mass). `AGENTS.md` and invariant #4 cite it as the **canonical example** to copy |
| Type errors | basedpyright baseline went 292 → 1,239 grandfathered errors in #142 (version pin). No ratchet-down |
| Honour-system gates | `interrogate` and pylint `duplicate-code` run in pre-commit only, not in CI. Duplication is fine today (0 at 14 lines, 4 at 8 lines) |
| Gate ownership | Both re-baselines (#120 erosion, #142 types) were agent-authored. Reasoned, but the agent controls its own gate |
| Branch protection | `development-workflow.md` says `main` protection is "convention only" |
| Agent hooks | Only `SessionStart` (venv setup). No stop-time gate, no protected files |
| Invariants | 14 of 29 rely at least partly on convention, including #4 (cache layers) and #5 (offline degradation) |
| Frontend | ~12.5k lines of JS/CSS/templates (`app.css` 2,925) outside complexity and duplication gates; guard tests cover banned patterns only |
| Stale docs | 3 plans from merged PRs remain in `docs/plans/` against the folder's own rule; `gh-handoff` documents the routine model as `claude-opus-4-8[1m]` (as of 2026-08-19) |
| PR size | 4 of the last 20 PRs added >1,500 lines (#126, #127, #139; #142 is mostly the baseline file) |

## Goals

1. Gates only ever tighten, and only a human can loosen them.
2. Nothing that guards quality runs on the honour system.
3. An agent session can't end with the fast gates red.
4. Complexity is paid down on a cadence, not only prevented.
5. Docs never point agents at eroded code as the pattern to follow.

## Non-goals

- More process ceremony per PR. Review bandwidth is the constraint;
  every change here is mechanical and needs no extra human attention
  per PR.
- Re-introducing Superpowers or another workflow plugin (retired in #120
  for good reasons).
- Changing the trunk-based flow, spec/plan conventions or the
  `gh-design` → `gh-handoff` path.

## Changes

### A. Branch protection — HUMAN

In GitHub → Settings → Branches → `main`:

- Require a pull request before merging; at least 1 approval.
- Require status checks: the `lint` and `test` jobs from `ci.yml`.
- Require branches to be up to date before merging.
- Require review from Code Owners (takes effect after PR B).
- Block force pushes and deletions.

Then update `development-workflow.md` to drop "until then it is
convention only" (the agent can do this doc edit in PR G once Peter
confirms).

### B. Human-owned gate files (CODEOWNERS + ADR)

Add `.github/CODEOWNERS` making Peter the owner of the files that define
the gates:

- `.erosion-baseline.json`, `tools/erosion_gate.py`
- `.basedpyright/baseline.json`
- `.importlinter`
- `.pre-commit-config.yaml`, `.github/workflows/`
- `.github/CODEOWNERS` itself
- the guard tests listed in `AGENTS.md` (`tests/unit/test_*guard*.py`,
  `test_no_*.py`, `test_*single*.py`, `test_templates_shared.py`,
  `test_context_delegation.py`)
- `docs/architecture-invariants.md`, `AGENTS.md`, `CLAUDE.md`
- `pyproject.toml` (holds ruff, interrogate and basedpyright settings;
  trade-off: dependency bumps will also need Peter's review)

Write ADR 0129 *"Quality gates only tighten; gate files are
human-owned"*, index it in `docs/decisions.md`, and add a matching
invariant to `docs/architecture-invariants.md` (enforced by: CODEOWNERS,
the hook in E, the checks in C).

**Acceptance:** CODEOWNERS parses (GitHub shows no errors on the file);
ADR and invariant merged.

### C. Ratchets that tighten

**Erosion.** Extend `tools/erosion_gate.py`:

1. *Stale-baseline check:* fail when `baseline − measured` exceeds a
   small slack (proposed 0.003), with a message telling the author to
   run `--write-baseline` and commit the lower value. Improvements then
   lock in within the PR that made them.
2. *Max-CC ratchet:* fail when measured `max_cc` exceeds the baseline's
   `max_cc`. Keep `--max-cc 30` as an absolute backstop.
3. *Touched-function rule:* fail when any function present at the
   merge-base and changed in this PR has a higher CC than before. New
   functions are governed by the CC-10 threshold through the erosion
   ratio as today.
4. Lower the baseline to the measured value (0.427 / 29 at time of
   writing; verify first).

Add unit tests for each new rule in the gate's existing test file (or a
new `tests/unit/test_erosion_gate_*.py`).

**Types.** Add a CI step that fails when the number of entries in
`.basedpyright/baseline.json` is higher than at the merge-base. Record
the current count (1,239; verify first) in the ADR from B as the
starting point. No target date for reduction; the refactor loop in H
can pick type errors off opportunistically.

**Acceptance:** a PR that raises erosion, raises max CC, raises a touched
function's CC, or grows the type baseline fails CI with a message naming
the offender. A PR that lowers erosion without lowering the baseline
fails with the re-baseline instruction.

### D. Move honour-system gates into CI

Add `interrogate -c pyproject.toml` and the pylint `duplicate-code` check
(same arguments as `.pre-commit-config.yaml`) to the `lint` job in
`ci.yml`. Verify first that both pass on `main`; if `interrogate` fails,
stop and report the numbers instead of lowering `fail-under`.

**Acceptance:** both appear as steps in the `lint` job and pass on the PR.

### E. Agent-side hooks

Extend `.claude/settings.json` (keep `SessionStart` as is). Hook scripts
go in `.claude/hooks/` alongside `session-start.sh`.

1. **Protect gate files (before Edit/Write/MultiEdit).** Block edits to
   any path owned in CODEOWNERS (read the list from CODEOWNERS so there's
   one source of truth). The block message tells the agent to stop and
   ask Peter, and names the file. Exception: `.erosion-baseline.json`
   when the only change is lowering values (C makes this a routine
   step); if that's awkward to detect in a hook, block it too and let
   CODEOWNERS review cover it.
2. **Stop-time gate.** When a session tries to finish and Python files
   changed relative to the merge-base, run the fast gates: `ruff check`
   and `ruff format --check` on changed files, `lint-imports`, the
   erosion gate, and pytest on test files matching the changed modules.
   On failure, return the output so the agent keeps working. Requirements:
   - respect the stop-hook re-entry flag so it can't loop forever;
   - stay under ~60 s, otherwise skip pytest and say so;
   - never start the dev server or touch CatDV (no seat use);
   - do nothing when no Python changed.

**Acceptance:** see manual flows 1 and 2.

### F. Refactor `_process_item` and fix the canonical pointer

Behaviour-preserving refactor of
`services/annotator.py::_process_item` (CC 29, 291 SLOC):

- Extract the cache-layer sequence (`proxy_resolver.path_for_clip_id` →
  `ai_store.status` / `ensure_uploaded` → `thumbnail_service.get_or_fetch`,
  each miss degrading with a layer-named error) into one small, named,
  documented helper. That helper becomes the canonical example.
- Target: `_process_item` CC ≤ 15; the helper CC ≤ 10.
- Existing tests must pass unchanged; add unit tests for the helper.
- Update `AGENTS.md` ("Clip media goes through the cache layers") and
  invariant #4 in `docs/architecture-invariants.md` to point at the
  helper. Pointer change only; this is not an invariant change and
  needs no ADR.
- Add a guard test: every `path::function` cited as canonical in
  `AGENTS.md` or the invariants page exists and has CC ≤ 10, so the docs
  can't drift back to pointing at eroded code.
- Lower `.erosion-baseline.json` in the same PR (C makes this required).

**Acceptance:** CC targets met per the erosion gate's output; full suite
and `/e2e` walkthroughs pass.

### G. Workflow and doc hygiene

- Delete the three plans for merged PRs:
  `docs/plans/2026-10-14-multiple-catalogs.md`,
  `docs/plans/2026-10-14-writeback-redesign-ai-markers.md`,
  `docs/plans/2026-10-17-batches-redesign.md` (verify each PR merged).
- `gh-handoff` SKILL.md: re-check the live `implement-issue` routine
  config and update the documented model and date. **HUMAN:** Peter
  confirms which model the routine should run (current working pattern
  is Opus designs, Sonnet implements).
- `development-workflow.md`: add a **Maintenance loop** section
  describing H, update the branch-protection line after A, and add a
  PR-size guideline: aim for under ~600 changed lines excluding
  generated and baseline files; split larger work into stacked PRs as
  the doc already describes.
- Add a guard test: every test or tool path cited in an *Enforced by:*
  line of `docs/architecture-invariants.md` exists. Cheap, and it turns
  doc drift into a red build.

### H. Scheduled maintenance loop

Create a weekly scheduled routine (same mechanism as `implement-issue`)
named `refactor-hotspot`. Its prompt, in substance:

1. Run the erosion gate; take the highest-mass offender not touched by
   any PR in the last 7 days (skip ones with open PRs).
2. Behaviour-preserving extraction only: no feature changes, existing
   tests unchanged, new tests for extracted helpers allowed.
3. One PR, labelled `refactor`, with before/after CC and erosion in the
   description and the baseline lowered.
4. Never touch the CatDV seat; run the full suite and `/e2e`.

**HUMAN:** Peter approves the routine's creation and its model.

**Acceptance:** first run produces one mergeable PR that lowers erosion.

### I. Convert convention-only invariants (later)

Lower priority; do after A–H. Candidates, each as its own PR:

- **#4 cache layers:** a source-grep guard that `catdv.download_*` and
  `_gcs_service` are only referenced inside the cache-layer modules.
- Walk the other convention-only invariants and list which can become a
  grep, import-linter or type rule; propose them in one issue rather
  than implementing all at once.

## Open questions for Peter

1. **Frontend gate.** ~12.5k lines of JS/CSS/templates have no
   complexity or duplication gate. Invariant #1 rules out Node tooling.
   Options: (a) a Python script that ratchets file and function length
   for `static/*.js`; (b) accept the risk and rely on the design-language
   guards; (c) revisit later. Recommendation: (a), small and in keeping
   with the existing gates.
2. **Slack values.** 0.003 stale-baseline slack and 0.005 tolerance are
   proposals; confirm or adjust.
3. **CODEOWNERS on `pyproject.toml`.** Worth the extra review on
   dependency bumps, or split the tool config into separate files first?

## Rollout

| Order | Section | Depends on | Who |
|---|---|---|---|
| 1 | A Branch protection | — | Peter |
| 2 | B CODEOWNERS + ADR | — | agent |
| 2 | D Gates into CI | — | agent |
| 3 | C Tightening ratchets | B | agent |
| 4 | E Agent hooks | B | agent |
| 5 | F Refactor `_process_item` | C | agent |
| 5 | G Hygiene | A (for the protection line) | agent + Peter |
| 6 | H Maintenance loop | C, F | agent + Peter |
| 7 | I Convention → guards | — | agent |

## Manual acceptance flows

1. **Protected file is blocked in a session.** Start a Claude Code
   session in the repo and ask the agent to raise `max_cc` in
   `.erosion-baseline.json`. The edit is blocked, and the agent reports
   that the file is human-owned and asks Peter.
2. **Session can't finish red.** In a session, ask the agent to add a
   function with CC > 30 to any service and then stop. The stop-time hook
   returns the erosion-gate failure and the agent continues working
   instead of ending.
3. **CI rejects a looser gate.** Open a PR that raises the erosion
   baseline value. CI may pass, but the PR shows Peter as a required
   reviewer and can't merge without his approval.
4. **CI rejects drift.** Open a PR that makes an existing function more
   complex (add branches to it). The `lint` job fails and names the
   function and its old/new CC.
5. **Improvements lock in.** Open a PR that extracts a helper and lowers
   erosion without touching the baseline. CI fails with the instruction
   to run `--write-baseline`; after committing the lower baseline it
   passes.
6. **Honour-system gates hold.** Open a PR that removes a module
   docstring from enough files to drop `interrogate` below `fail-under`.
   The `lint` job fails.
7. **Canonical pointer is guarded.** Change the canonical reference in
   `AGENTS.md` to a function with CC > 10. The guard test fails.
8. **Maintenance loop runs.** Trigger `refactor-hotspot` manually. It
   opens one `refactor` PR with before/after numbers and a lowered
   baseline, and CI is green.
