# 0017. Offline mode: keep Annotate available when proxy is cached; marker nav follows active scope

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Feature

## Context

Two related clip-detail bugs surfaced after the
offline-mode rollout (PRs leading up to `b21c30f`): (1) the Annotate
dropdown disappeared in offline / degraded mode even when the clip's
proxy was already cached locally and Gemini was reachable; (2) the
prev/next-marker transport buttons always navigated the published
marker list, even when the user had switched the right-aside scope
to Draft.

**Alternatives & choices.**

1. *Annotate visibility offline.* The offline-fallback plan
   (`docs/plans/2026-05-22-offline-fallback.md` step 6) lumped
   Annotate together with "Cache locally" and "Refresh from CatDV"
   under a single `{% if mode == "online" %}` gate. We considered
   leaving it as-is (conservative) vs. gating on the real
   precondition — proxy cached locally. We chose the latter:
   `{% if mode == "online" or (clip.cache and clip.cache.media_local.present) %}`
   in `clip_detail.html`.

2. *Scope-aware prev/next.* Player only knew about
   `clip.markers` (published). Options: (a) drive a separate Alpine
   component for draft markers, (b) thread draft markers through the
   existing player as a second list and pick by `scope`. Chose (b):
   `player()` now takes a fourth `draftMarkers` arg, exposes an
   `activeMarkers()` method that returns `scope === "draft" ? draftMarkers : markers`,
   and the transport buttons / arrow-key handler / `_jumpMarker`
   all read through it. The `:disabled` binding in the template
   becomes `:disabled="!activeMarkers().length"`.

## Consequences

For (1): the design spec's own acceptance criteria
(`docs/specs/2026-05-19-archive-abstraction-and-offline-mode-design.md`
§"Acceptance") lists "Go offline → annotate (cached) → reconnect →
sync" as a target. The annotate pipeline (`services/annotator.py`)
resolves the proxy through `LocalCacheOnlyResolver`, uploads to
Gemini/GCS directly, reads clip metadata from the stale-cache adapter,
and stores results in local SQLite. Apply-to-CatDV is a separate
later step that already queues through `pending_operations`. So
hiding Annotate when offline was strictly more restrictive than the
spec required and broke the documented offline workflow.

For (2): keeping a single `markers` array meant the user could see
draft marker ranges on the timeline but the "next/prev marker"
controls would silently jump to the wrong list. A method (not a
getter) was chosen because the player object is merged into the
final Alpine scope via `Object.assign(player(...), clipAnnotate(...), {scope, tab})`
— a plain function-valued property survives `Object.assign` and
Alpine's reactive wrapping more predictably than an accessor
descriptor on the merged target.

---
