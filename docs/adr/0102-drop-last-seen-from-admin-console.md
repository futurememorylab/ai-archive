# 0102. Drop "last sign-in" tracking from the admin console (UI + backend)

> Renumbered from 0090 → 0102 when merging `main` into `feat/clip-version-history`:
> that branch had independently taken 0090–0101 (write-back + clip version history).

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
touch best-effort. **Issue #73 named the deeper root cause**: even with the
≤ once/minute SQL guard, `mark_seen` still ran an `UPDATE` *and* `conn.commit()`
on **every** request — the throttle lived in the `WHERE`, not in a Python check,
so a no-op write still did a DB round-trip + commit on every browse. That
per-request commit is what kept poking the single SQLite connection Litestream
was checkpointing — the lock contention that crashed the container. So removing
last-seen has to remove the **per-request write on browse**, not just the
column. We were asked to remove last-seen entirely, from the UI *and* the
backend, not just hide it.

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
- **Keep calling the flip unconditionally, relying on its `WHERE` to no-op.**
  Rejected — this is exactly the trap issue #73 calls out: a no-op `UPDATE` +
  `commit()` still runs on every browse, so the per-request write contention
  survives. The write must be skipped in Python for non-invited users.
- **Gate the write with an in-memory "recently seen" cache** (issue #73's
  suggested shape for the old throttle). Rejected now that last-seen is gone:
  the only remaining write is a *one-time* flip, so an in-memory cache is
  unnecessary state that also wouldn't survive restarts or coordinate across
  instances. The DB status itself is the stateless gate.

## Decision

Remove last-seen tracking end to end, preserving the invited→active flip:

- **DB:** new migration `0022_drop_last_seen.sql` runs
  `ALTER TABLE user_roles DROP COLUMN last_seen_at` (SQLite ≥ 3.35; prod is far
  newer). The original `0021` table definition is left untouched.
- **Repo:** `mark_seen` → **`activate_on_first_sight`**, which does only
  `UPDATE user_roles SET status='active' WHERE email=? AND status='invited'`.
  `last_seen_at` is removed from `_COLS`, so it no longer surfaces as a member
  field.
- **Gate lookup:** `get_active_role` → **`get_gate_state`**, returning
  `(role, status) | None` in one read. The gate now has both facts it needs
  from a single `SELECT`: the role to attach, *and* the status that decides
  whether a flip is due.
- **Auth gate (`main.py`):** writes **only when `status == 'invited'`**. An
  already-active user's request is therefore **read-only** — no `UPDATE`, no
  `commit()` — which is the actual fix for #73's per-request-write contention.
  The flip stays best-effort (swallow + log): an invited user already admits and
  flips on a later request, so a transient lock never blocks the request or
  permanently loses the promotion.
- **UI (`_admin_members.html`):** the "Last sign-in" column header and the
  `last_seen_at` cell are removed.

This supersedes the last-seen aspects of ADR 0085 and the
`2026-06-14-iap-roles-admin-console-design` spec; those remain as the
historical record of the original design.

## Consequences

- The admin members table is now Member · Role · Status · (actions). Invited
  members still auto-activate on first sign-in; the only observable loss is the
  last-sign-in timestamp, which was the requested removal.
- **Steady-state browsing does zero DB writes on the auth path.** Measured: an
  active user over 5 requests → 0 commits; an invited user → exactly 1 commit on
  first sight, 0 thereafter. This resolves #73 (not just #72): the lock
  contention came from the per-request commit, which is now gone for all but the
  one-time flip. The #72 best-effort guard and its regression test survive.
- Tests: `test_active_user_browse_does_no_db_write` and
  `test_invited_user_is_activated_once_then_stays_read_only` are the #73
  regression guards; `test_activate_on_first_sight_flips_invited_to_active`
  asserts the flip *and* that `last_seen_at` is no longer a member field;
  `get_gate_state`'s admit semantics are covered by `test_upsert_gate_state_and_seed`.
- A future contributor who wants a last-seen / activity surface should add it
  as a deliberate, off-the-critical-path feature (e.g. a separate audit table
  written from a background job), not by reviving a per-request touch.
