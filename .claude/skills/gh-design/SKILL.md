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

2. **Move to Design, assign yourself.** `set_status N Design`, add the label, and
   assign the issue to whoever is running this skill (the person taking it from
   Backlog into Design) so the board shows clear ownership:
   ```bash
   gh issue edit N -R futurememorylab/ai-archive \
     --add-label "phase:design" --add-assignee "@me"
   ```

3. **Create the branch.** Slug = kebab-cased title, ~5 words. The branch name
   starts with the bare issue number — no `issue-` prefix.
   ```bash
   git switch -c "${N}-${SLUG}" main   # e.g. 13-centralised-enumeration
   ```

4. **Brainstorm.** Invoke `superpowers:brainstorming` with the issue body as the
   idea. It writes the spec to `docs/superpowers/specs/`.

5. **Plan.** Invoke `superpowers:writing-plans` against that spec. It writes the
   plan to `docs/superpowers/plans/`.

6. **Commit & push the design.**
   ```bash
   git add docs/superpowers/specs docs/superpowers/plans
   git commit -m "docs(#${N}): design spec + implementation plan"
   git push -u origin "${N}-${SLUG}"
   ```
   Then comment the artefacts on the issue:
   ```bash
   gh issue comment N -R futurememorylab/ai-archive --body \
     "Design ready on branch \`${N}-${SLUG}\`.
   - Spec: <spec path>
   - Plan: <plan path>
   Run \`gh-handoff\` to implement in the cloud."
   ```

7. **Stop.** Report that issue #N is designed and ready to hand off. Do **not**
   launch the cloud session — that is `gh-handoff`'s job and a separate human
   decision.

## Guardrails

- One issue → one branch `<N>-<slug>` (bare number first, no `issue-` prefix) →
  (later) one PR. Never reuse a branch across issues.
- If the branch already exists, check it out instead of recreating it.
- Honour the repo's git workflow: branch off `main`, never commit design docs
  straight to `main`.
