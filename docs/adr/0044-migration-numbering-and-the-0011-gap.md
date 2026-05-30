# 0044. Migration numbering and the 0011 gap

**Date:** 2026-05-30
**Status:** Accepted

## Context

`backend/migrations/` skips from `0010_live_sessions.sql` to
`0012_prompt_media_kind.sql`. The history:

- Commit `b55c8b0` added `0011_studio.sql` on a feature branch.
- Commit `e37b71a` independently added `0011_prompt_media_kind.sql` on
  a different branch (number collision).
- The studio version won the merge race when PR #9 landed on `main`
  (commit `8a9b2bb`).
- Three days later, PR #9 was reverted wholesale (commit `1065546`)
  because the implementation didn't match the intended design.
- Replacements went forward, not back: PR #8 used `0012_prompt_media_kind.sql`,
  PR #10 used `0013_studio.sql`. **Choosing 0012/0013 instead of
  reusing 0011 was correct** — it kept the historical sequence stable
  for any dev machine that had run main during the brief window PR #9
  was live.

The dev machine at `data/app.db` still has `0011_studio.sql` in
`schema_migrations` from that window. Fresh installs see no 0011
file on disk and skip the number cleanly.

## Alternatives

1. **Renumber forward, remove the gap.** Breaks dev DBs that have
   `0011_studio.sql` recorded — they'd re-apply the renumbered
   migrations because the names changed.
2. **Leave the gap as documented in this ADR only.** Future
   contributors might "fix" the gap, or might claim 0011 for a new
   migration without knowing the history.
3. **Sentinel file + runner-level enforcement** (chosen). A
   `0011_REVERTED.txt` file marks the number as reserved.
   `apply_migrations()` raises if any `.sql` file's numeric prefix
   collides with a sentinel. The runner also warns about
   `schema_migrations` entries whose source files are missing,
   surfacing the dev-DB state without breaking boot.

## Decision

- Migration files use a four-digit numeric prefix (`NNNN_<slug>.sql`).
- Reserved or reverted numbers get a `.txt` sentinel (e.g.
  `0011_REVERTED.txt`) documenting why the number is off-limits.
- `apply_migrations()` raises if any `.sql` file's numeric prefix
  matches a sentinel.
- `apply_migrations()` warns (does not fail) about
  `schema_migrations` rows whose source files no longer exist.
- New migrations claim the next unused number, not the lowest unused
  number.

## Consequences

- **Positive:** the 0011 reservation is now enforced by the runner,
  not by convention alone. A future PR claiming 0011 fails loudly
  with a clear error pointing at this ADR.
- **Negative:** the sentinel pattern adds a small amount of folklore
  every contributor must understand. The runner warning on dev DBs
  is informational noise (one line per orphaned entry per boot)
  until the orphan tables are cleaned up — which is out of scope for
  tier 1 (see the spec's open question).
- **Forward-looking:** the same sentinel pattern handles any future
  reverted migration. The ADR also documents that adopting a date-
  prefixed naming scheme (e.g.
  `20260601_0001_<slug>.sql`) or switching to Alembic remains the
  long-term option if number-collision pain returns.
