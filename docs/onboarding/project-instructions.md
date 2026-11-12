# SDLC rules for claude.ai Projects and Cowork

Claude Code reads `AGENTS.md` and runs the repo's hooks. A claude.ai
Project or a Cowork session doesn't: it only knows what is in its
instructions. Paste the block below into the Project's (or Cowork
workspace's) custom instructions so work done there follows the same
rules. CI enforces the parts marked *(CI)* whatever tool wrote the change;
the rest depends on the instructions being followed.

Keep this block in step with `AGENTS.md` and
`docs/architecture-invariants.md`; if they disagree, the invariants page
wins.

---

```text
SDLC rules for the ai-archive repo (futurememorylab/ai-archive)

Process
- Never push to main. Work on a short-lived branch and open a PR. (CI)
- Fill in the PR template, every section. The Decisions section is
  required: either link the ADR you added or changed, or write one line
  "No-ADR: <why this is not a decision>". (CI)
- Every PR needs green CI (lint, tests, gates) and every Claude review
  comment fixed or answered before merge.
- Test first: a failing test, then the change.

Decisions (ADRs)
- Read docs/architecture-invariants.md before changing architecture. It is
  the canon. A change that breaks an invariant needs an ADR that
  supersedes it, and the invariant line updated in the same PR.
- Write an ADR (docs/adr/NNNN-slug.md, next free number) when a change
  sets or changes an invariant, is expensive to reverse (schema,
  migration, public API, cloud topology), or binds future PRs. The bar is
  in docs/decisions.md.
- Every ADR has "Date:", "Status:" and "Lifespan:" (Invariant, Feature,
  Lesson or Superseded) headers and a row in the docs/decisions.md index.
  (CI)
- When a new ADR supersedes an old one, mark the old one
  "Lifespan: Superseded" (or add "Partly superseded by NNNN" to its
  header) and point it forward to the new one. (CI)
- These paths always need an ADR or a No-ADR reason (CI,
  tools/decision_triggers.toml): new migrations, deploy workflow and
  deploy/ scripts, pyproject dependencies, .importlinter, Gemini model
  entries in enums/registry.py, the archive writeback payload builders,
  CatDV login/session code, auth and access control, removed routes.

Gate files (human-owned, ADR 0129)
- Don't edit anything listed in .github/CODEOWNERS (CI config, baselines,
  gate tools, guard tests, AGENTS.md, CLAUDE.md, pyproject.toml,
  docs/architecture-invariants.md) without Peter's explicit OK. Propose
  the change in the PR description instead.
- Baselines only go down. Never raise a baseline or threshold to make CI
  pass.

CatDV seat
- The app gets one CatDV seat. Any script that logs in must log out
  (DELETE /catdv/api/9/session). On "session limit reached", don't retry.
```
