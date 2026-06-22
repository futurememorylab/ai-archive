# Annotated Playwright walkthrough tests

**Date:** 2026-06-22
**Status:** Draft — pending review

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
   real DB writes, and real archive writeback — pointed at local fixtures, not
   CatDV/GCS/Gemini.
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
- Exercising the **AI "Generate" button** on camera (requires wiring
  `fake_gemini` into the server process — deferred; see Alternatives).

## Background — what already exists

Grounded against the codebase on 2026-06-22:

- **Filesystem archive provider.** `ARCHIVE_PROVIDER=fs` + `FS_ROOT=<dir>` +
  `PROXY_SOURCE=filesystem` (`backend/app/settings.py`) makes the app run against
  a local directory of clips with `.annot.json` sidecars. It is **fully writable**:
  `backend/app/archive/providers/fs/adapter.py::apply_changes` applies a
  `ChangeSet` to the sidecar. This is real writeback with **no seat**.
- **Existing fixture archive.** `tests/fixtures/fs_archive/` holds
  `archive_30s/clip001.mov`, `clip001.annot.json` (one `intro` marker, the
  `pragafilm.dekáda.natočení = 30.léta` field, a note), and `.archive/fields.json`
  (a picklist, a bool, and a text field with `is_editable: true`).
- **Dev auth bypass.** `APP_ENV=dev` admits the operator as admin with no auth
  gate (`backend/app/main.py` `_auth_gate`).
- **Server entry.** `uvicorn backend.app.main:app` (default port 8765 per
  `CLAUDE.md`); the lifespan builds context from settings/env.
- **Draft vs published model.** *Published* annotation = the archive-side sidecar
  (`clip001.annot.json`). *Draft* = rows in the app DB (`annotation` +
  `review_items`), surfaced by `clips.py::_build_draft_for_clip` /
  `services/draft_view.py::build_draft_view`. The fs provider does **not** create
  drafts — drafts come from the AI annotator (Gemini) or are seeded directly.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Toolchain | **Python Playwright** (`playwright>=1.59`) | Honors ADR 0001; reuses seeding code; v1.59 screencast annotation API is in the Python binding. |
| Scenario format | **Python module + `step()` helper** | Full power of code; step labels auto-drive annotations and double as doc narration; easy for Claude to write. |
| Test data | **fs archive + seeded DB draft + a real short video** | One clip carries both published (sidecar) and draft (DB) state; real writeback. |
| AI generate step | **Seed the draft; no Gemini on camera (MVP)** | Server stays pure env-config; no test hooks in prod boot. |
| Video access | **Local gallery (`index.html`)** | Zero infra; doubles as user docs; cloud upload deferred. |
| Run modes | **`--assert` (headless, no video) and `--record` (headed, annotated)** | Best practice: don't record everything; same scenarios back both so they can't drift. |
| Entry point | **`/e2e` project skill** wrapping `tests/walkthrough/run.py` | One command to run/record/review; encodes the install check + gallery-open so neither a human nor Claude has to remember the flags. |

## Architecture

```
┌─ test-app fixture ──┐   ┌─ scenario (.py) ────┐   ┌─ annotate harness ──┐   ┌─ gallery ─┐
│ uvicorn on :8766    │ → │ TITLE, DESCRIPTION  │ → │ screencast.start    │ → │ index.html│
│ ARCHIVE_PROVIDER=fs │   │ run(wt):            │   │ show_chapter(TITLE) │   │  + *.webm │
│ FS_ROOT=<tmp copy>  │   │   wt.step("…", act) │   │ show_actions(...)   │   │  opens in │
│ tmp DB + tmp DATA   │   │   wt.step("…", act) │   │ step overlay (HTML) │   │  browser  │
│ APP_ENV=dev (no auth)│  │                     │   │ → <scenario>.webm   │   │           │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘   └───────────┘
```

Everything lives under a new `tests/walkthrough/` directory, isolated from the
existing `tests/unit` and `tests/integration` suites.

### 1. Test-app fixture — `tests/walkthrough/app_fixture.py`

