# 0090. Drop "last sign-in" tracking from the admin console (UI + backend)

**Date:** 2026-06-17
**Status:** Accepted

## Context

The IAP roles admin console (ADR 0085) showed a **"Last sign-in"** column per
member, backed by a `user_roles.last_seen_at` column. It was populated by
`UserRolesRepo.mark_seen`, called from the auth gate on every authenticated
request, throttled to ≤ once/minute per user to keep Litestream write churn
down.

The feature earned its keep poorly: it was cosmetic bookkeeping that put a
write on the auth critical path. That write already caused a production outage
(2026-06-17, #72) when a transient SQLite write-lock turned an unguarded
`mark_seen` into a 500 on every authenticated request — patched by making the
touch best-effort. We were asked to remove last-seen entirely, from the UI
*and* the backend, not just hide the column.

The complication: `mark_seen` did **two** things under one name —

1. touched `last_seen_at` (the thing to remove), and
2. flipped `invited → active` on a user's first authenticated sight.

The second is load-bearing: it is the **only** path that promotes a
pre-invited member to `active`. An admin invites someone (`status='invited'`),
and that row stays `invited` until their first sign-in flips it. There is no
"activate" button in the console. Deleting the flip along with last-seen would
strand every invited user in `invited` forever — an unrequested regression.

## Alternatives

- **Hide the column in the template only.** Rejected — the ask was explicit
  about removing it from the backend too; leaving a dead column and a per-
  request write that maintains it is exactly the cosmetic-write-on-the-auth-
  path liability we're trying to delete.
- **Delete `mark_seen` wholesale (column + flip).** Rejected — kills the
  invited→active promotion, the only mechanism that activates pre-invited
  members.
- **Keep the `mark_seen` name for the flip-only method.** Rejected — the name
  is the last-seen verb; a `mark_seen` that marks nothing seen misleads the
  next reader. The whole point of the task is to remove "last seen", so the
  name goes with it.
- **Edit `0021_user_roles.sql` to drop the column.** Rejected — `0021` is
  already applied in prod; migrations are append-only. The drop is a new
  forward migration.

## Decision

Remove last-seen tracking end to end, preserving the invited→active flip:

- **DB:** new migration `0022_drop_last_seen.sql` runs
  `ALTER TABLE user_roles DROP COLUMN last_seen_at` (SQLite ≥ 3.35; prod is far
  newer). The original `0021` table definition is left untouched.
- **Repo:** `mark_seen` → **`activate_on_first_sight`**, which does only
  `UPDATE user_roles SET status='active' WHERE email=? AND status='invited'`.
  Because the `WHERE` only matches an `invited` row, it writes exactly once (on
  first sign-in) and is a no-op thereafter — *fewer* writes than the old
  once-per-minute touch, so Litestream churn drops. `last_seen_at` is removed
  from `_COLS`, so it no longer surfaces as a member field.
- **Auth gate (`main.py`):** calls `activate_on_first_sight`, still wrapped
  best-effort (swallow + log). Swallowing remains safe: an invited user already
  admits at the gate (`get_active_role` accepts `invited`) and flips on a later
  request, so a transient lock never blocks the request and never loses the
  promotion permanently.
- **UI (`_admin_members.html`):** the "Last sign-in" column header and the
  `last_seen_at` cell are removed.

This supersedes the last-seen aspects of ADR 0085 and the
`2026-06-14-iap-roles-admin-console-design` spec; those remain as the
historical record of the original design.

## Consequences

- The admin members table is now Member · Role · Status · (actions). Invited
  members still auto-activate on first sign-in; the only observable loss is the
  last-sign-in timestamp, which was the requested removal.
- One fewer write on the auth critical path in steady state (the flip is a
  one-time write, not a recurring touch). The #72 best-effort guard and its
  regression test survive under the new method name.
- Tests updated: `test_activate_on_first_sight_flips_invited_to_active`
  asserts the flip *and* that `last_seen_at` is no longer a member field;
  `test_admin_console` and `test_auth_gate` reference the renamed method.
- A future contributor who wants a last-seen / activity surface should add it
  as a deliberate, off-the-critical-path feature (e.g. a separate audit table
  written from a background job), not by reviving a per-request touch.
