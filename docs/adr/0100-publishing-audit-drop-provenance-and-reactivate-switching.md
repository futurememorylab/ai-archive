# 0100. Publishing audit: drop the CatDV provenance field, switch versions by re-activation, harden error handling

**Date:** 2026-06-17
**Status:** Accepted — supersedes parts of [0099](./0099-clip-version-history-publish-snapshots.md)
**Lifespan:** Invariant

## Context

Operator QA of the clip-version-history feature (ADR 0099) surfaced a cluster of
real anomalies, two of them regressions introduced by 0099. A full walk of the
publish path (`PublishService` → `WriteQueue` → `pending_operations` →
`SyncEngine` → `apply_changes` → `catdv_client` → `build_put_payload`) found:

- **A1.** Every publish wrote `pragafilm.anno_version` (the 0099 provenance
  breadcrumb) as a CatDV user field. That field is not defined in CatDV's
  schema, so the PUT returned **HTTP 500** and the *entire* annotation write
  (markers + fields + notes) failed. Publishing was effectively broken.
- **A2.** `catdv_client._call_json` parsed every response straight into the
  `Envelope` model. CatDV's 500 body carries a numeric `status: 500`, which
  isn't in `Literal["OK","AUTH","ERROR","BUSY"]`, so it raised a raw
  `pydantic.ValidationError`. `apply_changes` only catches
  `CatdvError/Busy/Auth`, so it escaped as an unknown exception → the
  `SyncEngine` retried it **forever** with an unreadable message (the
  "Publishing… N" pile-up, raw pydantic text in the sync drawer).
- **A3.** "Restore & publish" was publish-forward: it forked a **new, identical
  version** on every click. Repeatedly returning to v2 produced v3, v4, v5 — the
  "duplicating" the operator reported, and the reason switching felt terrible.
- **A4.** The `SyncEngine` merges all of a clip's pending ops into one PUT and
  flips only `max(version_id)` live, so older queued versions could stay
  `publishing` forever (compounded by A1/A2 blocking every drain).
- **A5.** The history-panel click delegation attached its listener without a
  double-attach guard — a re-init would fire the POST (and create a write)
  twice.

## Alternatives

- **A1: keep the breadcrumb but make it best-effort / define the field in
  CatDV.** Rejected — the breadcrumb is a nice-to-have; history lives wholly in
  our app, and an optional field is not worth a schema dependency that can break
  the core write. Dropped entirely.
- **A2: 5xx → fatal, or 5xx → retryable-by-status.** Rejected in favour of
  parse-first: the *envelope's* own status (BUSY/ERROR/AUTH) should classify the
  outcome even on a 5xx, and only a genuinely unparseable body becomes a
  `CatdvError`. This keeps BUSY-at-503 retryable without special-casing codes.
- **A3: idempotent publish-forward (skip if identical), or restore-into-draft
  only.** Rejected — the operator wanted true *switching*: clicking a version
  makes it live, full stop, with history at its real count.

## Decision

- **A1 — drop the provenance field.** `PublishService` no longer writes
  `pragafilm.anno_version`; `build_provenance_value` / `PROVENANCE_FIELD` are
  gone. CatDV receives only the real annotation changes. (Reverses the 0099
  breadcrumb decision.)
- **A2 — `catdv_client._envelope_or_raise` is parse-first.** A well-formed
  envelope is returned (caller's `is_busy`/`is_ok` decides, even on a 5xx); a
  body the model can't validate (`{"status": 500}`, an HTML error page, non-JSON)
  becomes a classified `CatdvError`. So a CatDV error fails cleanly and is
  bounded by the retry ceiling instead of looping forever.
- **A3 — switching is re-activation, not publish-forward.**
  `PublishService.reactivate` re-PUTs an existing version's snapshot and (on
  success) marks it live again, superseding the current live, **without
  inserting a new row**. The history menu's primary action is **"Make live"**
  (`POST .../versions/{n}/activate`); **"Edit as draft"** keeps the
  load-into-draft path. The publish-forward `restore-and-publish` route and the
  `origin='restore'` flow are removed. Re-activation re-bases on the live clip
  (`expected_etag=None`) — an explicit "make this live" override.
- **A4 — `mark_live` supersedes orphaned siblings.** It now flips other
  `live` *and* `publishing` rows for the clip to `superseded`, so a merged
  multi-publish (or a stuck pile-up) cannot orphan versions; exactly one row
  per clip ends `live`.
- **A5 — guarded delegation.** `_initRestoreDelegation` sets a
  `_versionActionsBound` flag and returns if already bound.

## Consequences

- Publishing works again; a CatDV error now surfaces as a readable, bounded
  failure (drawer "Failed", discardable) instead of an eternal "Publishing…".
- Version history no longer proliferates on switching; "Make live" is the
  glanceable switch and supersedes the rest.
- **Existing stuck state is not auto-migrated:** ops enqueued *before* this fix
  still carry the old `pragafilm.anno_version` op in their `op_json`, so on the
  next drain they hit the CatDV 500 — now classified, so they move to `failed`
  and can be discarded from the sync drawer (rather than retrying forever).
  Orphaned `publishing` versions are cleaned the next time a good version is
  made live.
- Tests: provenance assertions inverted; new coverage for `reactivate` (no new
  version, re-enqueues the snapshot), `mark_live` superseding `publishing`
  siblings, the CatDV-500/numeric-status → `CatdvError` path, and the
  `/activate` route. Full suite green (1702).
