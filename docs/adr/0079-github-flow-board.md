# 0079. GitHub Flow: kanban board + gh-design / gh-handoff skills

**Date:** 2026-06-14
**Status:** Accepted
**Lifespan:** Invariant

## Context

Work on this repo had no shared, visible pipeline — ideas, design, and
implementation all happened ad hoc in local sessions. We introduced a GitHub
Projects v2 board ("AI-Archive Flow", org `futurememorylab`, project #1) with a
single-select `Status` field whose columns are the lifecycle: **Backlog →
Design → Implementation → Test → Done**. Two thin local skills drive the two
human decision points; everything else is automated or hands-off.

- `gh-design` (`.claude/skills/gh-design/SKILL.md`) — takes a Backlog issue,
  moves it to Design, creates an `issue-<N>-<slug>` branch, runs the superpowers
  brainstorming → writing-plans flow, and commits the spec + plan on the branch.
- `gh-handoff` (`.claude/skills/gh-handoff/SKILL.md`) — verifies the branch has a
  committed plan, fires a reusable claude.ai **remote trigger** (`implement-issue`)
  that runs a cloud Claude Code session to implement the plan and open a PR, then
  moves the issue to Implementation.

The full design and rationale live in
`docs/superpowers/specs/2026-06-14-github-flow-design.md`; the implementation
plan in `docs/superpowers/plans/2026-06-14-github-flow.md`.

## Alternatives

- **Skill granularity:** two orchestrator skills (chosen) vs. a third
  `gh-capture` issue-creation skill (rejected as YAGNI — `gh issue create` plus
  the auto-add workflow suffices) vs. one mega-skill with subcommands (rejected —
  couples two phases).
- **Cloud execution:** claude.ai remote triggers (chosen — documented best
  practice for "fire a committed plan, open a PR") vs. GitHub Actions + `@claude`
  vs. Agent SDK headless.
- **Trigger lifecycle:** one reusable parametrized trigger fired per issue
  (chosen) vs. an ephemeral trigger created per issue (rejected — API churn,
  against the documented create-once/fire-many model).
- **Plan storage:** committed on the feature branch (chosen — the cloud session
  reads it from the tree) vs. posted on the issue.
- **Branch-push policy:** unrestricted branch pushes on the cloud environment
  (chosen — one branch and one clean PR per issue) vs. the default `claude/`
  prefix (rejected — two branch names per issue).

## Decision

Adopt the board + two skills as above. Three implementation realities forced
deviations from the first-draft plan, recorded here:

1. **The `Status` field is edited, not recreated.** `Status` is a reserved,
   built-in Projects field — `gh project field-delete` refuses it and the name
   cannot be reused. The five columns were set via the GraphQL
   `updateProjectV2Field` mutation (`singleSelectOptions`), not the plan's
   delete-and-recreate approach.

2. **The Test transition is driven by the cloud session, not a built-in
   workflow.** GitHub Projects built-in workflows act only on items *in* the
   project, and we deliberately do not add PRs to the board — so there is no
   native "linked PR opened → move the issue to Test". Instead `gh-handoff`'s
   trigger prompt instructs the cloud session to move the issue to Test itself
   (step 7) after opening the PR. The **Done** transition *does* use a genuine
   built-in: `Closes #N` closes the issue on merge, and the *Item closed* →
   Done workflow fires.

3. **Built-in workflows are configured in the UI.** `gh` and the public API do
   not expose Projects built-in workflows (auto-add, item-added → Backlog, item
   closed → Done), so those are a one-time operator step in the board's web UI.

Other one-time operator prerequisites: `gh auth refresh -s project,read:project`
(done), and connecting the repo plus enabling **unrestricted branch pushes** at
`claude.ai/code`.

## Consequences

- The board reflects reality with humans acting at exactly two points (start
  design, hand off); the plan is versioned on the branch and reviewable in the
  PR alongside the implementation.
- The cloud session runs as the operator's GitHub identity (no bot account), so
  the operator's claude.ai connection is a hard dependency for hand-off, and
  unrestricted branch pushes trade the `claude/`-prefix guardrail for a
  single-branch flow.
- The remote-trigger API is a 2026 research preview; `gh-handoff` discovers the
  trigger body schema at runtime before creating, so a schema change is isolated
  to that skill's steps 1–3.
- Because the Test move is done by the cloud session rather than a built-in, a
  failed/aborted cloud run leaves the issue in Implementation — correct, since
  no PR was opened.
