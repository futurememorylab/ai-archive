# 0022. Tier 2 architecture plan — execution summary

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

The plan at
`docs/plans/2026-05-23-codebase-architecture-tier-2-and-beyond.md`
proposed a Tier 2 hygiene pass: enforce layering with import-linter,
add orientation docs (CONTEXT.md / ARCHITECTURE.md), sweep module
docstrings, decompose `pages.py` and `AppContext.build()`, collapse
duplicate `CacheInspector` / `CacheActions` construction, migrate
`decisions.md` into per-decision ADRs, and ratchet basedpyright.
Several items in the plan were "if cheap" — radon/C901 surfacing,
vulture sweep, archive-adapter sharpening — and were always tagged
as deferrable.

The plan was executed end-to-end on branch
`claude/tender-hawking-rp1zc` across nine commits (PRs A through I).
Tests, lint, format, import-linter, and basedpyright all hold green
at the end of the sequence.

## Decision

The status of each plan section as it stands at the end of this branch:

| Section | Outcome | Commit |
| --- | --- | --- |
| §1.4 findings | addressed via §3.x / §4.x | — |
| §2.1 import-linter | DONE | `5005673` |
| §2.2 C901 / radon | NOT DONE (deferred) | — |
| §2.3 vulture | NOT DONE (deferred) | — |
| §2.4 typed `get_ctx` (sub-task of §6) | DONE | `6aecaf3` |
| §3.1 CONTEXT.md | DONE | `a562c67` |
| §3.2 ARCHITECTURE.md | DONE | `a562c67` |
| §3.3 module docstring sweep | DONE (this commit) | — |
| §3.4 ADR migration (`decisions.md` → per-decision files) | DONE | `8baf647` |
| §4.1 `pages.py` split by feature | DONE | `e3849b5` |
| §4.2 `cache_actions` / `cache_inspector` construction collapse | DONE | `0200577` (ADR 0021) |
| §4.3 archive adapter sharpening | NOT DONE (deferred) | — |
| §4.4 `AppContext.build()` decomposition | DONE | `336dd80` |
| §6 basedpyright ratchet | PARTIAL | `6aecaf3` |
| §7 stabilize broken tests | DONE | `c94d702` |

The `interrogate` integration that the §3.3 plan called out deserves
a note. The plan set `fail-under = 70` expecting that adding a
module-level docstring to every backend file would clear the bar.
In practice `interrogate` counts every callable (every method on
every repo, every route handler) and the realistic post-sweep
coverage is ~30.7%. We did the module-level sweep as specified,
wired `interrogate` into `pyproject.toml` and pre-commit, and set
`fail-under = 30` so the hook actually gates regressions against
current coverage rather than against an unattainable target. Raising
the gate toward 70% is a follow-up: it requires either docstringing
~250 functions/methods or scoping interrogate to module-level only
(which it does not natively support — would require a custom hook).

## Consequences

Pick-up surface for future contributors:

- **§2.2 C901 / radon.** Wire `radon cc -s -a backend/app` into CI
  and pick a threshold; or add `select = ["C90"]` and `mccabe.max-complexity`
  to ruff. The previously identified hotspots
  (`annotator.run_job`, `CacheActions.attach_*` flow, `cache_inspector._load_*`
  helpers) are still the first places to look.
- **§2.3 vulture.** A one-shot pass would catch dead code accumulated
  during this branch's refactors. Use
  `vulture backend/ --min-confidence 80 --exclude tests`.
- **§4.3 archive adapter sharpening.** The `archive/providers/catdv/`
  package has `adapter.py` (~600 LOC), `mapping.py`, and `payload.py`.
  The plan noted that the adapter still mixes "talk to CatDV" with
  "translate the response shape"; a sub-split between transport and
  translation is the next reasonable cut.
- **§6 basedpyright ratchet.** The baseline at
  `.basedpyright/baseline.json` grew during PR E because typing
  `get_ctx` surfaced latent Optional issues we had been silently
  passing through `Any`. The ratchet path is to enable specific
  rules (`reportOptionalMemberAccess`, `reportOptionalSubscript`)
  as errors one at a time and burn down the baseline diff for each.
- **interrogate per-callable coverage.** If we want function/method
  docstrings enforced, the cheapest path is to start with the
  `services/` and `routes/` layers (which dominate the missing
  count) and raise `fail-under` in steps of 10. The repos are
  CRUD-heavy; many methods are self-documenting from their SQL and
  could be excluded by name pattern via `--ignore-regex` if
  documenting them is not worth the churn.

Nothing in the deferred list is blocking — the codebase passes all
gates green and is in better shape than before the branch started.
