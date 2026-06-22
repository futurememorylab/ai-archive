# Annotated Playwright walkthrough tests

**Date:** 2026-06-22
**Status:** Draft — pending review (revised after grounding investigation)

## Problem

Manual click-through testing eats time on every change. We want a Playwright
harness that drives the real app end-to-end, **records an annotated video of the
clickstream**, and lets a tester *review the video* instead of clicking through
by hand. Because the videos carry the use-case title and a visible step counter,
the same artifacts double as **user documentation**.

Constraints that shape the design:

- **No CatDV license seat may be touched.** The app's CatDV Enterprise license is
  effectively single-seat for this app (see `CLAUDE.md`). Tests must never hit the
  live CatDV server.
- **No GCS, no Gemini, fully offline.** Runs must be deterministic and network-free.
- **Python-only.** ADR 0001 keeps this repo free of a Node toolchain; the test
  tooling honors that.

## Goals

1. Drive the real FastAPI app through a real browser, exercising real routes,
   real DB writes, and real video streaming — with no CatDV/GCS/Gemini.
2. Produce a per-scenario **annotated `.webm`**: a use-case chapter card, an
   on-screen **"Step N — <label>"** counter, and Playwright's automatic
   action highlighting.
3. Let Claude author new scenarios from a plain-English flow description.
4. Give a reviewer a zero-infra **local gallery** (an `index.html`) to watch the
   videos; the same gallery serves as user documentation.
5. Run the *same* scenarios in a fast headless `--assert` mode for regression,
   so the doc video and the pass/fail test never drift.
6. Expose a project **`/e2e` skill** so the suite is one command to run, record,
   and review — for both a human and Claude — encapsulating the install check,
   the run modes, and opening the gallery.

## Non-goals (YAGNI for v1)

- GCS upload / shareable cloud links for videos.
- CI video artifacts and trace-viewer integration.
- Studio, Prompt-library, and Admin/cache flows (clip-detail only for MVP).
- Narrated audio.
- Exercising the **AI "Generate" button** on camera (would need a fake-AI wiring;
  we seed the draft instead).
- A genuine upstream **archive writeback** (queue → SyncEngine → sidecar). The
  Publish step is exercised as far as the **durable write queue** (real, observable
  in the DB + status pill); the SyncEngine round-trip is out of scope (see
  Decisions → Publish fidelity).

## Background — what already exists (grounded 2026-06-22)

This section was rewritten after a code investigation overturned the first draft's
assumption that the app could be pointed at a filesystem archive over the wire.

- **The web UI is numeric-clip-id-only.** `backend/app/ui/view_models.py:63` and
  `:130` compute `clip_id = int(clip.key[1])`, and `routes/pages/clips.py`'s
  `clip_detail_page(clip_id: int)` calls `ctx.archive.get_clip(str(clip_id))`.
  Every clip the UI renders must have an **integer** provider key.
- **The filesystem provider cannot drive the web UI.** `ARCHIVE_PROVIDER=fs`
  produces **path-string** keys like `("fs", "archive_30s/clip001")`
  (`archive/providers/fs/adapter.py::_clip_id_for_media`). `int("archive_30s/clip001")`
  raises — so the fs provider is only used by the *write-stack* tests, where
  `catdv_clip_id=0` is explicitly "ignored on FS path" (`test_fs_e2e.py`). It is
  **not** a vehicle for rendering the clips list or clip-detail page.
- **The page tests inject a fake archive in-process.** `test_clip_detail_draft.py`
  builds a `FakeArchive` returning a `CanonicalClip` with `key=("catdv", "101")`
  and installs it via `tests/_helpers/live_ctx.py::install_live_ctx(app,
  archive=…)`. `install_live_ctx` wraps the already-built `CoreCtx` in a `LiveCtx`,
  filling unspecified live services with `MagicMock`. This is the only supported
  way to render live routes with fakes — there is **no env switch** for it.
- **Proxy video is a `FileResponse`.** `routes/media.py` serves `/api/media/{id}`
  as a `FileResponse`/`StreamingResponse` from a real file path (via the proxy
  resolver). To make the player actually play, the injected resolver must return a
  real on-disk video for the clip id.
