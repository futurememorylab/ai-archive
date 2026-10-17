---
name: gh-handoff
description: Hand a designed GitHub issue off to a cloud Claude Code session for the Implementation phase. Use when the user says "hand off #N", "implement issue N remotely", or "ship N to the cloud". Verifies the issue has a committed plan on its branch, fires the reusable claude.ai routine, watches the run via the real session-log API, and moves the issue to Implementation / Test.
---

# gh-handoff — Implementation hand-off

Fires the cloud `implement-issue` routine to implement an already-designed
issue. The plan must already be committed on `<N>-<slug>` by `gh-design`.

**Board constants** and the `set_status` helper are identical to `gh-design`
(owner `futurememorylab`, repo `futurememorylab/ai-archive`, project
`AI-Archive Flow`). Resolve `PROJ` / `PROJECT_ID` at runtime; reuse the
`set_status N COL` function from `gh-design`'s docs.

## Checked 2026-08-19: `isolation: "remote"` does not replace this

The `Agent` tool's `isolation: "remote"` was evaluated as a possible
replacement for the routine-based flow below. On this account it does
**not** provide a real cloud VM — it silently falls back to a local git
worktree, sharing the host filesystem, the operator's own `gh` keychain
credentials, and a live IPC socket to the parent Claude Code process. That
is not equivalent to a genuine isolated cloud dispatch, so the routine flow
below remains the only real option. If re-checking this in the future
(availability may be account-gated and could change), verify with a
diagnostic dispatch before switching — don't assume the doc string alone.

## Preconditions — fail loudly if unmet

1. **Branch exists with a committed plan.**
   ```bash
   BR="${N}-${SLUG}"
   git fetch origin "$BR" || { echo "No remote branch $BR — run gh-design first"; exit 1; }
   git ls-tree -r --name-only "origin/$BR" -- docs/plans \
     | grep -q . || { echo "No plan committed on $BR — run gh-design first"; exit 1; }
   ```
   If either check fails, stop and tell the user exactly which is missing
   (no branch / no plan). Do not change the board and do not fire a run.

## Flow

1. **Reuse the existing `implement-issue` routine.** `RemoteTrigger` action
   `list` → find the entry with `name == "implement-issue"` and capture its
   `trigger_id`. Don't hardcode it — resolve by name every time, since it
   can be recreated. As of 2026-10-17 (re-checked with `get_trigger`) its live config is:
   - `environment_id: env_011CUK5wJZB5zuqK2n9asWVV`
   - `session_context.model: claude-opus-4-8[1m]`
   - `session_context.allowed_tools: ["Bash","Read","Write","Edit","Glob","Grep","WebFetch","WebSearch","Task"]`
   - `cron_expression: ""` — never autoruns, fires only via `run`.
   - GitHub access comes through the built-in `mcp__github__*` tools
     (confirmed in a real run's log — PR/issue writes used those, not the
     `gh` CLI), not an explicit MCP connector.

2. **Recreate only if the routine is missing.** The `create` body shape is
   now documented (see the `schedule` skill) — this replaces the old
   "ask the operator to build it in the claude.ai web UI" step entirely:
   ```json
   {
     "name": "implement-issue",
     "cron_expression": "",
     "enabled": true,
     "job_config": {
       "ccr": {
         "environment_id": "env_011CUK5wJZB5zuqK2n9asWVV",
         "session_context": {
           "model": "claude-opus-4-8[1m]",
           "sources": [{"git_repository": {"url": "https://github.com/futurememorylab/ai-archive"}}],
           "allowed_tools": ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "WebFetch", "WebSearch", "Task"]
         },
         "events": [{"data": {
           "uuid": "<fresh lowercase v4 uuid>",
           "session_id": "",
           "type": "user",
           "parent_tool_use_id": null,
           "message": {"role": "user", "content": "<prompt below, verbatim>"}
         }}]
       }
     }
   }
   ```
   Prompt (`events[0].data.message.content`). Keep the live routine in sync
   with this text; as of 2026-10-17 the live copy matches it:
   > You are implementing a pre-written plan. Read the run context `text`
   > field (e.g. `issue=#<N> branch=<branch>`) for the issue number and
   > intended branch.
   >
   > 1) `git fetch origin <branch> && git checkout <branch>`. Then VERIFY:
   > `git rev-parse --abbrev-ref HEAD` must equal `<branch>`. If the
   > environment forces a different working branch, note its real name —
   > you'll report it in step 6.
   > 2) Read the plan under `docs/plans/` on that branch. Count
   > the tasks.
   > 3) After Task 1 lands (committed + tests green), PUSH and immediately
   > open a DRAFT pull request to main. Body MUST contain `Closes #<N>` and
   > a checklist with one box per plan task (`- [x] Task 1: <title>` /
   > `- [ ] Task 2: <title>` ...).
   > 4) Implement remaining tasks in order, test first, delegating
   > independent tasks to sub-agents (Task tool) where that helps.
   > After EACH task: commit (tests green first), push (explicit
   > refspec: `git push origin HEAD:<branch>`), then tick that task's box in
   > the PR body. Push after every task, not at the end.
   > 5) When all tasks pass, run the plan's full-suite gate, paste the
   > result into the PR body, and mark the PR "Ready for review".
   > 6) Comment the PR URL on issue #<N>, stating the actual head branch
   > and "N/N tasks green — ready for Test".
   >
   > Do NOT move the project board (your identity lacks Projects write
   > scope; the step silently no-ops). Do NOT merge.

   Reuse the single routine across all issues — never create one per issue.
   Routines cannot be deleted programmatically, only via
   https://claude.ai/code/routines, so a per-issue routine is permanent
   clutter, not a cheap throwaway.

