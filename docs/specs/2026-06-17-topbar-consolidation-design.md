# Topbar consolidation + environment-aware session control

**Date:** 2026-06-17
**Status:** Approved (design)

## Problem

The topbar right side stacks **7** elements: batch-progress indicator, sync chip,
connection chip, Shut down button, `DEV · host` env pill, the signed-in email, and
a bordered **Log out** button. It's cluttered, and the Log out button is the odd
one out — a floating `.btn` after a bare text email. Two of the controls are also
shown in the wrong environment:

- **Log out** (clears the IAP cookie) is meaningless on a **local** dev instance —
  there's no real session to end.
- **Shut down** (stop the server, release the CatDV seat) is meaningless on a
  **cloud** instance — Cloud Run owns the lifecycle and the route already 403s.

Separately, the operator asked whether **batches awaiting review** should surface
in the topbar. They should — but NOT inside the sync chip: the sync chip tracks
changes flowing **out** to CatDV (write-backs), while "awaiting review" is AI
drafts coming **in** for the operator. Two opposite ends of the pipeline; two
indicators.

## Design

### Topbar right side (left → right)

1. **Batch progress** — `jobsIndicator`, unchanged. Transient (only while a batch runs).
2. **"👁 N to review"** — NEW. Drafts awaiting the operator's review. Amber
   (`changed`/accent) pill. Shown only when `N > 0`. Links to the review queue
   (`/?anno=for_review`). Distinct from the sync chip.
3. **Sync chip** (`↑ N` / `✓ Synced`) — unchanged (ADR 0095).
4. **Connection chip** (`● Online ▾`) — unchanged.
5. **User menu** (`AB ▾`) — NEW. A single popover that replaces the floating
   email + Log out, and absorbs the env badge and the env-appropriate
   session-end action.

Dropped from the bar as standalone elements: the `DEV · host` pill, the Shut down
button, the email text, and the Log out button — all consolidated into the user
menu.

### User menu (env-aware)

Built with the shared `ui.menu` / `ui.menu_item` + `popover()` (per
`design-language.md` §8 — no new `*-menu` vocabulary). Trigger: a compact pill
showing the user's initials (cloud) or a generic glyph (local). Contents:

- **Header:** signed-in email (real IAP email on cloud; `dev@localhost` on local).
- **Env line:** `DEV · localhost:8765` / `PROD · <host>` (the old env-pill content,
  demoted into the menu).
- **Session-end action — exactly one, by environment:**
  - **Cloud / IAP** (`settings.auth_backend == "iap"`): **Log out**
    (`/?gcp-iap-mode=CLEAR_LOGIN_COOKIE`).
  - **Local / dev** (not prod): **Shut down & release seat**
    (`POST /api/connection/shutdown`). Under `settings.dev_reload` it renders
    disabled with the existing "stop with Ctrl-C in the terminal" tooltip.

This keeps the two mutually-exclusive controls each in their right environment and
gives both environments a populated, consistent menu.

### "To review" count

The count is rendered **inline** on full-page loads (no flicker), reusing the
existing `_topbar_sync_context` Jinja context processor (added in ADR 0095) which
already does one synchronous, WAL-safe read for the sync chip. Add one more cheap
query alongside it:

```sql
SELECT COUNT(DISTINCT ri.catdv_clip_id)
FROM review_items ri
JOIN annotations a ON a.id = ri.annotation_id
WHERE ri.applied_at IS NULL
```

(job-based drafts not yet applied — matches the `/batches` "awaiting review"
metric). Injected as `review_count`. The chip is a plain link (no poll needed; it
refreshes on every navigation, which is when review state changes for the operator
anyway). The processor stays bulletproof (any error → `{}`).

### Environment detection

All from `settings` (already on `request.app.state.core_ctx.settings`):
`auth_backend` ("iap" vs "dev"), `app_env` ("prod" vs other), `dev_reload`.
Mirrors the existing branching in `_topbar_pills.html`.

## Files touched

- `templates/pages/layout.html` — move email/Log out out of the header into the
  new user menu include; keep brand + crumb + pills.
- `templates/pages/_topbar_pills.html` — add the "to review" chip; drop the
  standalone Shut down + env pill (moved into the user menu); add the user-menu include.
- `templates/pages/_user_menu.html` — NEW (the env-aware popover).
- `templates/_to_review_chip.html` (or inline) — NEW "👁 N to review" chip.
- `routes/pages/templates.py` — extend `_topbar_sync_context` with `review_count`.
- `static/app.css` — user-menu trigger pill (reuse `.btn`/pill tokens); to-review
  chip styling (reuse `.pill`/accent); remove now-dead `.topbar-logout` /
  `.topbar-user` / standalone `.env-pill`/`.shutdown-btn` topbar rules as needed.

## Reuse / non-duplication

- User menu = `ui.menu` + `popover()` (NOT a new dropdown). The connection chip's
  `popover()` pattern is the precedent.
- "To review" chip = `ui.status_pill` styling vocabulary, not a bespoke component.
- Shut down reuses the existing `POST /api/connection/shutdown` + `dev_reload`
  disabled state — just relocated into the menu.
- Count reuses the `_topbar_sync_context` processor — no new poll, no new endpoint.

## Out of scope

- No change to the sync chip, connection chip, or batch-progress behaviour.
- No new "review" backend route — the chip links to the existing
  `/?anno=for_review` clips view.

## Manual acceptance flows

1. **Declutter (local dev).** Load any page on the local instance. The topbar
   right side shows: (batch progress only if a batch is running) → sync chip →
   connection chip → a user-menu pill. There is **no** standalone Log out button,
   no standalone Shut down button, no `DEV · host` pill, no bare email.
2. **User menu — local.** Click the user-menu pill. The popover shows the email,
   a `DEV · localhost:<port>` line, and a **Shut down & release seat** action (or,
   under `dev_reload`, a disabled item with the Ctrl-C tooltip). There is **no**
   Log out item. Clicking Shut down behaves exactly as the old button did.
3. **User menu — cloud/IAP.** With `auth_backend=iap` (staging/prod), the same
   pill's popover shows the real Google email, a `PROD · <host>` line, and a
   **Log out** item (and **no** Shut down item). Log out clears the IAP cookie and
   forces re-auth, as before.
4. **To-review chip appears and links.** With at least one un-applied draft
   (a completed batch not yet reviewed), the topbar shows an amber **"👁 N to
   review"** chip with the correct N. Clicking it opens the review queue
   (`/?anno=for_review`). Apply/clear all drafts → reload → the chip is gone.
5. **To-review vs sync are distinct.** Approve & apply a clip's drafts while CatDV
   is offline: the **sync** chip shows `↑ N` (write-backs queued) AND the
   **to-review** count drops by one — the two move independently, proving they
   track opposite ends.
6. **No flicker / consistent shape.** On navigation the user-menu pill and the
   to-review chip paint immediately with no placeholder flash, and both read as
   rounded pills consistent with the connection chip.