- **Draft vs published.**
  - *Published* annotation on the clip-detail page comes from the **`CanonicalClip`**
    the archive returns (its `markers`/`fields`/`notes`) — so we control it by what
    `FakeArchive.get_clip` returns.
  - *Draft* lives in the app **DB** (`annotations` + `review_items`), surfaced by
    `clips.py::_build_draft_for_clip` → `services/draft_view.py::build_draft_view`.
    The fs/archive providers do not create drafts — we seed them directly.
- **Publish = enqueue, not write-through.** The "Accept & apply" / publish path is
  `POST /clips/{id}/apply` (`routes/review.py::apply_clip` →
  `_resolve_and_enqueue_clip` → `ctx.write_queue.enqueue_apply_for_clip`). It marks
  items accepted, creates a new `clip_versions` row, and enqueues
  `pending_operations` rows; the actual upstream write is the SyncEngine draining
  the queue later. With an injected `MagicMock` SyncEngine, the **enqueue is real
  and observable** (DB rows + the status pill flips to "publishing") while no
  network write happens. That is exactly the fidelity we want for MVP.
- **Dev auth bypass.** `APP_ENV=dev` admits the operator as admin with no auth
  gate (`backend/app/main.py::_auth_gate`).
- **App boot.** `backend.app.main:app`; the lifespan builds `CoreCtx` from
  settings/env. Offline boot (`CATDV_USERNAME=""`) leaves `app.state.live_ctx` as
  None until we install one. Test env defaults live in `tests/conftest.py`.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Toolchain | **Python Playwright** (`playwright>=1.59`) | Honors ADR 0001; reuses seeding code; v1.59 screencast annotation API is in the Python binding. |
| App under test | **In-process `uvicorn.Server` in a background thread** + `install_live_ctx` | Playwright needs a real socket; injection (not env) is the only way to give the UI a numeric-keyed archive. Reuses the exact fake-injection path the page tests use. |
| Archive | **`FakeArchive` returning a numeric-keyed `CanonicalClip`** + real proxy file | The web UI requires `int(clip.key[1])`; the fs provider can't satisfy it. We control published content via the returned clip. |
| Scenario format | **Python module + `step()` helper** | Full power of code; step labels auto-drive annotations and double as doc narration; easy for Claude to write. |
| Test data | **Injected fake clip + seeded DB draft + a real short video** | One clip carries both published (clip fields) and draft (DB) state. |
| AI generate step | **Seed the draft; no Gemini on camera (MVP)** | Avoids any AI wiring; deterministic. |
| Publish fidelity | **Exercise to the write queue; verify DB + status pill (no SyncEngine)** | `apply_clip` enqueues real `clip_versions` + `pending_operations` rows and flips the pill; the SyncEngine round-trip is out of scope for MVP. |
| Video access | **Local gallery (`index.html`)** | Zero infra; doubles as user docs; cloud upload deferred. |
| Run modes | **`--assert` (headless, no video) and `--record` (headed, annotated)** | Best practice: don't record everything; same scenarios back both so they can't drift. |
| Entry point | **`/e2e` project skill** wrapping `tests/walkthrough/run.py` | One command to run/record/review; encodes the install check + gallery-open. |

## Architecture

```
┌─ in-process test app ─┐   ┌─ scenario (.py) ────┐   ┌─ annotate harness ──┐   ┌─ gallery ─┐
│ uvicorn.Server thread │ → │ TITLE, DESCRIPTION  │ → │ screencast.start    │ → │ index.html│
│  on 127.0.0.1:8766    │   │ run(wt):            │   │ show_chapter(TITLE) │   │  + *.webm │
│ install_live_ctx(     │   │   wt.step("…", act) │   │ show_actions(...)   │   │  opens in │
│   archive=FakeArchive,│   │   wt.step("…", act) │   │ step overlay (HTML) │   │  browser  │
│   proxy_resolver=…)   │   │                     │   │ → <scenario>.webm   │   │           │
│ seeded DB draft       │   └─────────────────────┘   └─────────────────────┘   └───────────┘
│ APP_ENV=dev (no auth) │
└───────────────────────┘
```

