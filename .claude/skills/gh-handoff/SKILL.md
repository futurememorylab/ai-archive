---
name: gh-handoff
description: Hand a designed GitHub issue off to a cloud Claude Code session for the Implementation phase. Use when the user says "hand off #N", "implement issue N remotely", or "ship N to the cloud". Verifies the issue has a committed plan on its branch, fires the reusable claude.ai remote trigger, and moves the issue to Implementation. The cloud session implements the plan, pushes to the branch, and opens a PR.
---

# gh-handoff — Implementation hand-off

Fires a cloud Claude Code session to implement an already-designed issue. The
plan must already be committed on `issue-<N>-<slug>` by `gh-design`.

**Board constants** and the `set_status` helper are identical to `gh-design`
(owner `futurememorylab`, repo `futurememorylab/ai-archive`, project
`AI-Archive Flow`). Resolve `PROJ` / `PROJECT_ID` at runtime; reuse the
`set_status N COL` function from `gh-design`'s docs.

## Preconditions — fail loudly if unmet

1. **Branch exists with a committed plan.**
   ```bash
   BR="issue-${N}-${SLUG}"
   git fetch origin "$BR" || { echo "No remote branch $BR — run gh-design first"; exit 1; }
   git ls-tree -r --name-only "origin/$BR" -- docs/superpowers/plans \
     | grep -q . || { echo "No plan committed on $BR — run gh-design first"; exit 1; }
   ```
   If either check fails, stop and tell the user exactly which is missing
   (no branch / no plan). Do not change the board and do not fire a trigger.

## Flow

1. **Discover the trigger API shape.** The remote-trigger API is a research
   preview — confirm the body schema before creating anything:
   - `RemoteTrigger` action `list` → see existing triggers and their fields.
   - If an `implement-issue` trigger already exists, capture its `trigger_id`
     and skip to step 3.
   - Inspect one trigger with action `get` to confirm the exact field names
     (`prompt`, `repositories`, `trigger`/`triggers`, etc.) before composing a
     `create` body. Adapt the body in step 2 to the schema you observe — do not
     blindly trust the shape below if `get` shows different keys.

2. **Create the reusable trigger (only if missing).** `RemoteTrigger` action
   `create` with a body shaped like:
   ```json
   {
     "name": "implement-issue",
     "repositories": ["futurememorylab/ai-archive"],
     "trigger": { "type": "api" },
     "prompt": "You are implementing a pre-written plan. First read the run context (the `text` field, e.g. `issue=#<N> branch=<branch>`) to get the issue number and branch. Steps: 1) `git fetch origin <branch> && git checkout <branch>`. 2) Read the implementation plan under docs/superpowers/plans/ on that branch. 3) Use the superpowers:subagent-driven-development skill to implement the plan task by task. 4) Commit and push to the SAME branch <branch>. 5) Open a pull request to main whose body contains `Closes #<N>`. 6) Comment the PR URL back on issue #<N>. 7) Move issue #<N> to the `Test` column of the `AI-Archive Flow` project board, using the `set_status` helper documented in .claude/skills/gh-design/SKILL.md. Do not merge."
   }
   ```
   Capture the returned `trigger_id`.

3. **Fire it for this issue.** `RemoteTrigger` action `run` with `trigger_id`
   and a body carrying per-run context:
   ```json
   { "text": "issue=#<N> branch=issue-<N>-<slug>" }
   ```
   Relay the returned claude.ai session URL (and run time, if present) to the
   user.

4. **Move to Implementation.** `set_status N Implementation`, then swap labels:
   ```bash
   gh issue edit N -R futurememorylab/ai-archive \
     --remove-label "phase:design" --add-label "phase:implementation"
   ```

5. **Report and stop.** Tell the user the session URL and that the rest is
   hands-off: when the cloud session opens the PR it moves the issue to **Test**
   (step 7 of the trigger prompt); they test manually and squash-merge, and
   `Closes #N` closes the issue so the built-in *Item closed* workflow moves it
   to **Done**.

## Guardrails

- Never fire a trigger when the preconditions fail — a cloud run against a
  missing plan wastes a session and confuses the board.
- The cloud session runs as the connected GitHub identity and needs
  **unrestricted branch pushes** enabled on the claude.ai environment (one-time
  operator setup) to push to `issue-<N>-<slug>`. If the run reports it could
  only push a `claude/`-prefixed branch, that setting is off — tell the operator.
- Reuse the single `implement-issue` trigger across all issues; do not create a
  trigger per issue.
