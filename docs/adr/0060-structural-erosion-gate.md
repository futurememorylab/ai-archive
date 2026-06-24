# 0060. Structural erosion pre-commit gate (complexity-concentration ratchet)

**Date:** 2026-06-07
**Status:** Accepted
**Lifespan:** Invariant

Spec: `docs/specs/2026-06-07-erosion-detection-ci-design.md`.

## Context

The test suite is blind to gradual complexity concentration: tests stay green
while complexity migrates into a few high-CC functions. We want a deterministic
gate that measures this directly. The SlopCodeBench metric (arXiv:2603.24755)
defines structural erosion as the share of total complexity mass held by
functions with CC > 10, where `mass = CC * sqrt(SLOC)`.

A measurement spike first reported erosion 0.373, but `radon.cc_visit` returns
every method twice (once as a top-level `Function`, once inside its
`Class.methods`); deduping by `(path, name, lineno)` gives the true figure
**0.410** over 44 high-CC functions, max CC 25 — the human upper-end band
(scipy/scikit-learn territory), healthy and far from the agent-average 0.68.
Erosion concentrates in `routes/pages` and `archive/providers`; repositories
are at 0.00.

## Alternatives

- **Absolute erosion ceiling (e.g. 0.45).** Lets the number drift upward to the
  cap; misses the slope, which is the actual failure mode.
- **GitHub Actions enforcement.** The repo has no CI today; every quality gate
  is a local pre-commit hook. Adding the repo's first workflow for this one
  check was out of scope.
- **jscpd for duplication.** Node isn't reliably on PATH here (non-interactive
  shells lack it), so it would silently get skipped.
- **xenon for a hard CC ceiling.** Doesn't compute the erosion ratio, which is
  the signal we care about; the custom script does both.

## Decision

A self-contained `tools/erosion_gate.py` run as a local pre-commit hook,
**ratchet-only**: erosion must stay within `.erosion-baseline.json` (0.410)
+ tolerance 0.005, plus a hard `--max-cc 30` cap (one notch above today's 25).
No absolute `--max-erosion` ceiling on day one. Duplication is a second hook,
`pylint --enable=duplicate-code --min-similarity-lines=14` (the lowest value at
which today's tree passes). Two known sub-threshold clones exist and are
deliberately not flagged: the `run_job(...)` call block in `routes/jobs.py` +
`routes/studio.py`, and the `_loop()` start/stop lifecycle in
`connection_monitor.py` + `lru_eviction.py`. Re-baselining downward after a
burn-down is a conscious, reviewed act; raising the baseline is not allowed
without review.

## Consequences

- Erosion can hold or fall, never climb — matching the basedpyright/interrogate
  ratchet pattern already in the repo.
- A legitimate refactor that pushes a function past CC 30, or raises the overall
  ratio, blocks until the baseline is consciously updated.
- New large copy-paste clones (≥14 similar lines) are blocked.
- Two new dev dependencies: `radon`, `pylint`.
- Burn-down of the top offenders (`clips_list`, `status_for_clips`,
  `studio_page`) is opt-in follow-up that would drop the baseline to ~0.355.
- The analysis above measured 0.410 on the branch point. Merging `main`
  (PR #33, run telemetry) added new code that raised erosion to **0.423** and
  max CC to 26; since that code landed through normal review before this gate
  existed, the committed baseline was bumped to `0.4229` as part of that merge —
  a conscious, reviewed re-baseline, exactly the path this ADR prescribes.