Everything lives under a new `tests/walkthrough/` directory, isolated from the
existing `tests/unit` and `tests/integration` suites.

### 1. Test-app server — `tests/walkthrough/app_server.py`

Because Playwright needs a real socket *and* the UI needs an injected
numeric-keyed archive, we run the app **in-process**:

1. Set env (`APP_ENV=dev`, `CATDV_USERNAME=""` so external init is skipped,
   `DATA_DIR=<tmp>`, `BIND_PORT=8766`, plus the `tests/conftest.py` offline
   defaults).
2. Import & reload `backend.app.main`; the lifespan builds `CoreCtx` (runs
   migrations against the tmp DB).
3. Start `uvicorn.Server` in a **daemon thread** bound to `127.0.0.1:8766`; poll
   `GET /` until ready (bounded; fail fast).
4. Seed test data (§4) and `install_live_ctx(app, archive=<FakeArchive>,
   proxy_resolver=<LocalFileResolver>, thumbnail_service=<stub>)`.
5. Yield the base URL; on teardown signal the server to exit and join the thread.

No CatDV session is ever opened (no `catdv`/live external init), so there is **no
seat to leak** — and nothing touches port 8765.

### 2. Fakes — `tests/walkthrough/fakes.py`

Small, walkthrough-local doubles (mirroring `test_clip_detail_draft.py`):

- `FakeArchive` — holds one `CanonicalClip` with `key=("catdv", "101")`,
  `name="archive_30s"`, `duration_secs≈8`, `fps=25`, a published field
  (`pragafilm.dekáda.natočení = "30.léta"`) and a published `intro` marker, and a
  `MediaRef(cached_path=<seeded video>)`. Implements `list_clips`, `get_clip`,
  and `apply_changes` (records the call so an assertion can confirm publish was
  attempted; no real write needed — the queue rows are the receipt).
- `LocalFileResolver` — `path_for_clip_id(101)` returns the seeded video path so
  `/api/media/101` streams a playable file.
- `StubThumbnailService` — `get_or_fetch` returns `None` (the UI renders a
  placeholder), keeping the harness offline-safe.

### 3. Annotate harness — `tests/walkthrough/harness.py`

A `Walkthrough` wrapper over a Playwright `Page` owning the v1.59 screencast
lifecycle:

- `start(title, description)` → `page.screencast.start(path=...)`,
  `show_chapter(title, description)` (use-case title card),
  `show_actions(position="bottom")` (auto element highlight + action label).
- `step(label, action)` → refresh the **step-counter overlay** via
  `show_overlay(html=...)` (e.g. `Step 3 — Correct the Decade field`), run
  `action(page)`.
- `finish()` → `page.screencast.stop()`, return the saved `.webm` path.
- `record=False` (assert mode) short-circuits every screencast call so headless
  runs do no recording work.

The overlay-HTML builder is a pure function, unit-tested in isolation.

### 4. Scenario format — `tests/walkthrough/scenarios/*.py`

Each scenario is a module exposing `TITLE`, `DESCRIPTION`, and `run(wt)`:

```python
TITLE = "Review and edit an AI annotation"
DESCRIPTION = (
    "An operator opens a clip with a pending AI draft, reviews the suggested "
    "fields, corrects one, then publishes the accepted draft."
)

def run(wt):
    wt.step("Open the clip from the list",
            lambda p: p.get_by_text("archive_30s").click())
    wt.step("Play the proxy to spot-check",
            lambda p: p.get_by_test_id("player-play").click())
    wt.step("Switch to the draft view",
            lambda p: p.locator('button[data-scope="draft"]').click())
    wt.step("Open the Fields tab",
            lambda p: p.get_by_test_id("tab-fields").click())
    wt.step("Edit the proposed Decade field",
            lambda p: p.get_by_test_id("ri-edit-toggle").first.click())
    wt.step("Correct the value",
            lambda p: p.locator("input[data-item-id]").first.fill("40.léta"))
    wt.step("Accept & apply (publish) the draft",
            lambda p: p.get_by_test_id("apply-draft").click())
```

`wt.step(label, action)`:

