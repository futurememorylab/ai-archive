# 0007. PR 7 — Filesystem archive adapter

- **Date:** 2026-05-19
- **Status:** Accepted

## Context

PR 7 ships the second `ArchiveProvider` adapter
(`FilesystemArchiveProvider`) plus the shared contract test suite, closing
the seven-PR migration. Six design calls had to be made: (a) how
`provider_clip_id` is derived from the on-disk layout; (b) what counts as a
"catalog" when subdirectories are present; (c) the policy when `ffprobe`
is absent; (d) how timecodes are encoded inside the sidecar; (e) whether
the FS adapter offers a real etag; (f) whether the FS adapter writes
through to `clip_cache` / `field_def_cache`.

## Alternatives

(a) `sha256` of the absolute path (opaque), the
absolute path itself (leaks `FS_ROOT`), or the filename alone (collides
across catalogs). (b) Each leaf directory could be its own catalog
(deep tree → fragmented UI), or recursion could be forbidden (forces
flat layout on the user). (c) Refuse to start without `ffprobe`
(blocks hobbyist installs), or treat probe failure as fatal per-clip
(noisy in degraded states). (d) Persist canonical SMPTE `txt` plus
`secs`+`fps` (redundancy + drift on fps change), or `secs` alone
(loses anchor against a future fps redetection). (e) Skip etags and
match CatDV's heuristic-only mode (would forfeit the cheap, correct
write-time concurrency that POSIX file ops give us). (f) Write through
to the existing cache mirrors (extra invalidation surface for no
latency win on local I/O).

## Decision

(a) `provider_clip_id` is the path of the media file
relative to `FS_ROOT` with the media extension stripped and OS
separators normalised to `/`. Example:
`FS_ROOT/archive_30s/clip001.mov` → `"archive_30s/clip001"`.
(b) A **catalog is a top-level directory under `FS_ROOT`**.
Subdirectories below contribute to `provider_clip_id` via recursion
but are not separate catalogs. Hidden directories (those starting with
`.`) and the literal `.archive` directory are excluded from the
catalog list. (c) `ffprobe` is optional: when `shutil.which("ffprobe")`
is `None`, `media_probe.probe()` logs a single warning per process and
returns `(duration_secs=0.0, fps=25.0)`. Subprocess failures or
malformed `ffprobe` JSON also fall back to defaults — the user can
still annotate; only timeline display will be inaccurate.
(d) Timecodes are persisted as `{"secs": float, "frm": int, "fps":
float}` triples with `frm = round(secs * fps)`. The canonical SMPTE
`txt` string is dropped on write — it is a display concern derivable
from `secs + fps`. (e) The FS adapter is etag-aware
(`supports_etag=True`); etag = SHA-256 of the sidecar bytes on disk;
missing sidecar = etag `None`. Writes that supply a stale etag return
`WriteResult(status="conflict", ...)` without touching disk. (f) The
FS adapter accepts `clip_cache_repo` / `field_def_cache_repo` / 
`db_provider` kwargs for registry-symmetry but ignores them.

## Consequences

(a) Path-derived ids are human-readable in the audit log,
unambiguous within a `FS_ROOT`, and survive cross-platform deploys
because we normalise the separator on the way in. (b) A one-level
catalog model matches the existing CatDV pane's switcher and lets
users still organise within a catalog by subdirectory without
exploding the UI. (c) The probe path is the only place that needs
external tooling; gating startup on it would block legitimate
deployments (test rigs, lightweight installs). One warning is enough
diagnostic — repeated warnings would spam the log. (d) Storing both
`secs` and `frm` lets a future fps-redetection migrate timelines
deterministically; storing `txt` adds drift potential without buying
anything the renderer cannot regenerate. (e) The atomic-rename write
path makes a SHA-256 etag both cheap and correct — every successful
write changes it, every conflict refuses cleanly. This is what the
spec wants and what CatDV cannot do today. (f) Sidecars are the cache:
the disk read is sub-millisecond, the JSON parse is fast, and the
canonical clip is reconstructed from on-disk truth every time. Adding
a second mirror introduces an invalidation surface (sidecar edited
outside the app, cache says otherwise) for no latency win.
