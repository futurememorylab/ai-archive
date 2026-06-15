---
name: gh-handoff
description: Hand a designed GitHub issue off to a cloud Claude Code session for the Implementation phase. Use when the user says "hand off #N", "implement issue N remotely", or "ship N to the cloud". Verifies the issue has a committed plan on its branch, fires the reusable claude.ai remote trigger, and moves the issue to Implementation. The cloud session implements the plan, pushes to the branch, and opens a PR.
---

# gh-handoff — Implementation hand-off

Fires a cloud Claude Code session to implement an already-designed issue. The
plan must already be committed on `<N>-<slug>` by `gh-design`.

**Board constants** and the `set_status` helper are identical to `gh-design`
(owner `futurememorylab`, repo `futurememorylab/ai-archive`, project
`AI-Archive Flow`). Resolve `PROJ` / `PROJECT_ID` at runtime; reuse the
`set_status N COL` function from `gh-design`'s docs.

## Preconditions — fail loudly if unmet

1. **Branch exists with a committed plan.**
   ```bash
   BR="${N}-${SLUG}"
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

2. **Create the reusable trigger (only if missing).** Heads-up: as of this
   writing the programmatic `create` body documented below is **stale** — the
   live API rejects `prompt` / `repositories` / `trigger` ("Extra inputs are not
   permitted") and instead wants a nested `session_request.worker` (or
   `job_config.ccr`) shape that isn't publicly documented. The official docs say
   routine creation with an API trigger is a **web-UI operation**
   (claude.ai/code/routines → New routine → API trigger). So if no
   `implement-issue` trigger exists, ask the operator to create it once in the web
   UI with the prompt below (repo `futurememorylab/ai-archive`, **unrestricted
   branch pushes** on, allowed-tools incl. Bash), then `list` to capture its
   `trigger_id`. Only attempt the programmatic `create` (and adapt to whatever
   schema `get` on an existing trigger reveals) if the web UI is unavailable.
   Reference body:
   ```json
   {
     "name": "implement-issue",
     "repositories": ["futurememorylab/ai-archive"],
     "trigger": { "type": "api" },
     "prompt": "You are implementing a pre-written plan. First read the run context (the `text` field, e.g. `issue=#<N> branch=<branch>`) to get the issue number and branch. Steps: 1) `git fetch origin <branch> && git checkout <branch>`. 2) Read the implementation plan under docs/superpowers/plans/ on that branch. 3) Use the superpowers:subagent-driven-development skill to implement the plan task by task, in order. 4) After EACH task: make the task's commit, then immediately `git push origin <branch>`. Push after every task — not once at the end — so progress is visible remotely and committed work survives if the session restarts. Each task's tests must be green before you commit it. 5) When all tasks are done, open a pull request to main whose body contains `Closes #<N>`. 6) Comment the PR URL back on issue #<N>. 7) Move issue #<N> to the `Test` column of the `AI-Archive Flow` project board, using the `set_status` helper documented in .claude/skills/gh-design/SKILL.md. Do not merge."
   }
   ```
   Capture the returned `trigger_id`.

3. **Fire it for this issue.** `RemoteTrigger` action `run` with `trigger_id`
   and a body carrying per-run context:
   ```json
   { "text": "issue=#<N> branch=<N>-<slug>" }
   ```
   Relay the claude.ai session URL if the response carries one. Note: the
   internal `run` response often just echoes the trigger object **without** a
   `claude_code_session_url` — and fired routine sessions do **not** appear in the
   main session list at claude.ai/code. Point the user to the routine's
   **"Routine runs"** history (open `implement-issue` at claude.ai/code/routines)
   to watch the live session.

4. **Move to Implementation.** `set_status N Implementation`, then swap labels:
   ```bash
   gh issue edit N -R futurememorylab/ai-archive \
     --remove-label "phase:design" --add-label "phase:implementation"
   ```

5. **Monitor for the PR.** There is no routine run-status API (the per-routine
   token has no read access; `fire` is the only endpoint), so watch the
   **outcome on GitHub** instead. Set up a session watch that polls until the PR
   appears, then stops:
   ```bash
   gh pr list -R futurememorylab/ai-archive --head "${N}-${SLUG}" \
     --state all --json number,url,state,title
   ```
   Use `CronCreate` (recurring, session-only, an off-minute interval like
   `*/7 * * * *`) to re-run that check; when a PR shows up, report its number +
   URL to the user, **move the issue to `Test` yourself** with `set_status N Test`,
   and `CronDelete` the watch. Do the board move locally because the cloud routine
   **cannot** — its identity lacks GitHub Projects write scope, so its own step-7
   move silently no-ops and the issue is left stranded in Implementation. The local
   operator's `gh` has Projects scope, so the watch is the reliable place to do it.
   The trigger prompt pushes after **each task**, so surface incremental progress:
   list the implementation commits on the branch beyond the design commits, map
   each to its plan task, and report `N/<total> — latest: <task>`. Tell the user
   this watch is session-local (in memory, not in GitHub, gone if Claude exits) —
   for a portable notification they can instead subscribe to the issue / Watch the
   repo on github.com.

6. **Report and stop.** Tell the user the watch is running and that the rest is
   hands-off: when the PR appears the watch moves the issue to **Test** (step 5);
   they test manually and squash-merge, and `Closes #N` closes the issue so the
   built-in *Item closed* workflow moves it to **Done**.

## Guardrails

- Never fire a trigger when the preconditions fail — a cloud run against a
  missing plan wastes a session and confuses the board.
- The cloud session runs as the connected GitHub identity and needs
  **unrestricted branch pushes** enabled on the claude.ai environment (one-time
  operator setup) to push to `<N>-<slug>`. If the run reports it could only push
  a `claude/`-prefixed branch, that setting is off — tell the operator.
- The cloud session's allowed-tools must include **Bash** (for the `gh` PR /
  issue / board steps) — and **Task** if you want it to use
  `subagent-driven-development`; without Task it falls back to implementing the
  plan directly. Set this on the routine in the web UI.
- Reuse the single `implement-issue` trigger across all issues; do not create a
  trigger per issue.
