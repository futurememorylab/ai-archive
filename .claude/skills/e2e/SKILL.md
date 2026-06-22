---
name: e2e
description: Run, record, and review the annotated Playwright walkthrough tests for catdv-annotator. Use whenever the user asks to run the e2e / walkthrough / browser tests, record a walkthrough video, regenerate the test-video gallery, or scaffold a new walkthrough scenario. Runs fully offline in its own in-process app on port 8766 and never touches the CatDV license seat or the :8765 dev server.
---

# /e2e — annotated walkthrough tests

Drives the real app in a browser via Python Playwright, optionally recording an
annotated walkthrough video (use-case chapter card + "Step N" counter + action
highlights). See `docs/specs/2026-06-22-playwright-walkthrough-tests-design.md`.

**These tests boot their own in-process app on port 8766 with injected fakes.
They do NOT use CatDV, GCS, or Gemini, and do NOT start/stop the :8765 dev
server. There is no license seat involved — never start the dev server for this.**

## Modes

- `/e2e` (no args) → **assert mode** (fast, headless, pass/fail):
  ```bash
  .venv/bin/python -m tests.walkthrough.run --assert
  ```
- `/e2e record [slug]` → **record mode** (headed, annotated webm + gallery):
  ```bash
  .venv/bin/python -m tests.walkthrough.run --record [slug]
  ```
  Then report the gallery path (`tests/walkthrough/artifacts/index.html`); the
  runner opens it automatically.
- `/e2e new "<plain-English flow>"` → scaffold a new scenario:
  1. Create `tests/walkthrough/scenarios/<slug>.py` with `SLUG`, `TITLE`,
     `DESCRIPTION`, and a `run(wt)` of `wt.step("...", lambda p: ...)` calls,
     following `scenarios/review_edit_annotation.py`. One `step` per
     user-visible action; phrase labels as documentation.
  2. Run `.venv/bin/python -m tests.walkthrough.run --record <slug>` to record it.
  3. If a click can't find its target, add a `data-test="..."` hook to the
     relevant template (see the spec §6) and re-run.

## Preflight (always do this first)

1. Confirm Playwright + Chromium + ffmpeg are available:
   ```bash
   .venv/bin/python -c "import playwright; print('playwright', playwright.__version__)"
   .venv/bin/python -c "from playwright.sync_api import sync_playwright; \
     pw = sync_playwright().start(); print('chromium', pw.chromium.executable_path); pw.stop()" 2>/dev/null || echo "chromium missing"
   command -v ffmpeg >/dev/null && echo "ffmpeg ok" || echo "ffmpeg missing"
   ```
2. If anything is missing, offer to install (one-time):
   ```bash
   .venv/bin/pip install -e ".[dev]"
   .venv/bin/playwright install chromium
   # ffmpeg: brew install ffmpeg   (ask before installing system packages)
   ```
3. Always use `.venv/bin/python` (never system Python).

## After running

- Report per-scenario PASS/FAIL.
- In record mode, give the user the gallery path and remind them the videos
  double as user documentation.
- Never leave a process on :8766 — the runner tears its server down; if a run
  was interrupted, check `lsof -nP -iTCP:8766 -sTCP:LISTEN` and stop strays.
