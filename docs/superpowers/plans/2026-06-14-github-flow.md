# GitHub Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the "AI-Archive Flow" GitHub Project board and two local Claude Code skills (`gh-design`, `gh-handoff`) that drive issues through Backlog → Design → Implementation → Test → Done.

**Architecture:** A GitHub Projects v2 board with a single-select `Status` field is the visible pipeline. GitHub's built-in workflows handle the auto-add and PR/merge edges; two thin skills handle the two human decision points (start design, hand off). `gh-design` runs the superpowers brainstorming→plan flow locally and commits the plan on a per-issue branch. `gh-handoff` fires a reusable claude.ai remote trigger that implements the committed plan in the cloud and opens a PR.

**Tech Stack:** `gh` CLI (project + issue commands), `jq`, GitHub Projects v2, the in-process `RemoteTrigger` tool (claude.ai `/v1/code/triggers` API), markdown SKILL.md files.

**Source spec:** `docs/superpowers/specs/2026-06-14-github-flow-design.md`

**Note on verification style:** This plan produces GitHub configuration and prose skill files, not unit-testable code. "Verification" steps therefore run `gh`/`jq` commands and assert on their output, or walk an acceptance flow from the spec. There is no pytest layer to add.

---

## File Structure

- `.claude/skills/gh-design/SKILL.md` — **Create.** The Design-phase orchestrator skill.
- `.claude/skills/gh-handoff/SKILL.md` — **Create.** The Implementation hand-off skill.
- `docs/adr/NNNN-github-flow-board.md` — **Create.** Records the deliberate deviations (UI-only workflows, unrestricted branch pushes, remote-trigger preview dependency).
- `docs/decisions.md` — **Modify.** Add the new ADR row to the index table.

No application code changes. The GitHub Project, its `Status` field, the three `phase:*` labels, and the built-in workflows are created as side effects of Tasks 1–4 (state lives on GitHub, not in the repo).

A shared `Status`-setting shell snippet is duplicated into both skills deliberately — each skill must stand alone for an agent reading only one of them, and the snippet is short. (DRY yields to skill self-containment here.)

---

## Task 1: Create the GitHub Project and capture its identifiers

**Files:** none (creates GitHub state). Record the captured values in the task notes for later tasks.

- [ ] **Step 1: Confirm no board exists yet**

Run: `gh project list --owner futurememorylab --format json | jq -r '.projects[].title'`
Expected: output does NOT contain `AI-Archive Flow`. (If it already exists, skip creation and just capture identifiers in Step 3.)

- [ ] **Step 2: Create the project**

Run:
```bash
gh project create --owner futurememorylab --title "AI-Archive Flow" --format json
```
Expected: JSON containing `"number"`, `"id"` (a `PVT_…` node id), and `"url"`. Note the URL — it is `https://github.com/orgs/futurememorylab/projects/<number>`.

- [ ] **Step 3: Capture the project number and node id into shell vars for this session**

Run:
```bash
PROJ=$(gh project list --owner futurememorylab --format json \
  | jq -r '.projects[] | select(.title=="AI-Archive Flow") | .number')
PROJECT_ID=$(gh project view "$PROJ" --owner futurememorylab --format json | jq -r '.id')
echo "PROJ=$PROJ PROJECT_ID=$PROJECT_ID"
```
Expected: a non-empty integer `PROJ` and a `PVT_…` `PROJECT_ID`.

- [ ] **Step 4: Commit checkpoint — none**

No repo changes in this task; nothing to commit. Record `PROJ`, `PROJECT_ID`, and the board URL in the task notes so Tasks 2–6 can reuse them.

---

## Task 2: Configure the Status field with the five board columns

**Files:** none (creates GitHub state).

A freshly created Projects v2 board ships with a default single-select `Status` field whose options are `Todo / In Progress / Done`. `gh` cannot edit the options of an existing field, so we delete the default and recreate `Status` with our five options (order matters — it sets column order).

- [ ] **Step 1: Find the default Status field id**

Run:
```bash
STATUS_FIELD_ID=$(gh project field-list "$PROJ" --owner futurememorylab --format json \
  | jq -r '.fields[] | select(.name=="Status") | .id')
echo "$STATUS_FIELD_ID"
```
Expected: a `PVTSSF_…` field id (single-select field).

- [ ] **Step 2: Delete the default Status field**

Run: `gh project field-delete --id "$STATUS_FIELD_ID"`
Expected: success message; no error. (Deleting the field also drops its options — acceptable, the board has no items yet.)

- [ ] **Step 3: Recreate Status with the five columns in order**

Run:
```bash
gh project field-create "$PROJ" --owner futurememorylab \
  --name "Status" --data-type SINGLE_SELECT \
  --single-select-options "Backlog,Design,Implementation,Test,Done"
```
Expected: success; returns the new field.

