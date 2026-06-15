# GitHub Flow — Issue + Project Kanban workflow

**Date:** 2026-06-14
**Status:** Approved (pre-implementation)

## Context

Work on `ai-archive` currently has no shared, visible pipeline. Ideas, design
work, implementation, and testing all happen ad hoc in local Claude Code
sessions. We want a single GitHub Project board that makes the lifecycle of
every unit of work explicit, and two thin Claude Code skills that drive the
board at its two human decision points while delegating the actual thinking to
the existing superpowers skills.

The pipeline has four active lanes plus a terminal lane:

- **Backlog** — anything, human- or agent-created.
- **Design** — refinement done **locally**: `superpowers:brainstorming` →
  spec → `superpowers:writing-plans`. A raw idea becomes a detailed,
  committed implementation plan.
- **Implementation** — handed off to a **cloud** Claude Code session that runs
  the subagent superpowers development flow against the committed plan.
- **Test** — the cloud session has opened a PR; the human tests manually and,
  on success, squash-merges to `main`.
- **Done** — terminal lane for merged/closed work.

This spec defines the GitHub setup and the two skills (`gh-design`,
`gh-handoff`). It does **not** cover the internals of the superpowers skills it
calls — those are reused as-is.

### Key research finding (2026)

Remote Claude Code execution has three documented mechanisms: **Routines /
remote triggers** (claude.ai cloud), **GitHub Actions** (`claude-code-action`),
and the **Agent SDK** (headless). For "implement an already-committed plan in
the cloud and open a PR," Anthropic's documented best practice is **Routines /
remote triggers with the API/`run` trigger**: create the trigger **once**, then
**fire it many times** with per-run context. The cloud session runs as the
connected user's GitHub identity (no bot account), clones the repo at its
default branch, and can open PRs.

Two consequences for this design:

1. The repo `ai-archive` must be connected once at `claude.ai/code`, and the
   cloud environment must have **"Allow unrestricted branch pushes"** enabled so
   the session can push back to the `issue-<n>-<slug>` branch (the default
   policy only allows `claude/`-prefixed branches).
2. In this Claude Code environment the `RemoteTrigger` tool is the in-process
   client for that API (`/v1/code/triggers`, actions `create` / `run`); it
   injects the OAuth token automatically, so skills call it directly rather than
   shelling out to `curl`.

Docs: `code.claude.com/docs/en/routines`,
`platform.claude.com/docs/en/api/claude-code/routines-fire`,
`code.claude.com/docs/en/claude-code-on-the-web`.

## Alternatives considered

**Skill granularity.** Three options were weighed:
- **A — Two orchestrator skills** (`gh-design`, `gh-handoff`). Chosen: matches
  the two human decision points (start designing / hand off), stays thin glue
  around existing superpowers skills, easy to remember.
- **B — Three skills** (add `gh-capture` for issue creation). Rejected as YAGNI:
  `gh issue create` plus the auto-add workflow already lands new issues in
  Backlog consistently.
- **C — One mega-skill** with subcommands. Rejected: couples two distinct phases
  and fights the superpowers one-purpose-per-skill convention.

**Cloud execution mechanism.** Routines / remote triggers (chosen, see above)
vs. GitHub Actions + `@claude` vs. Agent SDK headless. Actions is best for
GitHub-event-native automations; the SDK is best for custom infra-owned
pipelines. Neither fits "fire a committed plan from a local session" as
directly as a remote trigger.

**Trigger lifecycle.** Ephemeral create-per-issue vs. **reusable parametrized**
trigger. Chosen reusable because it is the documented best practice and avoids
API churn; per-issue context rides in the `run` body's `text` field.

**Branch-push policy.** Enable **unrestricted pushes** (chosen — one branch and
one clean PR per issue) vs. keep the `claude/` prefix (rejected — two branch
names per issue, PR head diverges from the design branch).

**Transitions.** Skill-driven at phase boundaries **plus** GitHub Projects
built-in workflows for the PR/merge edges (chosen) vs. built-in only (more
manual dragging) vs. fully manual (board drifts from reality).

**Plan storage.** Committed on the feature branch (chosen — the cloud session
reads it straight from the tree, versioned and reviewable) vs. posted on the
issue vs. both.

## Decision

### GitHub Project

- **Project:** org-level under `futurememorylab`, title **"AI-Archive Flow"**,
  linked to the `ai-archive` repo.
- **Status field** (single-select, the board columns):
  `Backlog`, `Design`, `Implementation`, `Test`, `Done`.
- **Built-in workflows enabled:**
  - *Auto-add to project* — new issues in `ai-archive` are added automatically.
  - *Item added → Status = Backlog* — every new item lands in Backlog.
  - *Pull request linked → Status = Test* — when the implementation PR is opened
    and linked to the issue, the item moves to Test.
  - *Item closed → Status = Done* — merging the PR (`Closes #<n>`) closes the
    issue and moves it to Done.
- **PRs are not auto-added** to the board; only the issue travels the lanes, and
  its linked PR drives the Test/Done edges.

### Labels & conventions

- **Phase labels:** `phase:design`, `phase:implementation`, `phase:test`. The
  Status field is the source of truth; skills keep the label in sync as a
  CLI/issue-list convenience.
- **Branch:** `issue-<n>-<slug>` (slug = kebab-cased issue title, ~5 words),
  branched off `main`.
