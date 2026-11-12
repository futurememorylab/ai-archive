## What changes for the user

<!-- Plain words, no code terms. Before: ... After: ... -->

## How it was tested

<!-- Commands run and their result, screenshots, or the manual acceptance
flow you followed. -->

## Decisions

<!-- Required. CI (tools/decision_trigger_gate.py) fails a PR that touches a
decision-sensitive path (tools/decision_triggers.toml: migrations, deploy,
dependencies, .importlinter, model catalog, writeback payloads, CatDV
session code, auth, removed routes) unless it adds or changes an ADR, or
has a No-ADR line. Write one of these outside this comment:

ADR: docs/adr/NNNN-....md
No-ADR: <why this is not a decision, in one line>

The bar for an ADR is in docs/decisions.md. If this PR contradicts an
invariant in docs/architecture-invariants.md, it needs an ADR that
supersedes it. -->

## Risk

<!-- Low (wording, layout, docs), Medium (features, batches, lists) or High
(writes to the archive, CatDV login/sessions, deleting anything, deploy/CI,
gate files, credentials). If unsure, pick the higher one. -->

## How to undo it

<!-- Revert this PR, or what else is needed (data migration, settings). -->
