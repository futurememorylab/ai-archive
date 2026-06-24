# 0003. PR 3 — single migration file, clip TTL keyed off CanonicalClip.fetched_at

- **Date:** 2026-05-19
- **Status:** Accepted
- **Lifespan:** Feature

## Context

PR 3 adds `provider_id`/`provider_clip_id` to six clip-keyed
tables and creates two new mirror tables (`clip_cache`, `field_def_cache`).
Two design calls had to be made: (a) whether to split into two migration
files (one for ALTER TABLEs, one for new tables) or keep them together; and
(b) whose "now" wins when stamping `clip_cache.fetched_at` — the repo's
own `datetime.now()` at write time, or the `CanonicalClip.fetched_at` the
adapter computed via its injected clock.

## Alternatives

(a) Split migrations 0003 (provider columns) and 0004
(cache tables); use the repo's own clock for `fetched_at`. (b) Use the
adapter's clock end-to-end so tests can advance time deterministically.

## Decision

(a) Single file `0003_provider_id_and_caches.sql` — the changes
are conceptually one ("provider-aware identity") and the rollback boundary
should stay tight. (b) The repo writes `clip.fetched_at` (already computed
by the adapter from its own clock) into the row, rather than calling
`datetime.now()` again. Field-def cache uses `replace_all_for_provider`
with `_now_iso()` internally because there is no per-row "fetched_at" on
the canonical `FieldDef`; tests of TTL expiry there would need a different
fixture.

## Consequences

Two migrations doubled the test surface without buying anything.
Using the adapter's clock for `clip_cache.fetched_at` makes TTL expiry
testable with an injected clock — important for the offline-mode work in
later PRs where time-based behaviour must be deterministically exercised.