- [ ] **Step 4: Verify the field and capture option ids**

Run:
```bash
gh project field-list "$PROJ" --owner futurememorylab --format json \
  | jq -r '.fields[] | select(.name=="Status") | .options[] | "\(.name)\t\(.id)"'
```
Expected: exactly five lines — `Backlog`, `Design`, `Implementation`, `Test`, `Done`, each with an option id. These ids are what the skills resolve at runtime (they are not hard-coded).

- [ ] **Step 5: Commit checkpoint — none**

No repo changes.

---

## Task 3: Link the project to the repo and create phase labels

**Files:** none (creates GitHub state).

- [ ] **Step 1: Link the board to the repo**

Run: `gh project link "$PROJ" --owner futurememorylab --repo futurememorylab/ai-archive`
Expected: success message confirming the link.

- [ ] **Step 2: Create the three phase labels**

Run:
```bash
gh label create "phase:design"         -R futurememorylab/ai-archive -c "1D76DB" -d "In the Design lane" --force
gh label create "phase:implementation" -R futurememorylab/ai-archive -c "0E8A16" -d "In the Implementation lane" --force
gh label create "phase:test"           -R futurememorylab/ai-archive -c "FBCA04" -d "In the Test lane" --force
```
Expected: three "Label created" messages (or "updated" with `--force` if re-run).

- [ ] **Step 3: Verify labels exist**

Run: `gh label list -R futurememorylab/ai-archive | grep '^phase:'`
Expected: three rows — `phase:design`, `phase:implementation`, `phase:test`.

- [ ] **Step 4: Commit checkpoint — none**

No repo changes.

---

## Task 4: Enable the built-in Project workflows (operator, UI)

**Files:** none (creates GitHub state). **This task is run by the operator** — GitHub's built-in Project workflows are not exposed by `gh` or the public API, so they must be toggled in the web UI. The agent provides the exact steps and then verifies the outcome behaviourally in Task 7.

- [ ] **Step 1: Open the board's workflow settings**

Operator action: open `https://github.com/orgs/futurememorylab/projects/<number>/workflows`.

- [ ] **Step 2: Enable "Auto-add to project"**

Operator action: enable the **Auto-add to project** workflow; set its filter to `is:issue` on repository `futurememorylab/ai-archive`. Save and ensure the toggle is **On**.

- [ ] **Step 3: Enable "Item added to project" → Status: Backlog**

Operator action: enable **Item added to project**; set action to **Set Status = Backlog**. Save, toggle **On**.

- [ ] **Step 4: Enable "Pull request linked" / "Code changes requested" → Status: Test**

Operator action: enable the workflow that fires when a linked pull request is opened (labelled "Pull request" / "Item added" depending on UI wording); set action to **Set Status = Test**. Save, toggle **On**.

- [ ] **Step 5: Enable "Item closed" → Status: Done**

Operator action: enable **Item closed**; set action to **Set Status = Done**. Save, toggle **On**.

- [ ] **Step 6: Verify (manual)**

Operator action: create a throwaway test issue (`gh issue create -R futurememorylab/ai-archive -t "wf smoke test" -b "delete me"`), confirm it appears on the board in **Backlog** within ~10s, then close it and confirm it moves to **Done**. Delete the issue afterward (`gh issue delete <n> --yes`). This is acceptance flow #1 from the spec.

---

## Task 5: Author the `gh-design` skill

**Files:**
- Create: `.claude/skills/gh-design/SKILL.md`

This skill is prose. Step 1 writes the full file; Step 2 verifies it loads and reads sanely.

- [ ] **Step 1: Write `.claude/skills/gh-design/SKILL.md`**

Create the file with exactly this content:

````markdown
---
name: gh-design
description: Run the Design phase for a GitHub issue in the AI-Archive Flow board. Use when the user says "design #N", "refine issue N", or "start design on <idea>". Moves the issue to Design, creates its feature branch, runs the superpowers brainstorming→plan flow, and commits the spec + plan on the branch ready for hand-off. Local only — does not launch the cloud session.
---

# gh-design — Design phase orchestrator

Drives one Backlog issue through the **Design** lane: brainstorm → spec → plan,
all committed on a per-issue branch so `gh-handoff` can ship it to the cloud.

**Board constants:** owner `futurememorylab`, repo `futurememorylab/ai-archive`,
project title `AI-Archive Flow`. Resolve the project number at runtime — never
hard-code it:

```bash
OWNER=futurememorylab
PROJ=$(gh project list --owner "$OWNER" --format json \
  | jq -r '.projects[] | select(.title=="AI-Archive Flow") | .number')
PROJECT_ID=$(gh project view "$PROJ" --owner "$OWNER" --format json | jq -r '.id')
```

