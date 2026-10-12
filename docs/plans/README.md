# Implementation plans

A plan is working material for the PR that implements it: `gh-design` writes
it on the issue branch as `docs/plans/<YYYY-MM-DD>-<slug>.md`, and
`gh-handoff` hands it to a cloud session. Once the PR merges, the code, the
tests and the spec in `docs/specs/` are the record, so delete the plan in the
same PR or soon after. Stale plans get read by later agents as current truth.

The plans written before 2026-10-12 (this folder and `docs/superpowers/plans/`)
were removed in one cleanup commit. ADRs and comments that cite them still
resolve through git history:

```bash
git log --diff-filter=D --oneline -- docs/plans docs/superpowers/plans   # find the commit
git show <commit>^:docs/plans/2026-05-21-clip-annotate-ui.md             # read a plan
```