A pytest fixture (session-scoped) that:

1. Creates a tmp `DATA_DIR` and a tmp copy of `tests/fixtures/fs_archive/` as
   `FS_ROOT` (copy, so writeback during a run never mutates the committed
   fixture).
2. Replaces the placeholder `clip001.mov` (currently **0 bytes**) in the copy
   with a real short clip — see §4.
3. Seeds the draft state into the tmp DB — see §4.
4. Boots `uvicorn backend.app.main:app` in a **subprocess on port 8766** (not
   8765 — must not collide with or be mistaken for the dev server / seat) with
   env: `APP_ENV=dev`, `ARCHIVE_PROVIDER=fs`, `FS_ROOT=<tmp>`,
   `PROXY_SOURCE=filesystem`, `DATA_DIR=<tmp>`, `BIND_PORT=8766`.
5. Polls `GET /` until ready (bounded; fail fast on timeout).
6. Yields the base URL; on teardown sends **SIGTERM** (graceful) and waits for
   exit. (No CatDV session is ever opened under the fs provider, so there is no
   seat to leak — but SIGTERM keeps lifespan shutdown clean.)

Pure env-config — no Python objects injected into the server, no test-only branch
in app boot.

### 2. Scenario format — `tests/walkthrough/scenarios/*.py`

Each scenario is a module exposing `TITLE`, `DESCRIPTION`, and `run(wt)`:

```python
TITLE = "Review and edit an AI annotation"
DESCRIPTION = (
    "An operator opens a clip with a pending AI draft, reviews the suggested "
    "scenes and fields, edits one field, saves the draft, then publishes."
)

def run(wt):
    wt.step("Open the clip from the list",
            lambda p: p.get_by_text("archive_30s").click())
    wt.step("Play the proxy to spot-check",
            lambda p: p.get_by_test_id("play").click())
    wt.step("Review the AI draft fields",
            lambda p: p.get_by_test_id("draft-panel").scroll_into_view_if_needed())
    wt.step("Correct the Decade field",
            lambda p: p.get_by_test_id("field-decade").select_option("40.léta"))
    wt.step("Save the draft",
            lambda p: p.get_by_role("button", name="Save").click())
    wt.step("Publish to the archive",
            lambda p: p.get_by_role("button", name="Publish").click())
```

`wt` is a `Walkthrough` object (see §3). `wt.step(label, action)`:

- increments the step counter and updates the on-screen overlay to
  **"Step N — <label>"**;
- runs `action(page)`;
- in `--assert` mode, the action's own Playwright auto-waiting *is* the
  assertion (a missing element / failed click raises); a scenario may add
  explicit `wt.expect(...)` checks where a stronger guarantee is wanted.

The step label is the single source of truth: it drives the overlay, the
`--assert` step boundary, and the documentation narration.

**Selector policy.** Scenarios prefer `get_by_role` / `get_by_text` /
`get_by_test_id`. Where a stable hook is missing, we add a `data-test=` attribute
to the template (a small, reviewable template change) rather than coupling to CSS.

### 3. Annotate harness — `tests/walkthrough/harness.py`

A thin `Walkthrough` wrapper over a Playwright `Page` that owns the v1.59
screencast lifecycle:

- `start(title, description)` → `page.screencast.start(path=...)`,
  `show_chapter(title, description)` (the use-case title card),
  `show_actions(position="bottom")` (auto element-highlight + action label).
- `step(label, action)` → render/refresh the **step-counter overlay** via
  `show_overlay(html=...)` (an HTML badge, e.g. `Step 3 — Correct the Decade
  field`), run the action.
- `finish()` → `page.screencast.stop()`, returns the saved `.webm` path.
- `record=False` (assert mode) short-circuits all screencast calls so headless
  runs do no recording work.

The overlay HTML is small and self-contained; its construction is unit-tested
(see Testing).

### 4. Test-data seeding

Done by the fixture, two parts:

