# 0012. Clip Annotate UI: Draft view, scope toggle, in-page annotate flow

- **Date:** 2026-05-21
- **Status:** Accepted

## Context

Backend annotation pipeline (GCS → Gemini → annotations +
review_items) was already complete, but the clip detail page had no
entry point to fire a prompt against the open clip or read the result.
Spec at `docs/specs/2026-05-21-clip-annotate-ui-design.md`; plan and
17-task execution at `docs/plans/2026-05-21-clip-annotate-ui.md`.

**Alternatives & choices.**

- *View-model shape.* Plan originally suggested pydantic `DraftView` /
  `MarkerView` / `FieldView` models. Codebase convention is plain
  `dict[str, Any]` view-models (see `backend/app/ui/view_models.py`).
  **Chose plain dicts** in `backend/app/services/draft_view.py` to match.
- *Where prompt-name / version-num come from.* Could fetch inside
  `build_draft_view` (introduces repo dependency) or take as
  caller-supplied kwargs. **Chose caller-supplied** keyword-only kwargs;
  the route helper `_build_draft_for_clip` does the prompt lookup, the
  view-model stays pure.
- *Production-prompt filter.* `_prompt_envelope` was already exposing
  `current_production_version_id`. The dropdown calls `GET /api/prompts`
  (list endpoint, returns bare prompt rows — not envelopes). **Extended
  `list_prompts` to enrich each row** with both `current_production_version_id`
  and `current_production_version_num`, and added `_version_num` to the
  envelope for consistency. The dropdown then filters client-side.
- *Sharing run state between dropdown and aside.* Two siblings under the
  `.detail` wrapper. **Lifted `scope, tab, running, runningPromptName,
  runStatus, runError, jobId` onto the root** via
  `x-data='Object.assign(player(...), { ... })'` so both children
  read/write through `$root.*`. The dropdown's Alpine factory keeps
  only its own UI state (`open`, `prompts`, `loading`, `error`);
  the `pick(prompt, root)` method takes `$root` and mutates it.
- *Partial route's clip dependency.* `_anno_panels.html` uses `clip.fps`
  for SMPTE timecodes. The new `GET /clips/{id}/draft` partial route
  doesn't have a populated `clip` to pass. **Added `panels.fps` as a
  partial-local override**: `{{ smpte(m.in_secs, panels.fps or clip.fps) }}`.
  Published path leaves `panels.fps` unset (falls through to `clip.fps`),
  Draft path passes `clip.fps or 25.0` explicitly.
- *Empty-state marker.* The Draft empty body carries
  `data-draft-empty="true"` on its own element; the integration test
  asserts presence/absence of that string. Avoids parsing rendered
  HTML structure.
- *Annotation `created_at` round-trip.* The DB column already existed
  (written at INSERT) but the model and SELECTs didn't read it back.
  **Made it additive** — added `created_at: str | None = None` to
  `Annotation`, added the column to both SELECTs, and the `_row`
  mapper reads it with a `len(row) > 10` guard so older callers
  pulling fewer columns wouldn't break.
- *SSE error fallback.* When `EventSource.onerror` fires we close the
  stream and switch to polling `GET /api/jobs/{id}` every 2 seconds
  until terminal status. Loop is guarded by `root.running` so a
  successful SSE swap (which sets `running=false`) collapses the
  polling loop on the next tick. **Trade-off accepted:** the loop
  doesn't cap retry attempts, so a persistently-500 job endpoint would
  loop forever — bounded by `root.running` being flipped elsewhere.
- *Test approach.* `tests/integration/conftest.py` only provides a `db`
  fixture; the plan's `httpx.AsyncClient` + `client/ctx/seeded_clip_101`
  fixtures don't exist. **Followed the existing
  `tests/integration/test_routes_pages.py` pattern**: sync `TestClient`
  via a per-file `_make_client(monkeypatch, tmp_path)` helper, and an
  `asyncio.new_event_loop()` driver for repo seeding against the
  running app's `ctx.db`. The end-to-end test in
  `tests/integration/test_annotate_ui_e2e.py` imports the existing
  `FakeArchive / FakeResolver / FakeAIStore` from
  `test_annotator_worker.py` rather than redefining them.

## Consequences

The constraint shaping every call was "do not touch the
backend pipeline." The result is a thin glue layer: one pure-function
view-model, two new template partials plus a small refactor of the
existing aside, an HTMX partial route, and ~120 lines of Alpine JS.
Visual parity between Published and Draft is automatic because both
render through the same `_anno_panels.html`.

**Out of scope (deliberately, called out in spec).** Per-item
accept/reject, push to CatDV via `write_queue`, annotation history
picker, side-by-side diff, cancel button, raw-response tab,
`scripts/setup-gcp.sh` / `.env.example` / `DEPLOY.md` edits. Each is a
clear follow-up that can land on top of this surface without
redesigning what's here.
