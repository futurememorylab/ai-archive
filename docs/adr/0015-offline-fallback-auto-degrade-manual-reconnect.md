# 0015. Offline fallback: auto-degrade + manual reconnect

- **Date:** 2026-05-22
- **Status:** Accepted

## Context

The annotator crashed on startup without VPN and raised 5xx
on every read when CatDV went down mid-session. Users wanted to keep
working from the local cache while disconnected — list, open, scrub
clips that were already cached — without losing in-flight writes.

## Alternatives

(a) New `CacheOnlyArchiveAdapter` wrapper class —
heavier, doubles the read-API test surface. (b) Strictly automatic
fallback driven by `ConnectionMonitor` only — no env override,
operators couldn't boot without VPN being up at startup. (c) Auto-
degrade inside the existing `CatdvArchiveAdapter` via an injected
`is_online_provider` callable, plus a `CATDV_OFFLINE` env override and
a user-triggered reconnect from a topbar chip.

## Decision

(c). The 2026-05-19 abstraction already had cache-first
reads, `WriteQueue`, and `SyncEngine`; this finished the loop with the
smallest surface area. The connection state machine has exactly three
external states — `online`, `offline` (auto-degraded, reconnect via
chip), `forced_offline` (env flag, reconnect by restart). The monitor
halts its probe loop after a single failure rather than retrying
forever; the user reconnects on demand via `POST /api/connection/retry`.

## Consequences

Existing tests keep passing — `is_online_provider` defaults to
`None` which the adapter treats as "always online", and `forced_offline`
defaults to `False` on the monitor. Writes get the existing queue
behavior for free: `apply_changes` raises `RetryableError` when offline,
which is exactly what `SyncEngine` already retries on. Auth failure at
startup is treated as offline rather than fatal, matching the spirit of
"the app should be usable without CatDV". Two adapter-level deviations
from the original plan are documented inline: the column is
`canonical_json` (not `blob_json`) so the `LIKE` for free-text search
uses `json_extract(canonical_json, '$.notes.notes')` to avoid false
positives on JSON-key substrings; and `CatdvClient.__aenter__` is lazy
about auth, so we call `client.login()` explicitly at boot to detect
unreachable/unauthorized servers cleanly.
