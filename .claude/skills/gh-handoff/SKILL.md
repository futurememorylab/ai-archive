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
     "prompt": "You are implementing a pre-written plan. Read the run context `text` field (e.g. `issue=#<N> branch=<branch>`) for the issue number and intended branch. Steps: 1) `git fetch origin <branch> && git checkout <branch>`, then VERIFY `git rev-parse --abbrev-ref HEAD` equals <branch>; if the environment forces a different working branch, capture its real name to report in step 6. 2) Read the implementation plan under docs/superpowers/plans/ on that branch and count its tasks. 3) After Task 1 is committed with tests green, push and immediately open a DRAFT pull request to main. The PR body MUST contain `Closes #<N>` and a checklist with one box per plan task (`- [x] Task 1: <title>` / `- [ ] Task 2: <title>` ...). This early, stable PR is how the operator's watch tracks progress — branch-name-agnostic. 4) Implement the remaining tasks with the superpowers:subagent-driven-development skill, in order. After EACH task: commit (tests green first), `git push origin HEAD:<branch>` (explicit refspec so the push targets the named branch), then tick that task's box in the PR body with `gh pr edit`. Push after every task, not once at the end. 5) When all tasks pass, run the plan's full-suite gate, paste the result into the PR body, and mark the PR ready with `gh pr ready`. 6) Comment the PR URL on issue #<N>, stating the ACTUAL head branch and `N/N tasks green — ready for Test`. Do NOT move the project board — your identity lacks GitHub Projects write scope, so the move silently no-ops; the operator's watch does it. Do NOT merge."
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
   **outcome on GitHub** instead.

   **Discover the PR by issue linkage, NOT by the head branch.** Do not assume
   the head is `<N>-<slug>` — the cloud environment frequently pushes to its own
   `claude/<name>` branch regardless of the named branch (see Guardrails), so a
   `--head "${N}-${SLUG}"` query silently misses the PR. Search by the issue the
   PR closes instead — this is branch-name-agnostic:
   ```bash
   gh pr list -R futurememorylab/ai-archive --state all --search "#${N} in:body" \
     --json number,url,state,isDraft,headRefName,body
   ```
   The routine opens this PR as a **draft after Task 1**, so the watch has a
   stable handle for the whole run. Derive the real working branch from the PR's
   `headRefName` once it appears.

   **Progress comes from the PR, not commit-subject guessing.** The PR body
   carries a per-task checklist the routine ticks after each task. Count it and
   check CI:
   ```bash
   gh pr view <PR#> -R futurememorylab/ai-archive --json body,isDraft \
     | jq -r '.body' | grep -c '^\s*- \[x\]'   # ticked boxes = tasks done
   gh pr checks <PR#> -R futurememorylab/ai-archive   # test/CI status
   ```
   Report `N/<total> done — latest ticked task`. The **done signal is
   `isDraft == false`** (routine ran `gh pr ready`), not merely "a PR exists".

   Use `CronCreate` (recurring, session-only, an off-minute interval like
   `*/7 * * * *`) to re-run the check. When the PR is **ready** (not draft),
   report its number + URL + `headRefName`, **move the issue to `Test` yourself**
   with `set_status N Test`, and `CronDelete` the watch. Do the board move locally
   because the cloud routine **cannot** — its identity lacks GitHub Projects write
   scope, so it is told to skip that step; the local operator's `gh` has Projects
   scope, so the watch is the reliable place to do it. Tell the user this watch is
   session-local (in memory, not in GitHub, gone if Claude exits) — for a portable
   notification they can instead subscribe to the issue / Watch the repo on
   github.com.

   **Fallback if no draft PR appears** within ~1–2 task intervals of firing: the
   routine likely couldn't open it (token scope / push setting). Fall back to
   commit-diffing the candidate branches — `git ls-remote --heads origin
   'claude/*' "${N}-*"`, then `git log --oneline origin/main..origin/<branch>`
   mapped to plan tasks — and tell the operator the draft-PR step isn't working.

6. **Report and stop.** Tell the user the watch is running and that the rest is
   hands-off: when the PR is marked **ready** (un-drafted) the watch moves the
   issue to **Test** (step 5); they test manually and squash-merge, and
   `Closes #N` closes the issue so the built-in *Item closed* workflow moves it
   to **Done**.

## Guardrails

- Never fire a trigger when the preconditions fail — a cloud run against a
  missing plan wastes a session and confuses the board.
- The cloud session runs as the connected GitHub identity and needs
  **unrestricted branch pushes** enabled on the claude.ai environment (one-time
  operator setup) to push to `<N>-<slug>`. Even with it on, the environment
  often still works on (and pushes to) an auto-generated `claude/<name>` branch
  rather than the named branch — observed in practice. This is why the watch
  (step 5) discovers the PR by `#<N> in:body`, not by head branch, and reads the
  real branch from the PR's `headRefName`. The work is still correct (it bases
  off the design commits and the PR `Closes #<N>`); only the branch name
  deviates from the one-issue-one-branch convention. Flag it to the operator
  when reporting the PR, but don't treat it as a failure.
- The cloud session's allowed-tools must include **Bash** (for the `gh` PR /
  issue / board steps) — and **Task** if you want it to use
  `subagent-driven-development`; without Task it falls back to implementing the
  plan directly. Set this on the routine in the web UI.
- Reuse the single `implement-issue` trigger across all issues; do not create a
  trigger per issue.