- **One issue → one branch → one PR.** PR body contains `Closes #<n>` so merge
  auto-closes the issue (→ Done).
- **Spec:** `docs/superpowers/specs/YYYY-MM-DD-<slug>-design.md`.
  **Plan:** wherever `superpowers:writing-plans` writes it, committed on the
  same branch.

### Skill: `gh-design` (local)

Trigger phrases: "design #12", "refine issue 12", "start design on <idea>".

1. Read the issue (`gh issue view <n>`). If no number is given, list Backlog
   items and ask which one.
2. Move the issue to **Design** — set Project Status and add `phase:design`.
3. Create branch `issue-<n>-<slug>` off `main`.
4. Invoke `superpowers:brainstorming` → produces the spec.
5. Invoke `superpowers:writing-plans` → produces the plan.
6. Commit spec + plan on the branch, push, and post a comment on the issue
   linking the branch, spec, and plan.
7. Stop and report that the issue is ready to hand off. **Does not** auto-launch
   the cloud session (that is the second, deliberate human decision point).

### Skill: `gh-handoff` (local)

Trigger phrases: "hand off #12", "implement issue 12 remotely".

1. Verify the branch exists and has a committed plan. If not, fail with a clear
   error naming exactly what is missing (no branch / no plan) — do not proceed.
2. Ensure the persistent **`implement-issue`** remote trigger exists. If absent,
   `RemoteTrigger create` it once. Its prompt instructs a cloud Claude Code
   session to:
   - `git fetch` and `git checkout issue-<n>-<slug>`,
   - read the plan from the tree,
   - run `superpowers:subagent-driven-development` against it,
   - push commits to the **same** branch,
   - open a PR with `Closes #<n>`,
   - comment the PR link back on the issue.
3. `RemoteTrigger run` it with a body whose `text` carries the issue number and
   branch name.
4. Move the issue to **Implementation** — set Project Status and swap the label
   to `phase:implementation`.
5. Relay the returned claude.ai session URL to the user. From here it is
   hands-off: the cloud session opens the PR → built-in workflow moves the item
   to **Test** → the human tests and squash-merges → `Closes #<n>` closes it →
   **Done**.

### One-time setup

Some steps need the operator's own credentials/identity and cannot be done by
the agent:

- **Operator:** `gh auth refresh -s project,read:project` *(done 2026-06-14)*.
- **Operator:** at `claude.ai/code`, connect the `ai-archive` repo and enable
  **"Allow unrestricted branch pushes"** on the cloud environment.
- **Agent (after scope refresh):** create the Project and Status field, enable
  the four built-in workflows, and create the three `phase:*` labels.

### Out of scope (YAGNI)

- No `gh-capture` skill — `gh issue create` + auto-add covers it.
- No Test-phase skill — built-in automation plus manual squash-merge covers it.
- No GitHub Actions / Agent-SDK execution path — remote triggers are the chosen
  mechanism.

## Consequences

- The board reflects reality with minimal manual dragging: humans act at exactly
  two points (start design, hand off); everything else is automated.
- The plan is versioned on the branch and reviewable in the eventual PR
  alongside the implementation — one clean diff per issue.
- The cloud session runs as the operator's GitHub identity, so commits/PRs are
  attributable to them; there is no bot account to manage, but it also means the
  operator's claude.ai connection is a hard dependency for hand-off.
- Unrestricted branch pushes are enabled for the cloud environment — a
  deliberate trade of the `claude/`-prefix guardrail for a single-branch flow.
- The remote-trigger API is a 2026 research preview; its surface may change
  behind dated beta headers. `gh-handoff` depends on the `RemoteTrigger` tool
  remaining the in-process client. If the API shifts, only `gh-handoff` step 2–3
  need updating.

## Manual acceptance flows

1. **New issue lands in Backlog.**
   Setup: the Project and workflows exist. Action: `gh issue create -R
   futurememorylab/ai-archive -t "Test idea" -b "..."`. Expected: within a few
   seconds the issue appears on the **AI-Archive Flow** board in the **Backlog**
   column with no manual step.

2. **`gh-design` turns a Backlog issue into a committed plan.**
   Setup: a Backlog issue #N. Action: invoke `gh-design` for #N. Expected: the
   issue moves to **Design** and gains `phase:design`; a branch
   `issue-<N>-<slug>` exists locally and on the remote; the branch contains a
   spec under `docs/superpowers/specs/` and a plan file; a comment on the issue
   links the branch/spec/plan. The skill reports "ready to hand off" and does
   **not** start a cloud session.

3. **`gh-handoff` launches the cloud implementation.**
   Setup: issue #N in Design with a committed plan on `issue-<N>-<slug>`.
   Action: invoke `gh-handoff` for #N. Expected: the `implement-issue` trigger
   exists (created if it was missing); a run is fired with the issue/branch in
   its body; the issue moves to **Implementation** with `phase:implementation`;
   the user receives a claude.ai session URL.

4. **`gh-handoff` refuses an un-designed issue.**
   Setup: a Backlog issue with no branch/plan. Action: invoke `gh-handoff`.
   Expected: a clear error stating the branch or plan is missing; the board is
   **not** changed and no trigger is fired.

5. **PR opening and merge drive the board to Test then Done.**
   Setup: a cloud session has implemented #N and opened a PR with `Closes #N`.
   Expected: the issue moves to **Test** when the PR is linked. After the human
   squash-merges the PR, the issue closes and the item moves to **Done**.
