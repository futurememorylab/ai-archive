# End-to-end walkthrough tests

Browser-driven tests that exercise the **real app** through Playwright and
double as **annotated walkthrough videos** for human review / documentation.
One scenario file = one user story = one video. They run **fully offline** in
their own in-process app on port `8766` and **never touch the CatDV license
seat** or the `:8765` dev server.

The `/e2e` skill is the day-to-day entry point; this README is the reference.

## Running

```bash
# fast headless regression — pass/fail, no video
.venv/bin/python -m tests.walkthrough.run --assert

# headed, annotated .webm per scenario + a grouped gallery (index.html)
.venv/bin/python -m tests.walkthrough.run --record [slug ...]
```

Or via the skill: `/e2e`, `/e2e record [slug]`, `/e2e new "<flow>"`.

The assert-mode run is also a pytest test (`test_scenario_e2e.py`), so a plain
`.venv/bin/pytest -q` exercises it — it self-skips when Chromium or ffmpeg are
missing. One-time setup:

```bash
.venv/bin/pip install -e ".[dev]" && .venv/bin/playwright install chromium
# ffmpeg: brew install ffmpeg   (seeds the proxy video + thumbnail poster)
```

## How it works

- **In-process app under test** (`app_server.py`). Boots the real FastAPI app
  on a daemon thread (real socket on `127.0.0.1:8766`) and injects fakes via
  `install_live_ctx`. The UI needs a numeric clip key (`int(clip.key[1])`),
  which the filesystem provider can't supply — hence injection, not env. See
  ADR 0109.
- **Fully offline, no seat.** No CatDV / GCS / Gemini; credentials are
  force-blanked at boot so nothing external is constructed.
- **Mocked archive + media** (`fakes.py`). `FakeArchive` serves an in-memory
  clip catalog and filters `list_clips` by query text + paginates.
  `LocalFileResolver` serves a real ffmpeg-seeded proxy so the player plays on
  camera; `StubThumbnailService` serves one real JPEG poster so rows show a
  thumbnail (not a broken image).
- **Seeded DB, kept separate from the run** (`seed.py`). `build_seed_db()`
  builds a standalone, migrated + seeded SQLite file on its **own** connection
  (draft on clip 101, an "awaiting review" fixture clip, and the catalog in
  `clip_list_cache` so the annotation-status filters resolve). `app_server`
  copies it to `app.db`; the app connects to the copy for the run.
- **Annotated recording** (`harness.py`). `Walkthrough.step(label, action)`
  shows a "Step N — label" overlay, runs the action, then dwells so the result
  (e.g. an HTMX table swap) paints and is captured under that step's overlay.
  Recording forces the page's native viewport size to defeat Playwright's
  800px screencast cap, so videos are 1280×800. All of this is skipped in
  assert mode.
- **Gallery** (`gallery.py`). `--record` writes `artifacts/index.html`: videos
  grouped by `TOPIC` into sections with a sidebar navigation menu. The
  `artifacts/` dir is git-ignored (regenerated on demand).

## Anatomy of a scenario

`scenarios/<name>.py`, auto-discovered if it exposes `SLUG` + `run`:

```python
SLUG = "search-no-results"
TOPIC = "Search page"          # gallery grouping
TITLE = "Search with no matches"
DESCRIPTION = "An operator searches for a term no clip name contains…"

def run(wt):
    wt.step("Search for a term that matches nothing",
            lambda p: search_for(p, NO_RESULTS_TERM))
    wt.step("The list shows the empty-state message",
            lambda p: expect_empty_state(p))
```

- One `wt.step` per user-visible action; phrase the label as documentation.
- Shared actions/assertions live in `scenarios/_search_support.py` (the `_`
  prefix keeps the loader from treating it as a scenario). Use Playwright
  `expect(...)` so a step that can't reach its target fails the scenario.
- If a click can't find its target, add an inert `data-test="…"` hook to the
  template (see the existing `player-play` / `apply-draft` hooks) — don't
  couple to styling classes.
- Filter/search scenarios that need DB state rely on the seed (see
  `search_filter_awaiting_review.py`). If a scenario mutates shared state
  (e.g. publishing), don't make another scenario depend on the mutated clip —
  use a dedicated fixture clip.

## Keep scenarios in sync with the UI

When you change UI functionality — a page template, an Alpine component, a
route that renders a page, or a user-facing flow — **add, update, or remove
the affected walkthrough scenario(s)** in the same PR, and re-run `/e2e`
(assert mode) before merging. A scenario that no longer matches the UI is worse
than none. This mirrors the rule in the repo-root `CLAUDE.md`.

## Files

| File | Owns |
|---|---|
| `run.py` | CLI: discover scenarios, drive the browser, write the gallery |
| `app_server.py` | Boot the in-process app + inject fakes + seeded DB copy |
| `fakes.py` | `FakeArchive` catalog + resolver + thumbnail stub + clip builders |
| `seed.py` | Proxy video, thumbnail poster, DB drafts, `build_seed_db` |
| `harness.py` | `Walkthrough` step/overlay/native-size recording |
| `gallery.py` | Static grouped gallery + nav menu |
| `scenarios/` | One module per walkthrough (`SLUG`/`TOPIC`/`TITLE`/`run`) |
