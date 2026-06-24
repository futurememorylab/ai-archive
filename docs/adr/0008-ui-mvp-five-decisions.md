# 0008. UI MVP — five decisions

- **Date:** 2026-05-20
- **Status:** Accepted
- **Lifespan:** Feature

## Context

First UI deliverable. Backend was complete through PR 7; only HTTP/JSON surfaces existed. Spec at `docs/specs/2026-05-20-ui-mvp-design.md`.

**1. Defer Tailwind to ≥4 screens.** ADR 2026-05-18 nominated Tailwind standalone CLI, but two read-only screens don't justify a build step. Ship hand-crafted `static/app.css` (~280 lines) with CSS variables. Adopt Tailwind when Templates + Jobs + Archive land.

**2. HTML routes call `ctx.archive` directly.** The HTML layer reuses the same `ArchiveProvider` adapter as `/api/catdv/*`, in-process, without going through HTTP. The JSON API stays as a public surface for future external consumers; the HTML layer is parallel, not a client. Avoids a second JSON serialization and a network hop on every render.

**3. Native `<video controls>` for MVP playback.** Custom transport (J/K/L, ±1 frame, set in/out) is review-flow work; premature here. Native controls cover play/pause/scrub/volume/fullscreen for free. Add custom transport when the AI-review UI lands.

**4. View-model adapter (`backend/app/ui/view_models.py`) keeps templates logic-free.** Templates receive flat dicts (`clip_summary`, `clip_detail`). Reading `provider_data` shape in Jinja is a maintenance trap — provider-specific keys (`bigNotes`, `media.codec`, …) belong in one Python function, not in three template files. When the FS adapter lands, only `view_models.py` adapts.

**5. Dark theme only.** All colors flow through CSS variables (`--bg`, `--panel`, `--accent`, …). Light theme is a ~20-line later addition; the tokens are ready for it.