- **Real video.** Generate an ~8s clip with `ffmpeg` (test pattern + a visible
  running timecode burn-in) and write it as `clip001.mov` in the tmp `FS_ROOT`.
  A visible timecode makes "is the player actually playing?" obvious on camera.
  If `ffmpeg` is unavailable, fail with a clear message (the harness needs it;
  it is already an optional dependency the fs provider probes for via
  `media_probe`).
- **Draft.** Insert an `annotation` row + its `review_items` for `clip001` into
  the tmp DB via the existing repositories (not raw SQL), so
  `_build_draft_for_clip` returns a populated draft. The draft deliberately
  differs from the published sidecar (e.g. proposes `40.léta` vs the published
  `30.léta`) so the "review → correct → publish" story is visible.

**Published** state needs no seeding — it is the committed `clip001.annot.json`
sidecar, read through the fs provider.

### 5. Runner & gallery — `tests/walkthrough/run.py`

A small CLI (`python -m tests.walkthrough.run`):

- `--assert` — headless, `record=False`, runs every scenario, exits non-zero on
  the first failure. This is the CI/regression entrypoint.
- `--record [scenario...]` — headed, `record=True`, runs the named scenarios (or
  all), writes `tests/walkthrough/artifacts/<scenario>.webm`, then regenerates
  `tests/walkthrough/artifacts/index.html` — a static page listing each scenario
  (TITLE, DESCRIPTION, embedded `<video controls>`).
- After `--record`, the runner opens the gallery for review (per the user's
  global setup, via `zed -e` / the default browser). The `artifacts/` dir is
  git-ignored.

### 6. The `/e2e` skill — `.claude/skills/e2e/SKILL.md`

A project skill (sibling of `server-start` / `deploy-staging`) that is the single
entry point for the suite — so neither a human nor Claude has to remember the
runner's flags or the one-time install. Argument-driven:

- `/e2e` (no args) → **assert mode.** Runs `python -m tests.walkthrough.run
  --assert` and reports pass/fail per scenario. The fast "did I break a flow?"
  check.
- `/e2e record [scenario]` → **record mode.** Runs `--record` (all scenarios, or
  the named one), then opens `tests/walkthrough/artifacts/index.html` for review.
  The "produce the watchable/doc videos" path.
- `/e2e new "<plain-English flow>"` → scaffolds a new
  `tests/walkthrough/scenarios/<slug>.py` from the description (the `step()`
  format), then records it so the author sees it immediately.

The skill body encodes the guardrails:

1. **Install check first** — verify `playwright` is importable in `.venv` and
   `chromium` is installed (`playwright install chromium`), and that `ffmpeg` is
   present for video seeding; offer to run the one-time installs if missing.
2. **Never touches the dev seat** — the runner boots its own fs-provider app on
   :8766; the skill explicitly does **not** start/stop the :8765 dev server and
   reminds that no CatDV seat is involved.
3. **Always use `.venv/bin/python`** (per global rules), never system Python.
4. Surfaces the artifacts dir path and the gallery URL on completion.

The skill is a thin wrapper — all logic lives in `run.py`; the skill only
chooses arguments, runs install preflight, and opens the gallery.

### 7. Where Claude fits