## Reusable: set an issue's board Status

Given an issue number `$N` and a target column `$COL` (e.g. `Design`):

```bash
set_status() {
  local N="$1" COL="$2"
  local fields item_id field_id opt_id
  fields=$(gh project field-list "$PROJ" --owner "$OWNER" --format json)
  field_id=$(echo "$fields" | jq -r '.fields[] | select(.name=="Status") | .id')
  opt_id=$(echo "$fields" | jq -r --arg c "$COL" \
    '.fields[] | select(.name=="Status") | .options[] | select(.name==$c) | .id')
  item_id=$(gh project item-list "$PROJ" --owner "$OWNER" --format json --limit 800 \
    | jq -r --argjson n "$N" '.items[] | select(.content.number==$n) | .id')
  gh project item-edit --project-id "$PROJECT_ID" --id "$item_id" \
    --field-id "$field_id" --single-select-option-id "$opt_id"
}
```

If `item_id` comes back empty the issue is not on the board yet — add it with
`gh project item-add "$PROJ" --owner "$OWNER" --url <issue-url>` then retry.

## Flow

1. **Resolve the issue.** If the user gave a number, `gh issue view N --json
   number,title,body,labels,url`. If not, list Backlog candidates (open issues
   carrying no `phase:*` label) and ask which:

   ```bash
   gh issue list -R futurememorylab/ai-archive --state open --json number,title,labels \
     | jq -r '.[] | select([.labels[].name] | any(startswith("phase:")) | not)
              | "#\(.number) \(.title)"'
   ```

2. **Move to Design.** `set_status N Design` and add the label:
   `gh issue edit N -R futurememorylab/ai-archive --add-label "phase:design"`.

3. **Create the branch.** Slug = kebab-cased title, ~5 words.
   ```bash
   git switch -c "issue-${N}-${SLUG}" main
   ```

4. **Brainstorm.** Invoke `superpowers:brainstorming` with the issue body as the
   idea. It writes the spec to `docs/superpowers/specs/`.

5. **Plan.** Invoke `superpowers:writing-plans` against that spec. It writes the
   plan to `docs/superpowers/plans/`.

6. **Commit & push the design.**
   ```bash
   git add docs/superpowers/specs docs/superpowers/plans
   git commit -m "docs(#${N}): design spec + implementation plan"
   git push -u origin "issue-${N}-${SLUG}"
   ```
   Then comment the artefacts on the issue:
   ```bash
   gh issue comment N -R futurememorylab/ai-archive --body \
     "Design ready on branch \`issue-${N}-${SLUG}\`.
   - Spec: <spec path>
   - Plan: <plan path>
   Run \`gh-handoff\` to implement in the cloud."
   ```

7. **Stop.** Report that issue #N is designed and ready to hand off. Do **not**
   launch the cloud session — that is `gh-handoff`'s job and a separate human
   decision.

## Guardrails

- One issue → one branch `issue-<N>-<slug>` → (later) one PR. Never reuse a
  branch across issues.
- If the branch already exists, check it out instead of recreating it.
- Honour the repo's git workflow: branch off `main`, never commit design docs
  straight to `main`.
````

- [ ] **Step 2: Verify the skill is discoverable and well-formed**

Run:
```bash
head -4 .claude/skills/gh-design/SKILL.md
jq -e . </dev/null 2>/dev/null; gh --version >/dev/null && echo "gh ok"
```
Expected: the frontmatter block (`---`, `name: gh-design`, `description:`, `---`) prints, and `gh ok` confirms the CLI the skill depends on is present. (Skill loading itself is validated end-to-end in Task 7.)

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/gh-design/SKILL.md
git commit -m "feat(skills): add gh-design Design-phase orchestrator"
```

---

## Task 6: Author the `gh-handoff` skill

**Files:**
- Create: `.claude/skills/gh-handoff/SKILL.md`

The hand-off depends on the claude.ai remote-trigger API, which is a 2026
research preview — so the skill first **discovers** the trigger body schema from
a live `list`/`get` rather than assuming it, then creates the reusable trigger
if missing and runs it.

- [ ] **Step 1: Write `.claude/skills/gh-handoff/SKILL.md`**

Create the file with exactly this content:

````markdown
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
     "prompt": "You are implementing a pre-written plan. The issue number and branch are in the run context. Steps: 1) `git fetch origin <branch> && git checkout <branch>`. 2) Read the implementation plan under docs/superpowers/plans/ on that branch. 3) Use the superpowers:subagent-driven-development skill to implement the plan task by task. 4) Commit and push to the SAME branch <branch>. 5) Open a pull request to main whose body contains `Closes #<N>`. 6) Comment the PR URL back on issue #<N>. Do not merge."
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
   hands-off: the cloud PR will move the issue to **Test** (built-in workflow),
   they test manually, squash-merge, and `Closes #N` moves it to **Done**.

