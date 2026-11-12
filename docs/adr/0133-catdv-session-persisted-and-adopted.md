# ADR 0133 — Persist the CatDV session id and let a new instance adopt it

Date: 2026-11-12 · Status: accepted · Lifespan: Invariant (10) ·
Recorded after the fact for PR #134 (merged 2026-10-15) · Refines 0023,
0032, 0041, 0077

## Context

Invariant 10 assumed one process, one CatDV session: log in on boot, log
out on `SIGTERM`. On Cloud Run that breaks during an instance swap. The
outgoing instance often can't reach CatDV to `DELETE /session`, because the
incoming one has taken over the shared WireGuard tunnel. Its seat stays
held until CatDV's idle timeout. On 2026-10-15 three such failed logouts
filled both CatDV seats and locked everyone out.

PR #134 fixed this by storing the session id and reusing it. That changes
invariant 10 and keeps a session credential at rest, so it needed an ADR;
this records it (SDLC audit, 2026-11-12).

## Decision

- `CatdvClient` takes an optional `SessionStore`. In the app it is
  `repositories/app_meta.py::CatdvSessionStore`, one `app_meta` row
  (`catdv_session_id`).
- After a successful login the client saves its `JSESSIONID` there.
- Before logging in, a client that is not logged in tries to **adopt** the
  stored id: it sets the cookie and makes one cheap authenticated call
  (`GET /catdv/api/9/fields`). If CatDV accepts it, the process is logged in
  and has spent no new seat. If not, it clears the cookie and logs in
  normally.
- A clean logout (`DELETE /session` succeeded) clears the row. A failed
  logout leaves it, so the next process can adopt the still-held seat.

## Threat: a session credential at rest

`app_meta` lives in SQLite, which Litestream replicates to
`gs://catdv-annotator-db` (prod) and `.../staging` (staging). So a live
CatDV `JSESSIONID` sits in a GCS object, and in older Litestream
generations until retention removes them, and in any local `data/app.db`
of a developer who ran against CatDV.

Who could use it, and what they would need:

- **Read access to the replica bucket.** The bucket is private; the runtime
  service account has `storage.objectAdmin` on it (`deploy/README.md`), plus
  project owners. Those principals can already read the whole database and
  the `catdv-password` secret, so the session id gives them nothing new.
- **A network path to CatDV.** CatDV is reachable from the cloud only
  through the WireGuard tunnel, whose private key is in Secret Manager. A
  stolen session id is useless from the internet.
- **Lifetime.** The id is valid until a clean logout or CatDV's idle
  timeout, the same as the in-memory one.

## Acceptance and mitigation

Accepted: the stored id grants no access beyond what a reader of the
bucket or the project already has, and it is only usable from inside the
VPN. Mitigations in place:

- The row is cleared on every clean logout, so a stored id is normally
  either live and in use, or about to time out.
- Adoption only ever reuses the id; it never extends its life beyond what
  CatDV's own idle timeout allows.
- Staging and prod have separate replicas, so neither can adopt the
  other's session.

Not done (left open): encrypting the row at rest, or shortening Litestream
retention for it. Revisit if the replica bucket's readers ever widen
beyond the project's own principals, or if CatDV becomes reachable outside
the VPN.

## Consequences

- Invariant 10 now reads: one in-flight CatDV session **per deployment**,
  carried across instance swaps by adoption; graceful shutdown still logs
  out and clears it. `kill -9` still leaks the seat, but the next start
  adopts it instead of spending the second one.
- A script that logs in to CatDV itself (outside the app) gets no store and
  must still `DELETE /session` when done.
- Adding a second `CatdvClient` with the same store would make two
  processes share one session. Don't.

## Alternatives considered

1. **Rely on the service-wide one-instance cap alone (ADR 0132).** Prevents
   overlap but not the lost-tunnel logout on a swap. Not enough by itself.
2. **Keep the id in Secret Manager.** A secret write on every login, a new
   IAM grant for the runtime account to write secrets, and no better
   protection than the private bucket. Rejected.
3. **Log out the old session from the new instance by id.** Possible, but
   it spends a login to free a seat; adopting spends nothing.
