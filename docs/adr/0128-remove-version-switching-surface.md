# ADR 0128 — Remove the version-switching surface (activate / restore)

Date: 2026-10-17 · Status: accepted · Supersedes the switching half of 0100 and 0101

## Context

PR #140 removed the clip page's version History menu. The routes behind it
(`GET /api/review/clips/{id}/versions`, `POST …/versions/{n}/activate`,
`POST …/versions/{n}/restore`), `PublishService.reactivate`, `RestoreService`
and their tests stayed: roughly 500 lines reachable only by hand-made
requests, with no template or script calling them.

## Decision

Delete the three routes, `PublishService.reactivate` (+ `_switch_ops`) and
`RestoreService`. Clip versions are still recorded on every apply
(`clip_versions`, `PublishService.publish`) and the SyncEngine still advances
them, so the audit trail is intact; only the switching actions are gone.
Invariant 20 now reads "published clip state is a snapshot layer"; the
sentence about switching versions is dropped.

## Consequences

- Restoring an older version is a product decision for later; when it comes
  back it is designed against ADR 0127's ownership rule from scratch rather
  than resurrecting `_switch_ops`.
- `context.py` loses `restore_service`; `_load_live_snapshot` stays as the
  publish base (it is deliberately empty: snapshots record accepted deltas).