- increments the counter, updates the **"Step N — <label>"** overlay;
- runs `action(page)` (its auto-waiting *is* the assertion in `--assert` mode — a
  missing element / failed click raises); a scenario may add `wt.expect(...)` for
  a stronger guarantee.

**Selectors.** Prefer `get_by_role` / `get_by_text` / `get_by_test_id`. Some
clip-detail elements already have stable hooks (`button[data-scope="draft"]`,
`div.ri-row[data-item-id]`, `input[data-item-id]`); a few need a new
`data-test=` added to the template (see §6).

### 5. Runner & gallery — `tests/walkthrough/run.py`

CLI (`python -m tests.walkthrough.run`):

- `--assert` — headless, `record=False`; runs every scenario; exits non-zero on
  the first failure. CI/regression entrypoint.
- `--record [scenario...]` — headed, `record=True`; writes
  `tests/walkthrough/artifacts/<scenario>.webm`, then regenerates
  `tests/walkthrough/artifacts/index.html` (a static page: per-scenario TITLE,
  DESCRIPTION, embedded `<video controls>`), then opens the gallery.

`artifacts/` is git-ignored.

### 6. Template selector hooks

Small, inert `data-test=` attributes added where no stable hook exists today,
each a one-line template change:

- `data-test="player-play"` on the player's play control (`_player.html`).
- `data-test="tab-fields"` / `data-test="tab-markers"` on the anno tabs
  (`_anno_panels.html`).
- `data-test="ri-edit-toggle"` on a review-item Edit button (`_anno_panels.html`).
- `data-test="apply-draft"` on the Accept-&-apply button (clip-detail draft area).

Already-usable, no change needed: `button[data-scope="draft"]`,
`div.ri-row[data-item-id]`, `input[data-item-id]`.

### 7. The `/e2e` skill — `.claude/skills/e2e/SKILL.md`

A project skill (sibling of `server-start` / `deploy-staging`) — the single entry
point so neither a human nor Claude has to remember flags or the one-time install:

- `/e2e` → assert mode (`run.py --assert`); reports pass/fail per scenario.
- `/e2e record [scenario]` → record mode; writes videos and opens the gallery.
- `/e2e new "<plain-English flow>"` → scaffolds a
  `tests/walkthrough/scenarios/<slug>.py` from the description, then records it.

Guardrails encoded in the skill body:

1. **Install preflight** — verify `playwright` importable in `.venv` + `chromium`
   installed (`playwright install chromium`) + `ffmpeg` present; offer the
   one-time installs if missing.
2. **Never touches the dev seat** — the runner boots its own in-process app on
   :8766; the skill does not start/stop the :8765 dev server and notes no CatDV
   seat is involved.
3. **`.venv/bin/python` only** (per global rules).
4. Surfaces the artifacts dir + gallery path on completion.

Thin wrapper — all logic lives in `run.py`.

### 8. Where Claude fits

New scenarios are `tests/walkthrough/scenarios/<name>.py` written from a
plain-English description; one `step` per user-visible action, labels phrased as
documentation ("Correct the Decade field", not "fill input").

## Test-data seeding (detail)

Done in `app_server.py` after boot, two parts:

- **Real video.** Generate an ~8s clip with `ffmpeg` (test pattern + burned-in
  running timecode) into the tmp `DATA_DIR`; `LocalFileResolver` returns it for
  clip 101 so `/api/media/101` plays. If `ffmpeg` is missing, fail with a clear
  message.
- **Draft.** Using the real repos (not raw SQL), against `app.state.core_ctx.db`:
  1. `prompts_repo.create_with_initial_version(...)` → a `prompt_version_id`.
  2. `annotations_repo.insert(Annotation(catdv_clip_id=101, …, prompt_version_id=vid,
     clip_snapshot={"ID":101,"name":"archive_30s","markers":[],"fields":{}}))`.
  3. `review_items_repo.bulk_insert([...])` with a **field** item proposing
     `pragafilm.dekáda.natočení = "20.léta"` (deliberately *different* from the
     published `30.léta`, and the scenario corrects it to `40.léta`), plus a
     **marker** item, all `decision="pending"`.

**Published** state needs no seeding — it is the `CanonicalClip` returned by
`FakeArchive`.

## Alternatives considered

