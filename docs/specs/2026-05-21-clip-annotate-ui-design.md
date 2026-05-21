# Clip Annotate UI — Spec

**Date:** 2026-05-21
**Scope:** Add a clip-detail UI entry point that runs the existing Gemini annotation pipeline against the current clip with a user-chosen production prompt, then renders the result as a "Draft" view of the right-aside metadata panels, toggleable against the existing "Published" view from CatDV. Backend pipeline is reused as-is.

## Background

The backend annotation pipeline is already built end-to-end:

- `backend/app/services/gcs.py` + `archive/ai_stores/{gcs,gemini_files}/` — GCS / Gemini Files adapters behind the `AIInputStore` port.
- `backend/app/services/gemini.py` — Vertex Gemini call with quota / safety / permission classification and quota retry.
- `backend/app/services/annotator.py` (`run_job`) — orchestrator: resolve proxy → ensure uploaded → call Gemini → store annotation → expand structured output into `review_items` via `target_map.expand()`.
- `backend/app/routes/jobs.py` — `POST /api/jobs` accepting `{prompt_version_id, clip_ids, auto_start}`. Works for a single clip (`clip_ids: [id]`).
- `backend/app/routes/events.py` — `GET /api/jobs/{job_id}/events` SSE stream with `resolving → uploading → prompting → review_ready` (or `error`).
- `backend/app/repositories/annotations.py` — persists `raw_response`, `structured_output`, `clip_snapshot`, `prompt_used`, `model`; `list_by_clip` returns DESC.
- `backend/app/repositories/review_items.py` — proposed `marker` / `field` / `note` items with `decision` lifecycle.
- `backend/app/repositories/prompts.py` — prompt versions with `state ∈ {draft, production, archived}`; at most one production version per prompt.
- `scripts/setup-gcp.sh` — APIs, bucket, service account, IAM, secrets.

What is missing is a UI entry point. The clip detail page (`backend/app/templates/pages/clip_detail.html`) shows Markers / Fields / Notes from the live CatDV state in the right aside, but there is no way from that page to fire a prompt against the clip and view the result. This spec adds that surface with the smallest sensible footprint.

## Goals

1. Surface a one-click "Annotate" affordance on `clip_detail.html` that runs the existing job pipeline against the open clip with a chosen production prompt version.
2. Render Gemini's structured output as a **Draft** version of the right-aside metadata, using the *same* visual treatment as the existing **Published** view (markers as marker cards, fields as identifier→value rows, notes as a paragraph). A segmented Published↔Draft toggle scopes the whole aside.
3. Show live progress during the run via the existing SSE stream — no polling spinner.
4. Keep all existing backend services and infrastructure scripts unchanged. New code is a thin glue layer plus templates / JS for the clip detail page.

## Non-goals

- Per-item accept / reject; updating `review_items.decision` from the UI.
- Pushing accepted items back to CatDV via `write_queue` / `sync_engine`.
- Annotation history picker (anything other than the latest annotation for the clip).
- Side-by-side diff of Published vs Draft.
- A cancel button for in-flight jobs.
- Modifying `services/gcs.py`, `services/gemini.py`, `services/annotator.py`, `services/target_map.py`, `archive/ai_stores/*`, `scripts/setup-gcp.sh`, or `.env.example`.
- A standalone "raw response" tab — the structured output is what the user actually wants to read; raw lives in the DB for debugging only.
- Overhauling `DEPLOY.md` or rewriting `setup-gcp.sh`. README gets a short how-to and a quick correctness pass on existing docs.

## Architecture

```
┌────────────────────── clip_detail.html ──────────────────────┐
│  [Cache video] / [Evict local]   [Annotate ▾]   tc readout   │
│                                       │                       │
│  ┌── player ──┐    ┌── right aside ───┴────────────────┐     │
│  │            │    │  ◀ Published │ Draft ▶            │     │
│  │            │    │  [ Markers ] [ Fields ] [ Notes ]  │     │
│  │            │    │  ┌──────────────────────────────┐  │     │
│  │            │    │  │ (panel renders from EITHER   │  │     │
│  │            │    │  │  clip.* or latest_draft.*)   │  │     │
│  │            │    │  └──────────────────────────────┘  │     │
│  └────────────┘    └────────────────────────────────────┘     │
└──────────────────────────────────────────────────────────────┘
```

**Reuse, no new pipeline.** The annotation pipeline (GCS upload → Vertex Gemini → annotations + review_items) is unchanged. The UI submits a job-of-one via the existing `POST /api/jobs`, subscribes to the existing SSE topic `job:{id}`, and renders the latest annotation's `review_items` through a new view-model that produces the same shape the Published panels already consume.

### New / changed modules

