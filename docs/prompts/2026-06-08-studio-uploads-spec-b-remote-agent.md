# Remote-agent prompt — Prompt Studio uploads (Spec B)

Hand this to a remote Claude Code agent on branch `feat/studio-uploads-spec-b`.
It executes the committed plan task-by-task via subagent-driven development.

---

You are implementing a feature in the catdv-annotator repo. The branch
`feat/studio-uploads-spec-b` is already checked out and already contains the
approved spec and a complete, task-by-task implementation plan. Your job is to
EXECUTE that plan to completion.

## Start here
1. Confirm you are on branch `feat/studio-uploads-spec-b` (it already has the
   spec, the plan, and this prompt committed — do not rewrite them).
2. Read the plan in full before doing anything:
   `docs/plans/2026-06-08-prompt-studio-uploads-spec-b.md`
3. Read the spec for context/acceptance:
   `docs/specs/2026-06-08-prompt-studio-uploads-spec-b-design.md`
4. Read `CLAUDE.md` at the repo root and follow its rules (they override
   defaults).

## How to execute
Use the `superpowers:subagent-driven-development` skill to drive the work:
dispatch a fresh subagent per task, review between tasks, two-stage review.
Work through the plan's **Task 1 → Task 16 strictly in order**. Each task is
already broken into bite-sized TDD steps (write the failing test → run it red →
implement → run it green → commit). Honor those steps exactly, including the
per-task commit message. Do not batch tasks or skip the "watch it fail" step.

Notes on task ordering:
- Tasks 11 and 12 are paired (page route marks `uploaded`; the card consumes
  it). Run them in that order and re-run Task 11's test after Task 12.
- Task 16 is the final regression + guardrail pass (full pytest, `lint-imports`,
  N+1 / no-sync-fs-in-async / context-delegation guards).

## Project rules you MUST follow
- **Python:** always use the project venv — `.venv/bin/python` and
  `.venv/bin/python -m pytest …` (never system python). Python 3.12/3.13 only.
- **TDD is mandatory** for every task (the plan is written this way).
- **CatDV license seat discipline:** if you need the dev server, start it with
  the `server-start` skill and stop it with `server-stop` (SIGTERM only — NEVER
  `kill -9`; that leaks the CatDV session seat). Most tasks need only pytest, not
  a running server.
- **No network hangs:** if `pip`/`git fetch`/a probe shows zero activity within a
  few seconds, treat it as a network failure and pivot to reading code — do not
  retry in a loop.
- **Frontend discipline:** reuse existing partials/components; user-facing errors
  go through `Alpine.store('toast')`; never `location.reload()` after CRUD;
  no raw hex — use `app.css` design tokens.
- This repo is **Python-only** (no JS test runner): JS behavior is verified by
  source-scan tests + the manual acceptance flows, exactly as the plan specifies.

## When all 16 tasks are green
1. Run the full suite once more: `.venv/bin/python -m pytest -q` and
   `.venv/bin/lint-imports` — both must pass.
2. Add an ADR recording the identity-model decision (synthetic high-offset
   uploaded clip ids + thin source guards): create
   `docs/adr/NNNN-prompt-studio-uploaded-clip-identity.md` (next free number;
   MADR-lite format — see existing ADRs) and add a row to the index table in
   `docs/decisions.md`. Commit it.
3. Push the branch and open a PR titled
   "Prompt Studio: uploaded clips (Spec B)" whose body summarizes the change,
   links the spec + plan, and lists the 8 manual acceptance flows from the spec
   as a reviewer checklist. Do NOT merge.

## If you get stuck
If a task's test can't be made to pass after a genuine root-cause attempt (not
surface patches), STOP, leave that task's branch state intact, and report: which
task, the failing command + output, your diagnosis, and the options — rather than
forcing a workaround that deviates from the plan. The plan flags two
implementation-time decision points (Task 16's `lint-imports` note;
Task 15's CSS-token-name note) — handle those as the plan describes.
