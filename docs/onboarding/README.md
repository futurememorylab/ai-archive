# Onboarding — CatDV Annotator

Welcome. This folder is the entry point for a new developer joining the
project. It distils the design specs, ADRs, and `CLAUDE.md` into a
linear reading order that gets you to your first useful PR without
having to scrape 60+ docs files.

If you have **two hours**, read everything here in order. If you have
**twenty minutes**, read `01-overview` and `05-catdv-license-discipline`
(the second one will save you a server outage).

## Reading order

| # | Doc | What you learn |
|---|---|---|
| 1 | [`01-overview.md`](./01-overview.md) | What the app does, who it's for, what's in/out of scope |
| 2 | [`02-architecture.md`](./02-architecture.md) | The layer map, request/write/cache flows, with diagrams |
| 3 | [`03-tech-stack.md`](./03-tech-stack.md) | Which technology owns which job and why |
| 4 | [`04-running-locally.md`](./04-running-locally.md) | Bring the server up; the four runtime modes |
| 5 | [`05-catdv-license-discipline.md`](./05-catdv-license-discipline.md) | The 2-seat CatDV limit — **read before starting any process** |
| 6 | [`06-coding-standards.md`](./06-coding-standards.md) | Ruff, basedpyright, import-linter, ADR practice |
| 7 | [`07-codebase-tour.md`](./07-codebase-tour.md) | Where things live; symptom→file table; further reading |

## After onboarding — where to go next

The reference material these docs are condensed from:

- [`../CONTEXT.md`](../CONTEXT.md) — one-sentence glossary of every
  domain noun (Clip, Workspace, Write Queue, Live Session, …).
- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — the canonical layer map
  with the symptom→file triage table.
- [`../decisions.md`](../decisions.md) — index of all ADRs (currently
  22 of them, MADR-lite format under [`../adr/`](../adr/)).
- [`../specs/`](../specs/) — feature design specs.
- [`../plans/`](../plans/) — per-PR implementation plans.
- [`../DEPLOY.md`](../DEPLOY.md) — production deployment.
- [`../../CLAUDE.md`](../../CLAUDE.md) — repo-scoped agent guidance;
  also the canonical place for the CatDV seat discipline rules.

## Diagrams

The architecture pages embed [Mermaid](https://mermaid.js.org/)
diagrams, which render natively on GitHub, GitLab, VS Code (with the
Markdown Preview Mermaid extension), and most modern markdown viewers.
If your viewer renders the source as a code block, paste it into
<https://mermaid.live> to see the picture.
