# Structural Erosion Pre-commit Gate

**Date:** 2026-06-07
**Status:** Proposed

## Problem

The test suite is blind to one failure mode: tests stay green while complexity
quietly concentrates into a few monstrous functions. Over time a codebase
erodes — most of its complexity mass migrates into a handful of high-CC
functions — without any single commit tripping a test. We want a deterministic,
auditable gate that measures that concentration directly and refuses commits
that make it worse.

This follows the SlopCodeBench "structural erosion" definition
(arXiv:2603.24755): the share of total complexity mass held by high-complexity
functions, where `mass(f) = cyclomatic_complexity(f) × sqrt(source_lines(f))`.

## Current state (measured 2026-06-07)

Run against `backend/` (115 files, 698 deduped callables):

| Metric | Value | Reference band |
|---|---|---|
| Erosion ratio | **0.410** | human median ~0.31, human upper end 0.41–0.46, agent avg ~0.68 |
| Max function CC | **25** | Radon "risky" boundary is CC 10 |
| Functions with CC > 10 | 44 | — |

> **Correction (PoC, 2026-06-07):** an earlier measurement reported 0.373 / 56
> functions. That run double-counted methods — `radon.cc_visit` returns every
> method **twice** (once as a top-level `Function` with `is_method=True`, again
> inside its `Class.methods`). Deduping by `(path, name, lineno)` gives the true
> figures above (0.4099 → 0.410). The corrected ratio sits in the documented
> **human upper-end band (0.41–0.46, scipy/scikit-learn territory)** — still
> healthy, far from the agent-average 0.68, but the baseline MUST be 0.410, not
> 0.373, or the gate would fail on a clean tree.

The codebase is in the healthy human band. The worst offenders today:

```
mass=254.0 cc=24 sloc=112  backend/app/routes/pages/clips.py:175       clips_list()
mass=239.8 cc=25 sloc=92   backend/app/services/cache_inspector.py:129 CacheInspector.status_for_clips()
mass=239.2 cc=17 sloc=198  backend/app/context.py:395                  _build_archive_subsystem()
mass=237.9 cc=23 sloc=107  backend/app/services/sync_engine.py:143     SyncEngine._tick()
mass=209.2 cc=25 sloc=70   backend/app/routes/pages/studio.py:30       studio_page()
```

Because we are already healthy, a **ratchet** locks in the good state rather
than forcing an up-front refactor.

**Where the erosion lives (concentration analysis).** It is moderately
concentrated, not systemic. Of the 44 high-CC functions: 25 sit at CC 11–15,
15 at CC 16–20, 4 at CC 21–25, and **none above 25**. The eroded mass clusters
in two surfaces:

- `app/routes/pages` — ratio ~0.61 (`clips.py`, `studio.py`, `prompts.py`)
- `app/archive/providers` — ratio ~0.61 (fs + catdv adapters: `media_probe`,
  `apply_changes`, `payload`)

`app/repositories/*` is **0.00** across the board — the "repos are leaves"
discipline shows up cleanly. Because the mass is concentrated in a handful of
fan-out handlers/adapters (not diffused everywhere), opt-in burn-down later is
cheap: refactoring the top three (`clips_list`, `status_for_clips`,
`studio_page`) below CC 11 would claw back roughly 0.41 → ~0.355.

## Goals / Non-goals

**Goals**
- Fail a commit when erosion rises above a committed baseline (+ small tolerance).
- Fail a commit when any single function exceeds a hard CC cap.
- Catch *new* large copy-paste clones (duplication).
- Print the top offenders so the developer knows exactly what to simplify.
- Fit the repo's existing convention: local pre-commit hooks pointing at `.venv/bin`.

**Non-goals**
- No GitHub Actions / server-side CI (the repo has none today; out of scope).
- No correctness, security, or business-logic checking — that stays with the
  test suite and cross-model review.
- No up-front refactor of the 56 high-CC functions. Burn-down is opt-in later.

## Architecture

Two independent pre-commit hooks, both pure-Python, both `pass_filenames: false`
(they scan the whole `backend/` tree, not just staged files, because erosion is
a whole-tree property).

### Component 1 — `tools/erosion_gate.py`

A self-contained script (the SlopCodeBench gate, adapted to this repo). It:

1. Walks `backend/**/*.py`, excluding `migrations`, `__pycache__`, `.venv`, etc.
2. Flattens Radon `cc_visit` blocks into individual functions/methods/closures.
3. Computes per-function `mass = CC × sqrt(SLOC)`, sums total mass and
   "eroded" mass (functions with CC > 10), reports the ratio and the max CC.
4. Sorts and prints the top N offenders with `path:line` locations.
5. Enforces gates and exits non-zero on violation.

Key adaptations from the source guide:
- Default `--path backend` (this repo has no `src/`).
- **Double-count guard (confirmed bug):** `radon.cc_visit` returns every method
  **twice** — once as a top-level `Function` block (`is_method=True`) and again
  inside its `Class.methods` list. A naive flatten therefore counts every method
  twice, inflating the denominator more than the numerator and *deflating* the
  ratio (this is what produced the bogus 0.373). The flatten MUST dedupe by
  `(path, name, lineno)`, keeping the top-level occurrence, and assert zero
  duplicate keys before computing the ratio. Unit test #3 below guards this.

CLI surface (unchanged from the guide except defaults):
`--path`, `--max-erosion`, `--max-cc`, `--baseline`, `--tolerance`,
`--write-baseline`, `--exclude`, `--top`.

### Component 2 — duplication via `pylint`

Pure-Python clone detection (no Node — `jscpd` is unreliable here because
non-interactive shells lack `node` on PATH):

```
pylint backend --disable=all --enable=duplicate-code
```