New scenarios are added as `tests/walkthrough/scenarios/<name>.py` from a
plain-English description. The `step()` format is the contract: one `step` per
user-visible action, label phrased as documentation ("Correct the Decade
field", not "select_option decade").

## Alternatives considered

- **Node `@playwright/test`.** Config-level video annotations + HTML reporter +
  trace viewer out of the box. Rejected: introduces a Node toolchain (violates
  ADR 0001's spirit) and splits test-data seeding across two languages. The
  Python binding has the same v1.59 annotation API, so the gap is small.
- **Declarative YAML/JSON scenarios.** More readable for non-devs and very
  doc-like. Rejected for v1: a DSL accretes complexity, selectors get brittle,
  and hard flows (waits, conditionals) become awkward. The `step()` helper keeps
  scenarios readable while staying real Python.
- **Record everything in CI.** Rejected per Playwright best practice — full-suite
  video can be 15–25 GB/run; the trace viewer is the right tool for *failure
  debugging*. Here video is the deliberate deliverable, so `--assert` (no video)
  is the default and `--record` is opt-in.
- **AI "Generate" on camera (fake_gemini in the server).** Would complete the
  create → review → publish loop. Deferred: it requires an env-selectable
  fake-AI branch in app boot. Seeding the draft keeps the server pure env-config
  for MVP; this is a clean follow-on.
- **In-process ASGI server (inject Python fakes).** Would allow injecting
  `fake_gemini`/`fake_catdv` directly. Rejected for MVP: Playwright needs a real
  listening socket, and a subprocess with env-config is simpler and closer to how
  the app actually boots. (Revisit if/when we want fakes injected for the
  generate step.)

## Consequences

- New dev dependency `playwright>=1.59` plus a one-time `playwright install
  chromium` in the `.venv` (the `/e2e` skill preflights this). `ffmpeg` required
  for video seeding.
- A new project skill `.claude/skills/e2e/SKILL.md` is the suite's entry point;
  it is a thin wrapper over `run.py` and explicitly stays clear of the :8765 dev
  seat.
- A handful of `data-test=` attributes added to clip-detail templates for stable
  selectors — small, reviewable, and inert at runtime.
- Reviewers get watchable, self-documenting `.webm` artifacts; the same files
  serve as user documentation. The regression path (`--assert`) runs in CI
  without video cost.
- The approach is one flow deep on purpose; Studio/Prompt/Admin flows, cloud
  upload, and the generate-on-camera step are deliberate follow-ons that reuse
  the same harness.

## Manual acceptance flows

1. **Offline boot, no seat.** With CatDV unreachable (VPN off), run
   `python -m tests.walkthrough.run --assert`. The test-app boots on :8766, the
   clip-detail scenario runs green, and no CatDV session is opened (server log
   shows no `POST /session`; no `Maximum:2` error). Confirm nothing is listening
   on :8765 was touched.
2. **Annotated recording produced.** Run
   `python -m tests.walkthrough.run --record review-edit-annotation`. A file
   `tests/walkthrough/artifacts/review-edit-annotation.webm` is created. Playing
   it shows: (a) a chapter title card "Review and edit an AI annotation" with the
   description, (b) the proxy video actually playing (running timecode visible),
   (c) an on-screen "Step N — <label>" counter that advances 1→6, (d) each click
   highlighted with its action label.
3. **Real writeback, not mocked.** After the recording run, inspect the tmp
   `FS_ROOT` copy's `clip001.annot.json`: the published field reflects the
   value chosen in the "Publish" step (e.g. now `40.léta`), proving the Publish
   button performed a real `apply_changes` writeback — no button stub.
4. **Draft vs published both visible.** In the recorded video, the draft panel
   shows the seeded AI suggestion differing from the published value before the
   edit, and the published view reflects the new value after Publish — one clip,
   both states.
5. **Gallery review.** After `--record` with no scenario filter, open
   `tests/walkthrough/artifacts/index.html`. It lists every scenario with its
   title, description, and an embedded playable video. A reviewer who didn't
   write the test can watch it and understand the use case end to end.
6. **Assert mode catches regressions.** Temporarily rename the "Decade" field's
   `data-test` hook in the template; `--assert` fails fast on the
   "Correct the Decade field" step with a clear locator error; restore the hook
   and it passes again.
7. **Claude authors a scenario.** Add a second scenario file from a plain-English
   description (e.g. "open a clip and add a new marker, then publish"); it runs
   under both `--assert` and `--record` with no harness changes, and appears in
   the gallery.
8. **`/e2e` skill end to end.** On a clean checkout, run `/e2e` — the skill
   preflights the install (offers `playwright install chromium` if missing),
   runs assert mode, and reports per-scenario pass/fail without starting the
   :8765 dev server. Then run `/e2e record` — it produces the videos and opens
   the gallery. Then `/e2e new "open a clip and add a marker"` — it scaffolds a
   scenario file and records it.
