# ADR 0129 — Quality gates only tighten; gate files are human-owned

Date: 2026-10-17 · Status: accepted · Lifespan: Invariant ·
Spec: `docs/specs/2026-10-17-quality-gates-hardening-design.md`

## Context

Most of the code in this repo is written by agents, and the same agents
could edit the files that define the gates their code has to pass. Both
recent re-baselines were agent-authored: #120 raised the erosion baseline
to 0.435 after it drifted from 0.423 to 0.442 while the gate ran only in
pre-commit, and #142 grew the basedpyright baseline from 292 to 1,239
entries when the checker version was pinned. Each was reasoned, but the
author controlled its own gate.

Since CI started enforcing the erosion gate, erosion went from 0.436 to
0.427 across 20 PRs. Enforcement works; what was missing is a rule that
the gates never loosen without a human.

## Decision

1. The files that define the gates are listed in `.github/CODEOWNERS` with
   @peterhora as owner: the erosion and type baselines, the erosion gate,
   `.importlinter`, `.pre-commit-config.yaml`, `.github/workflows/`, the
   guard tests named in `AGENTS.md`, `docs/architecture-invariants.md`,
   `AGENTS.md`, `CLAUDE.md`, `pyproject.toml` and CODEOWNERS itself.
   Once branch protection requires Code Owner review, none of them changes
   without his approval.
2. Gates only tighten. A baseline is lowered to the measured value in the
   PR that improves it; raising a baseline or a threshold is a human
   decision made in review, never a side effect of a feature PR.
3. Starting points, measured on `main` @ `530487d`: erosion 0.427 against a
   baseline of 0.4354, max CC 29, and 1,239 entries in
   `.basedpyright/baseline.json`. The type baseline has no reduction
   target; it may only shrink.

## Consequences

- Dependency bumps touch `pyproject.toml` and so need Peter's review too.
  Splitting tool config out of `pyproject.toml` would remove that cost
  (open question 3 in the spec).
- Mechanical enforcement lands in follow-up PRs from the same spec: the
  tightening ratchets in CI (section C) and an agent-side hook that blocks
  edits to CODEOWNERS paths (section E).
- Invariant 28 records the rule.