`pylint`'s `duplicate-code` is a **binary** check (it reports clone blocks above
`min-similarity-lines`; it cannot ratchet a percentage). So `min-similarity-lines`
is tuned during rollout to the smallest value at which **today's code passes**,
making the hook catch only *new* large clones. The chosen value and the rationale
are recorded in the ADR.

### Baseline file — `.erosion-baseline.json`

Committed at repo root (mirrors the `.basedpyright/baseline.json` pattern):

```json
{ "erosion": 0.410, "max_cc": 25 }
```

Written via `python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json --write-baseline`.
Refreshed (downward) after a burn-down refactor; raising it is a conscious,
reviewed act.

## Thresholds

| Gate | Setting | Rationale |
|---|---|---|
| Erosion ratchet | baseline `0.410`, `--tolerance 0.005` | "Never get worse"; matches basedpyright/interrogate ratchets. No absolute ceiling yet. |
| Hard CC cap | `--max-cc 30` | One notch above today's max (25). Allows minor growth in the four functions at 24–25 during normal work while still catching genuinely runaway functions. |
| Duplication | `pylint ... --enable=duplicate-code --min-similarity-lines=14` | Binary gate; 14 is the lowest value at which today's tree passes (0 reports). Catches new large clones only. |

Two real-but-sub-threshold clones exist today (the `run_job(...)` call block in
`routes/jobs.py` + `routes/studio.py`, and the `_loop()` start/stop lifecycle in
`connection_monitor.py` + `lru_eviction.py`). They surface at
`min-similarity-lines ≤ 12` but not at 14; the ADR will record them so a future
contributor doesn't mistake them for new regressions.

No absolute `--max-erosion` ceiling on day one — the ratchet is the gate. An
absolute `0.45` ceiling can be added later if/when burn-down brings the number
down and we want to prevent it climbing back.

## Files touched

- **add** `tools/erosion_gate.py`
- **add** `.erosion-baseline.json`
- **edit** `pyproject.toml` — add `radon` and `pylint` to `[project.optional-dependencies].dev`
- **edit** `.pre-commit-config.yaml` — two new `local` hooks
- **add** `tests/unit/test_erosion_gate.py` — see Testing
- **add** `docs/adr/0060-structural-erosion-gate.md` + row in `docs/decisions.md`

### Pre-commit hooks (appended to the existing `local` repo block)

```yaml
      - id: erosion-gate
        name: structural erosion gate (ratchet)
        entry: .venv/bin/python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json --max-cc 30
        language: system
        pass_filenames: false
        always_run: true

      - id: duplicate-code
        name: pylint duplicate-code
        entry: .venv/bin/pylint backend --disable=all --enable=duplicate-code --min-similarity-lines=14
        language: system
        pass_filenames: false
        always_run: true
```

## Testing (TDD)

Unit tests in `tests/unit/test_erosion_gate.py`, driving the script's functions
directly (not via subprocess) where possible:

1. **mass formula** — `mass(cc=4, sloc=9) == 12.0` (`4 × sqrt(9)`).
2. **erosion ratio on a fixture** — a synthetic source string with one CC>10
   function and one trivial function yields the expected ratio.
3. **no double-counting** — a class with methods + a function with a closure is
   counted exactly once each (guards the measurement-spike bug).
4. **ratchet pass/fail** — erosion at baseline+tolerance passes; above fails
   (exit code).
5. **max-cc gate** — a fixture with a CC>cap function fails; at/below passes.
6. **write-baseline** — `--write-baseline` produces a JSON file with the
   expected keys and exit 0.

Write the double-counting test first (red), then fix the flatten/dedupe.

## Rollout

1. Land the script + tests + deps.
2. Measure and commit `.erosion-baseline.json` (0.410, from the deduped run).
3. `min-similarity-lines=14` is the lowest value that passes today (PoC-verified);
   record the two known sub-threshold clones in the ADR.
4. Enable both hooks (no report-only phase — pre-commit always blocks; the
   baseline guarantees today's tree passes).
5. (Later, opt-in) Point `/simplify` at the printed top offenders, re-run the
   suite, then re-baseline lower.

## Manual acceptance flows

1. **Gate passes on a clean tree.** Setup: clean checkout on this branch,
   `.venv` with `radon`+`pylint`. Action: `pre-commit run --all-files`.
   Expected: `erosion-gate` and `duplicate-code` hooks both pass; erosion-gate
   prints `Erosion: 0.410` (±) and `OK: erosion ... within baseline`.

2. **A new complex function is blocked by the CC cap.** Setup: add a throwaway
   function to a `backend/` module with CC > 30 (e.g. ~31 nested branches).
   Action: `git add` it and `git commit`. Expected: commit is rejected;
   erosion-gate output ends with `FAIL: a function has CC 31 > cap 30` and lists
   the offending function with its `path:line`.

3. **Rising erosion is blocked by the ratchet.** Setup: add several CC-12–15
   functions across `backend/` so the aggregate ratio climbs past
   `0.410 + 0.005`. Action: `git commit`. Expected: rejected with
   `FAIL: erosion <n> exceeds baseline 0.410 (+0.005)`. Removing the functions
   makes the commit succeed.

4. **A new large clone is blocked.** Setup: copy a ~15-line block verbatim into
   a second `backend/` module (above `min-similarity-lines`). Action:
   `git commit`. Expected: `duplicate-code` hook fails and prints the duplicated
   block locations. Deleting the copy makes it pass.

5. **Re-baselining lowers the number.** Setup: simplify a top offender so the
   suite stays green and erosion drops. Action:
   `python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json --write-baseline`,
   then `pre-commit run erosion-gate --all-files`. Expected: baseline JSON shows
   the lower number; gate passes against the new, stricter baseline.