| Where | What | Why |
|---|---|---|
| `backend/app/models/draft_view.py` (new) | `DraftView`, `MarkerView`, `FieldView` pydantic models. | Explicit contract between view-model and templates; isolates UI from `review_items` raw shape. |
| `backend/app/services/draft_view.py` (new) | `build_draft_view(annotation, review_items) → DraftView`. Applies the existing `view_models._fix` mojibake cleanup. | One place owns the "annotation + review_items → tab data" mapping. Both Published and Draft panels render via one template path regardless of source. |
| `backend/app/routes/pages.py` (changed) | Extend the `clip_detail` view-model: load latest annotation + its `review_items` via existing repos; build `DraftView`; pass `draft` alongside `clip` to the template. | One round trip on initial page load; first paint of Draft is server-rendered. |
| `backend/app/routes/pages.py` (changed) | Add `GET /clips/{catdv_clip_id}/draft` returning an HTMX partial (the Draft aside body only). | Used after a job completes to swap in fresh Draft markup without a full page reload. |
| `backend/app/templates/pages/clip_detail.html` (changed) | Split right aside into `_anno_panels.html` (a single panel renderer parametrised by `markers / fields / notes`) plus a scope toggle. | Visual parity comes "for free" because both Published and Draft render through the same partial. |
| `backend/app/templates/pages/_anno_panels.html` (new) | Markers / Fields / Notes panel set, parametrised by data; renders inside either the Published or Draft container. | DRY. |
| `backend/app/templates/pages/_annotate_dropdown.html` (new) | Button + dropdown listing prompts that have a `production` version. | Localised, reusable from the clip header. |
| `backend/app/static/clipAnnotate.js` (new, ~80 lines) | Alpine component: opens dropdown, calls `POST /api/jobs`, opens `EventSource('/api/jobs/{id}/events')`, drives a status line, swaps Draft aside via HTMX on `review_ready`. | Single file owns the dropdown + run lifecycle. |
| `README.md` (changed) | "Annotate a clip from the UI" how-to section. Quick pass over the rest for drift. | Per scope decision. |
| `docs/specs/2026-05-21-clip-annotate-ui-design.md` (this file) | Spec. | Repo convention. |
| `docs/plans/2026-05-21-clip-annotate-ui.md` (next skill) | Implementation plan. | Repo convention. |

### Untouched modules

`backend/app/services/{gcs,gemini,annotator,target_map}.py`, `backend/app/archive/ai_stores/*`, `backend/app/repositories/{annotations,review_items,jobs,prompts}.py`, `backend/app/routes/jobs.py` (`POST` + existing GETs), `backend/app/routes/events.py`, `scripts/setup-gcp.sh`, `.env.example`. The whole backend infra was built for this; we plug into it.

## Data flow

### Initial page load

1. `GET /clips/{id}` (existing route) — `routes/pages.py` builds the clip view-model.
2. New: `annotations_repo.list_by_clip(db, clip_id)` — take `[0]` if any (DESC order, latest first); else `None`.
3. New: if an annotation exists, `review_items_repo.list_by_clip(db, clip_id, annotation_id=...)` (small extension if the existing signature does not filter by annotation; otherwise filter in Python).
4. New: `build_draft_view(annotation, review_items)` produces `DraftView` (with `has_draft: false` when there is no annotation).
5. Template renders `clip` + `draft` together. Published↔Draft toggle defaults to Published.

### Annotate flow

```
User clicks [Annotate ▾]
   │
   ▼
clipAnnotate.js opens dropdown
   │   GET /api/prompts?archived=0   (session-cached on first open)
   │   filter client-side to entries with current_production_version_id != null
   ▼
User picks a prompt
   │   POST /api/jobs { prompt_version_id, clip_ids: [clip.id], auto_start: true }
   │   ← { id: job_id }
   │   toggle auto-switches to Draft
   │   Draft aside replaces panels with a single status line
   ▼
EventSource('/api/jobs/{job_id}/events')
   │   {status: "resolving"}            → "Locating proxy…"
   │   {status: "uploading"}            → "Uploading proxy to GCS…"
   │   {status: "prompting"}            → "Calling Gemini…"
   │   {status: "review_ready", annotation_id} → swap Draft
   │   {status: "error", error}         → red status line, do not swap
   ▼
On review_ready: hx-get /clips/{id}/draft (HTMX partial swap)
   status line clears, tab counts update.
```

### `DraftView` shape

```python
class MarkerView(BaseModel):
    name: str
    category: str | None
    in_secs: float
    out_secs: float | None
    description: str | None

class FieldView(BaseModel):
    identifier: str            # e.g. "pragafilm.rok.natočení"
    value: Any                 # str | list[str] | … — same shape as published

class DraftView(BaseModel):
    has_draft: bool
    annotation_id: int | None
    created_at: str | None
    prompt_name: str | None
    version_num: int | None
    model: str | None
    markers: list[MarkerView]
    fields: list[FieldView]
    notes: str | None
```