- **Subprocess + `ARCHIVE_PROVIDER=fs` (the first draft's plan).** Rejected after
  investigation: the fs provider's path-string keys fail `int(clip.key[1])`, so it
  cannot render the clips list or clip-detail page. It only backs the write-stack
  tests.
- **Node `@playwright/test`.** Rejected: introduces a Node toolchain (ADR 0001)
  and splits seeding across two languages; the Python binding has the same v1.59
  annotation API.
- **Declarative YAML/JSON scenarios.** Rejected for v1: a DSL accretes complexity
  and brittle selectors; the `step()` helper stays readable while being real
  Python.
- **Wire the real SyncEngine + a writable numeric archive** for a genuine sidecar
  writeback. Deferred (chosen Publish fidelity is queue-level): materially more
  harness code and the riskiest part to get right; the durable write-queue rows +
  status pill are a real, sufficient receipt for MVP.
- **Record everything in CI.** Rejected per Playwright best practice (full-suite
  video can be 15–25 GB/run); video is the deliberate deliverable, so `--record`
  is opt-in and `--assert` is the default.

## Consequences

- New dev dependency `playwright>=1.59` + a one-time `playwright install chromium`
  in `.venv` (the `/e2e` skill preflights this). `ffmpeg` required for video
  seeding.
- A handful of inert `data-test=` attributes added to clip-detail templates.
- A new project skill `.claude/skills/e2e/SKILL.md` is the suite's entry point; a
  thin wrapper over `run.py` that stays clear of the :8765 dev seat.
- Reviewers get watchable, self-documenting `.webm` artifacts that double as user
  docs; the regression path (`--assert`) runs without video cost.
- The approach is one flow deep on purpose; Studio/Prompt/Admin flows, cloud
  upload, the generate-on-camera step, and a real SyncEngine writeback are
  deliberate follow-ons that reuse the same harness.

## Manual acceptance flows

1. **Offline, no seat.** With CatDV unreachable, run
   `python -m tests.walkthrough.run --assert`. The in-process app boots on
   :8766, the clip-detail scenario runs green, and **no** CatDV session is opened
   (no external init under `CATDV_USERNAME=""`; nothing on :8765 is touched).
2. **Annotated recording produced.** Run
   `python -m tests.walkthrough.run --record review-edit-annotation`. A file
   `tests/walkthrough/artifacts/review-edit-annotation.webm` is created. Playing
   it shows: (a) a chapter title card "Review and edit an AI annotation" with the
   description, (b) the proxy video actually playing (running timecode visible),
   (c) an on-screen "Step N — <label>" counter that advances 1→7, (d) each click
   highlighted with its action label.
3. **Publish is real to the queue.** After the recording run, query the tmp DB:
   the corrected review item is `decision='accepted'` with an `applied_at`, a new
   `clip_versions` row exists for clip 101, and a `pending_operations` row was
   enqueued for the publish. In the video, the status pill changes to the
   publishing/queued state after the apply click — no button stub.
4. **Draft vs published both visible.** In the recorded video, the draft view
   shows the seeded suggestion (`20.léta`) differing from the published value
   (`30.léta`) before the edit; the operator corrects it to `40.léta` and applies.
5. **Gallery review.** After `--record` with no filter, open
   `tests/walkthrough/artifacts/index.html`: every scenario is listed with title,
   description, and an embedded playable video. A reviewer who didn't write the
   test can watch and understand the use case end to end.
6. **Assert mode catches regressions.** Temporarily remove the
   `data-test="tab-fields"` hook; `--assert` fails fast on the "Open the Fields
   tab" step with a clear locator error; restore it and it passes.
7. **`/e2e` skill end to end.** On a clean checkout, `/e2e` preflights the install
   (offers `playwright install chromium` if missing), runs assert mode, and
   reports per-scenario pass/fail without starting the :8765 dev server. `/e2e
   record` produces the videos and opens the gallery. `/e2e new "open a clip and
   add a marker"` scaffolds a scenario file and records it.
8. **Claude authors a scenario.** Add a second scenario from a plain-English
   description; it runs under both `--assert` and `--record` with no harness
   changes and appears in the gallery.
