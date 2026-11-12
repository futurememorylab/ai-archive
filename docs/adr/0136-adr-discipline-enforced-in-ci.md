# ADR 0136 — ADR discipline is enforced in CI, not only in agent hooks

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant ·
Refines: [0129](./0129-quality-gates-only-tighten.md)

## Context

The November SDLC audit found that the PR process held across every tool
(Claude Code, Cowork, claude.ai Projects, hand edits), but decisions still
shipped without ADRs: #134 persisted the CatDV session id in replicated
`app_meta`, #132 hid a deploy `--max=1` change, #165 added signed-URL
uploads, #126 added multi-catalog support, and #161 contradicted invariant
9b. Supersessions were half-marked (the target ADR never pointed forward).
PR #179 recorded the missing ADRs (0131–0135) after the fact.

The existing guards (`.claude/hooks/`) run only inside Claude Code. The
`main` ruleset requires zero approvals, no Code Owner review, and lets an
admin bypass it. So the only check every change meets, whatever wrote it,
is the required `lint` / `test` CI jobs.

## Alternatives

- **Agent instructions only** (AGENTS.md, project instructions). Cheap, but
  that is what we had; it depends on each tool reading and honouring them.
- **Require Code Owner review.** GitHub doesn't let an author approve their
  own PR and agent PRs are opened under Peter's account (ADR 0129), so this
  would block every gate-file PR.
- **CI checks in the required `lint` job** (chosen). They run for every PR
  regardless of tool, and become required without a ruleset change.

## Decision

1. `tools/adr_lint.py` checks the ADR log mechanically: every ADR has
   `Date`, `Status` and `Lifespan`; every ADR is indexed in
   `docs/decisions.md` and every index link resolves; when ADR X supersedes
   NNNN, NNNN is `Lifespan: Superseded` or marked partly superseded and
   points back at X; every ADR and invariant number cited on the invariants
   page resolves; the synthesis log is at most 20 ADRs behind. Violations
   that predate the gate go in `.adr-lint-baseline.json`, which only
   shrinks (empty at introduction).
2. `tools/decision_trigger_gate.py` fails a PR whose diff touches a path in
   `tools/decision_triggers.toml` (new migrations, deploy config,
   dependencies, `.importlinter`, model-catalog entries, writeback payload
   builders, CatDV session code, auth/access control, removed routes) unless
   the PR adds or changes an ADR or its description has a
   `No-ADR: <reason>` line. The trigger list is data.
3. `tests/unit/test_invariants_enforced_by.py` resolves every file named in
   an invariant's *Enforced by:* and keeps a list of convention-only
   invariants that may only shrink.
4. The PR template has a required **Decisions** section, and the Claude PR
   review checks ADR triggers and invariant contradictions.
5. The tool files, the trigger list, the baseline and the guard test are
   CODEOWNERS-owned gate files (ADR 0129).

## Consequences

- About one PR in three trips a trigger (measured over the last 60 merges).
  Most need only a one-line `No-ADR:` reason; the reason is the record.
- The `No-ADR:` line is read from the PR description at CI time, from the
  API when the job has a token (a re-run replays the original event, so
  the payload alone would miss an edit). Editing the description doesn't
  start CI by itself; re-run the `lint` job or push a commit.
- The gate is a prompt, not a proof: a determined author can write a weak
  reason, and any edit under `docs/adr/` (even a typo fix in an old ADR)
  counts as "changes an ADR". The Claude review and Peter's merge are the backstop.
- Invariant 28 records that the ADR rules are enforced in CI.