### `build_draft_view` mapping (from `review_items`)

| Review item `kind` | Mapping |
|---|---|
| `marker` | `MarkerView` from `proposed_value` (already marker-shaped from `target_map.expand`). |
| `field` | `FieldView(identifier=target_identifier, value=proposed_value)`. |
| `note` | Concatenate `proposed_value` strings into `notes`. (`target_map` convention is one note key per prompt; if multiple appear, join with blank lines. To be confirmed against an actual prompt during implementation.) |

`edited_value` is ignored. Mojibake cleanup from `view_models._fix` is applied to marker names / descriptions / note text on read; raw data is untouched.

## States

| State | Shown in Draft view |
|---|---|
| No annotation for this clip | Tab counts `0 / 0 / —`. Empty-state body: "No draft yet. Click **Annotate** to generate one." Toggle is still enabled. |
| Running | Tabs hidden; single status line ("Calling Gemini…"). Annotate button is disabled and shows the running prompt name. Player and Published tabs stay interactive. |
| Run completed | Tabs render with proposals. Header chip in Draft view: `Prompt "X" • v3 • gemini-2.5-pro • 14:22:08`. |
| Run failed | Status line becomes red: `Failed: <error message verbatim>`. The previous draft (if any) is preserved — we only swap on `review_ready`. Annotate button re-enabled. |
| Run cancelled | Status line neutral: `Cancelled.` No cancel button in v1. |
| No production prompts exist | Dropdown body: "No production prompts. Open Prompts to create one." with a link to `/prompts`. The Annotate button stays visible. |

## Error handling (specifics)

- **Proxy unavailable** (`ProxyNotFound` or other I/O error) → bubbles through job-item error path → SSE `{status: "error", error}`. Status line renders the error string verbatim.
- **`GeminiQuotaError`** retries inside `annotate_with_retry` (existing). User sees `"Calling Gemini…"` for longer; no UI-level retry.
- **`GeminiSafetyError`** / **`GeminiPermissionError`** → surface as the same job-item error; the status line text distinguishes them.
- **SSE drops mid-run** (VPN flap, server restart): `EventSource` auto-reconnects. If the stream returns 4xx/5xx, fall back to a 2-second poll of `GET /api/jobs/{id}` until a terminal status. ~10 lines in `clipAnnotate.js`.
- **User navigates away and back** while a job is running: not supported in v1. The annotation still lands in the DB and is visible on next page load. Adding "resume in-flight SSE" is a small future addition once needed.

## Testing

| Layer | Test |
|---|---|
| Unit | `build_draft_view` — fixture `review_items` covering all three kinds → expected `DraftView`. |
| Unit | Mojibake cleanup applied to marker names / descriptions / notes. |
| Unit | "No production prompts" client-side filter — given mixed prompt states, only those with a production version surface. |
| Route | `GET /clips/{id}/draft` returns 200 with empty-draft HTML when no annotation exists. |
| Route | `GET /clips/{id}/draft` returns populated HTML when an annotation + review_items are seeded. |
| Route | `GET /clips/{id}/draft` returns 404 when the clip does not exist (matches existing `pages.py`). |
| Integration | One end-to-end test using existing fake Gemini + fake AI store: run a job-of-one, then assert the rendered clip page Draft contains the expected markers / fields. |
| Manual | Local run against a stub Gemini, exercise: empty-draft state, dropdown open, run, SSE status transitions, Published↔Draft toggle, error path. |

## Risks and mitigations

- **CatDV session seat leak.** Out of band — covered by existing CatDV session discipline in `CLAUDE.md`. The annotation flow does not open new CatDV sessions; it reuses the app's existing one.
- **Two simultaneous Annotate clicks from the same tab.** The button is disabled while a job is in flight (Alpine `running` flag). Cross-tab is unsupported in v1 — second click in another tab would start a parallel job and produce a second annotation; the latest still wins on refresh.
- **A draft going stale silently.** The Draft view's header chip shows `created_at` so the user can see how old the proposal is relative to the clip's current Published state.
- **Mismatch between `target_map.expand` output shape and `DraftView`'s marker / field shape.** Resolved by adding the unit test in the testing table and converging shapes in `build_draft_view`.

## Open questions (resolved before sign-off)

None — all answered in the brainstorming pass:

- Picker shows production versions only.
- Latest annotation only; no history picker.
- Inline dropdown next to the button.
- SSE-driven status line (with poll fallback).
- Annotate button always visible; chains caching automatically via `proxy_resolver`.
- Segmented Published↔Draft toggle above the tabs row.
- README how-to + small docs refresh.

## Out of scope, called out

Per-item review actions, push to CatDV, history picker, diff view, cancel button, backend pipeline edits, `setup-gcp.sh` / `.env.example` / `DEPLOY.md` edits, and a raw-response tab. Each of these is a clear future addition that does not need to land in this spec.