3. **Fire it for this issue.** `RemoteTrigger` action `run` with
   `trigger_id` and per-run context:
   ```json
   { "text": "issue=#<N> branch=<N>-<slug>" }
   ```
   Immediately after, call `RemoteTrigger list_runs` with the same
   `trigger_id` — the newest entry is this run. Capture its `session_id`
   and `url` (a real `https://claude.ai/code/session_...` link) and give
   that URL to the user directly.

4. **Move to Implementation.** `set_status N Implementation`, then swap
   labels:
   ```bash
   gh issue edit N -R futurememorylab/ai-archive \
     --remove-label "phase:design" --add-label "phase:implementation"
   ```

5. **Monitor with two real signals, not guesswork.**

   - **Session health** — `RemoteTrigger` action `get_run_log` with the
     `session_id` from step 3 (no cursor = latest page). Read the tail for
     a `result:` line (`success` or error) to know the session actually
     finished, and for `is_error` / permission-denial / repeated-error
     lines to catch a stuck or failed run early. Don't sit on a blind
     "wait 1-2 task intervals" timer before suspecting trouble — read the
     log.
   - **Task/PR progress** — discover the PR by issue linkage, **not** by
     head branch (the cloud env frequently pushes to its own
     `claude/<name>` branch regardless of the named branch):
     ```bash
     gh pr list -R futurememorylab/ai-archive --state all --search "#${N} in:body" \
       --json number,url,state,isDraft,headRefName,body
     gh pr view <PR#> -R futurememorylab/ai-archive --json body,isDraft \
       | jq -r '.body' | grep -c '^\s*- \[x\]'   # ticked boxes = tasks done
     gh pr checks <PR#> -R futurememorylab/ai-archive
     ```
     Report `N/<total> done — latest ticked task`. Done signal is
     `isDraft == false`.

   Use `CronCreate` (recurring, session-only, off-minute interval like
   `*/7 * * * *`) to re-check both signals. When the PR is **ready**, report
   its number + URL + `headRefName` + the session URL from step 3, **move
   the issue to `Test` yourself** with `set_status N Test` (the cloud
   identity lacks Projects write scope, so this must happen locally), and
   `CronDelete` the watch. Tell the user the watch is session-local (gone
   if Claude exits) — for a portable notification they can instead
   subscribe to the issue / Watch the repo on github.com.

   **If no draft PR appears** within a task interval or two, don't guess at
   a cause — pull `get_run_log` and read what actually happened (auth
   failure, a permission prompt it's stuck on, a crash) instead of assuming
   a push-setting problem.

6. **Report and stop.** Tell the user the watch is running and the rest is
   hands-off: when the PR is marked **ready** the watch moves the issue to
   **Test** (step 5); they test manually and squash-merge, and
   `Closes #N` closes the issue so the built-in *Item closed* workflow
   moves it to **Done**.

## Guardrails

- Never fire a run when the preconditions fail — a cloud run against a
  missing plan wastes a session and confuses the board.
- The routine's `allowed_tools` must include **Bash** (for the `gh` / PR /
  issue steps) and **Task** (so it can
  delegate tasks to sub-agents) — without Task it implements every task
  itself.
- Reuse the single `implement-issue` routine across all issues; do not
  create a routine per issue — routines aren't programmatically deletable.
- The routine's identity lacks GitHub Projects write scope — board moves
  are always done locally by the watch (step 5), never by the cloud
  session; this is intentional, not a bug to fix in the prompt.
- Branch-name deviation (`claude/<name>` instead of `<N>-<slug>`) is
  observed in practice and is not a failure — the PR's `Closes #<N>` is
  the source of truth, not the branch name.