## Guardrails

- Never fire a trigger when the preconditions fail — a cloud run against a
  missing plan wastes a session and confuses the board.
- The cloud session runs as the connected GitHub identity and needs
  **unrestricted branch pushes** enabled on the claude.ai environment (one-time
  operator setup) to push to `issue-<N>-<slug>`. If the run reports it could
  only push a `claude/`-prefixed branch, that setting is off — tell the operator.
- Reuse the single `implement-issue` trigger across all issues; do not create a
  trigger per issue.
````

- [ ] **Step 2: Verify the skill is well-formed**

Run:
```bash
head -4 .claude/skills/gh-handoff/SKILL.md
```
Expected: the frontmatter prints with `name: gh-handoff`.

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/gh-handoff/SKILL.md
git commit -m "feat(skills): add gh-handoff cloud Implementation hand-off"
```

---

## Task 7: End-to-end acceptance dry-run

**Files:** none. Walks the spec's manual acceptance flows against the live setup.

- [ ] **Step 1: Acceptance flow #1 — new issue lands in Backlog**

Run: `gh issue create -R futurememorylab/ai-archive -t "ACCEPT: backlog test" -b "temp"`
Then: `gh project item-list "$PROJ" --owner futurememorylab --format json | jq -r '.items[] | select(.content.title=="ACCEPT: backlog test") | .status'`
Expected: `Backlog`. Clean up: `gh issue delete <n> --yes`.

- [ ] **Step 2: Acceptance flow #2 — gh-design (dry check)**

Confirm the `gh-design` skill loads (it appears in the available-skills list after the commit) and that `set_status` + the Backlog-candidate query run without error against a real Backlog issue. Do not run a full brainstorm here; just confirm the board-moving and branch-creation commands succeed on a scratch issue, then revert (`git switch main`, delete the scratch branch, move the issue back to Backlog).

- [ ] **Step 3: Acceptance flow #4 — gh-handoff refuses an un-designed issue**

On a Backlog issue with no branch, run the precondition block from `gh-handoff`.
Expected: it prints "No remote branch … — run gh-design first" and exits without
touching the board or firing a trigger.

- [ ] **Step 4: Commit checkpoint — none**

No repo changes in this task.

---

## Task 8: Record the ADR

**Files:**
- Create: `docs/adr/NNNN-github-flow-board.md` (NNNN = one higher than the last ADR)
- Modify: `docs/decisions.md`

- [ ] **Step 1: Find the next ADR number**

Run: `ls docs/adr | sort | tail -1`
Expected: the highest existing `NNNN-…` file; the new number is one higher.

- [ ] **Step 2: Write the ADR**

Create `docs/adr/NNNN-github-flow-board.md` using the repo's MADR-lite format
(`# NNNN. GitHub Flow board and skills`, `**Date:** 2026-06-14`,
`**Status:** Accepted`, then `## Context / ## Alternatives / ## Decision /
## Consequences`). Capture the three deliberate deviations: (a) built-in Project
workflows configured via the UI because `gh`/the API don't expose them;
(b) **unrestricted branch pushes** enabled on the claude.ai environment, trading
the `claude/`-prefix guardrail for a single-branch-per-issue PR; (c) dependence
on the remote-trigger **research-preview** API, isolated to `gh-handoff` steps
1–3. Reference the spec at `docs/superpowers/specs/2026-06-14-github-flow-design.md`.

- [ ] **Step 3: Update the decisions index**

Modify `docs/decisions.md`: add a row to the index table for the new ADR
(number, title, date, status), matching the existing table's columns.

- [ ] **Step 4: Commit**

```bash
git add docs/adr docs/decisions.md
git commit -m "docs(adr): record GitHub Flow board + skills decisions"
```

---

## Self-review notes

- **Spec coverage:** board/Status/columns (Tasks 1–2), labels + conventions
  (Task 3), built-in workflows (Task 4), `gh-design` (Task 5), `gh-handoff`
  (Task 6, incl. reusable-trigger + unrestricted-push handling), acceptance flows
  (Task 7), ADR per repo policy (Task 8). One-time operator setup from the spec:
  `gh auth refresh` already done; repo-connect + unrestricted pushes are operator
  prerequisites called out in Task 6's guardrails and Task 4.
- **Placeholder scan:** the only intentional `<…>` placeholders are per-issue
  runtime values (issue number, slug, paths, next ADR number) that cannot be
  known until execution — each is accompanied by the command that resolves it.
- **Naming consistency:** `set_status N COL`, `PROJ`, `PROJECT_ID`, branch
  `issue-<N>-<slug>`, trigger `implement-issue`, labels `phase:design|implementation|test`
  are used identically across Tasks 1–8 and both skill files.
