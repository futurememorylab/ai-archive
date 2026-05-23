# Remote subagent prompt — implement Gemini Live clip assistant

> Paste the block below into a fresh Claude Code session running on a machine
> with this repo checked out. The agent has no memory of the conversation that
> wrote the plan — everything it needs is in the prompt.

---

You're picking up a written implementation plan. Everything you need to do
the work is in the repo at HEAD on `main` — read it, don't guess.

## Repo

`catdv-annotator` (Python 3.12+, FastAPI, aiosqlite, Alpine.js). You are
already in the repo root. Confirm with `git rev-parse --show-toplevel` and
`cat CLAUDE.md` and `cat ../CLAUDE.md` to load both project- and parent-scope
conventions (CatDV session discipline, decisions.md format, venv usage,
network notes).

## Your task

Execute the plan at:

```
docs/plans/2026-05-23-gemini-live-clip-assistant.md
```

This adds a **Czech voice assistant** to the clip-detail page. Pressing a new
`🎤 Live` button mints an ephemeral Gemini Developer API token server-side,
opens a WebSocket **directly from the browser** to Google's Gemini Live
endpoint (audio bytes never traverse our process — a prior PoC of a
backend bridge scrambled the audio), and lets the operator talk in Czech
about the currently-visible frame. The session has access to a system
instruction in Czech, all published + draft annotation context, the
`googleSearch` grounding tool, and a function tool `end_session(reason)`
so the operator can stop by voice. After the session, the transcript is
persisted in a new `live_sessions` SQLite table and a non-Live
`generateContent` call distills a Czech summary. Past sessions surface in
a read-only **History** tab next to Published / Draft — nothing is
auto-pushed into draft annotations.

The spec lives at `docs/specs/2026-05-23-gemini-live-clip-assistant-design.md` —
read it before Task 1. It explains why we use the Gemini **Developer API**
(`generativelanguage.googleapis.com`) rather than Vertex AI for Live (the
`authTokens.create` endpoint is purpose-built for browser-direct, with
single-use purpose-bound tokens), why the audio path is browser↔Google
direct (PoC scrambled audio through a backend bridge), why summaries do
not auto-populate the draft annotation column (operator wants a clean
separation), and the exact shape of the Czech "Publikované / Rozpracované"
context blocks. Do **not** re-derive those decisions; they're locked.
The decision log entry in `docs/decisions.md` 2026-05-23 also documents
the tradeoffs.

## Execution rules

- **One task at a time, in order, with checkpoints.** Use the
  `superpowers:executing-plans` skill to drive the loop. Treat each Task
  in the plan as one batch: write the failing test(s), confirm they fail,
  write the code, confirm they pass, commit.
- **TDD is required** for all backend work (Phases 1–5). The plan is
  structured around it. If a step says "write the failing test", run the
  test and confirm the failure mode before writing implementation code.
  Don't skip ahead.
- **Phase 6 (browser audio) has no JS test framework** in this repo —
  confirmed by `find tests -name '*.js'` → empty. For Phase 6 tasks,
  implement and commit per task, then verify integrated behavior in
  Phase 8 (Task 27, operator-only). Do **not** invent a JS test harness.
- **Frequent commits.** Each task ends with a commit step using the exact
  message shown in the plan. Don't squash tasks together.
- **Don't expand scope.** The plan is the spec. If a step looks redundant
  or over-specified, complete it as written. If something is genuinely
  unbuildable as specified, stop and surface the contradiction — don't
  improvise.
- **One new test dependency only.** The plan introduces `respx` for
  mocking `httpx.AsyncClient` calls. Add it to dev deps (see Spec Gaps
  §1 below for how). No other new libraries — everything else builds on
  FastAPI, Jinja2, aiosqlite, httpx, pytest, pytest-asyncio, Alpine,
  already vendored.
- **Audio bytes must never flow through our backend.** This is the
  hardest-locked decision in the spec. If you find yourself writing a
  WebSocket route in FastAPI or piping PCM through the Python process,
  stop — re-read §3 of the spec and the [[gemini-live-browser-direct]]
  decision. The backend's role is token mint + transcript persist +
  non-Live summarize. Nothing else.

## Verification

- Use the venv: `.venv/bin/pytest` and `.venv/bin/python` (never system
  `python3` — see `~/.claude/CLAUDE.md` global rules).
- After each task: run that task's specific tests with `-v` and confirm
  the expected pass/fail at each step.
- After each Phase: run the full suite to catch regressions:
  ```
  .venv/bin/pytest -q
  ```
- After Phase 5 (backend feature-complete): the full suite must be green
  with no skipped tests beyond pre-existing ones.

## CRITICAL — Network / external-service safety

You will **not** call any live external service. All of your tests use
mocks (`respx` for `httpx`). Do **not**:

- `curl` or `wget` `generativelanguage.googleapis.com`, `aiplatform.googleapis.com`,
  or any other Google endpoint.
- Run `deploy/enable-gemini-live.sh` (Task 25) — it creates real GCP
  resources on the operator's project. Just commit the script; the
  operator runs it after merge.
- Set or paste a real `GEMINI_API_KEY` into `.env`. The plan's test
  fixtures use literal `"test-key"` strings; that's the only key value
  that should appear in your edits.
- Start a `uvicorn` / `./run.sh` instance for any reason. Aside from the
  CatDV seat issue noted in `CLAUDE.md`, the live server attempts to
  connect to the real CatDV VPN at startup and may also attempt the new
  Gemini Live calls if `GEMINI_API_KEY` is set — exactly what we're
  trying to avoid.
- `ping`, `curl`, `nc`, or otherwise probe `192.168.1.41` (the CatDV
  host on the VPN). The parent `CLAUDE.md` covers this in detail.
- Run any test marked `@pytest.mark.live` or `@pytest.mark.requires_vpn`
  (grep `tests/` to check whether such markers exist before running the
  full suite).

`respx.mock` is the only acceptable "live HTTP" surface in your test
runs — it intercepts at the `httpx` boundary and doesn't open real
sockets.

## Operator-only steps — SKIP

The following plan steps require a real `GEMINI_API_KEY`, a real
running browser, and microphone permission on the operator's machine.
Skip them and mark each checkbox `N/A — operator-only` in your final
report:

- **Task 17 Step 2** — manual `liveSession(123, ...)` console smoke. The
  Alpine component is exercised via the integration tests in Tasks 11–14;
  full UI smoke happens in Task 27.
- **Task 18 Step 2** — explicitly deferred to Task 27 in the plan.
- **Task 23 Step 4** (template context wiring) — implement the change
  itself, but the visual verification of the header overlay / transcript
  strip / `🎤 Live` button is part of Task 27.
- **Task 25 Step 1–3** — write the script, set its mode to `0755`, and
  commit it. Do **not** execute `./deploy/enable-gemini-live.sh` —
  that's an operator-run step that creates billable GCP resources.
- **Task 27 (all)** — entire manual checklist. Leave every checkbox
  unchecked in the plan and call out the whole task as
  `N/A — operator-only` in your final report.

For all other Phase 6 and Phase 7 tasks — implementing
`audio-worklet-recorder.js`, the `liveSession.js` Alpine component, the
Jinja template overlay, the History tab + its partial route — implement
and commit normally. The browser code's correctness is verified by
operator review of the diffs and the manual run in Task 27; the History
route is exercised by `test_routes_live_history_partial.py` (Task 24).

## Spec gaps you may hit and what to do

The plan's TDD discipline depends on calling existing repo / service /
template methods by name. Some of those names may differ from what the
plan assumes — grep first, then adapt. Specifically:

1. **`respx` not in dev deps.** Add it:
   ```
   .venv/bin/pip install respx
   ```
   Then add `"respx"` to whichever dev-deps group `pyproject.toml`
   uses (look for `pytest`, `pytest-asyncio`, `httpx` already declared;
   add `respx` alongside them). Commit the `pyproject.toml` change
   separately under message `chore(deps): add respx for httpx mocking`
   before starting Task 9 — it's the first task that imports `respx`.

2. **`PromptsRepo` method names** may not be exactly `get_by_name` and
   `get_production_version`. Open `backend/app/repositories/prompts.py`
   and grep for methods returning a single prompt by name and the
   production-state version. If the actual names are different (e.g.
   `find_by_name`, `production_version_for`, etc.), use those in
   Task 11 Step 4 and Task 7 Step 1, and note the substitution in your
   final report. If `get_by_name` truly doesn't exist, **add a thin
   wrapper method** to the repo (one query, one return) rather than
   inlining SQL in the route. Cover the new method with one unit test.

3. **`view_models._fix` import path.** The memory
   `[[catdv-mojibake-display-fix]]` says the mojibake repair helper
   lives in `backend/app/ui/view_models.py`. Open that file and confirm
   the symbol name — it may be `_fix`, `fix_mojibake`, `repair_text`,
   or similar. Use the actual name in `services/live_context.py`
   (Task 5 Step 3) and update the test cases (e) and (f) in Task 5
   Step 1 if the helper isn't importable as `_fix`. Note the actual
   symbol name in your final report.

4. **`routes/pages.py` view-model helper names.** The plan references
   `_build_clip_view_model(ctx, clip_id)` and `_build_draft_for_clip(ctx,
   clip_id)` in Task 11 (the indirection points
   `load_clip_for_live` / `load_draft_for_live`). Grep
   `backend/app/routes/pages.py` for the actual function that builds the
   clip view-model passed to `clip_detail.html`. The draft helper is
   confirmed to exist (`_build_draft_for_clip`, see grep against current
   repo); the clip-VM helper's name might differ. Use the real name;
   note it in your report.

5. **`aiosqlite.Connection` fixture in repo / route tests.** The
   existing `tests/integration/` tests show two patterns — some use a
   pytest-asyncio `async def` fixture with `yield`, others use a
   `conftest`-level fixture. Use whichever pattern the closest existing
   test file uses (e.g. `tests/integration/test_annotations_repo.py`
   for repo tests; `tests/integration/test_routes_*.py` for route
   tests). If the plan's inline fixture doesn't work because of a
   pytest-asyncio scope mismatch, adopt the existing convention.

6. **`Templates` / Jinja env reference in `routes/pages.py`.** The plan's
   Task 24 Step 5 calls `templates.TemplateResponse(...)`. Confirm the
   exact symbol name by grepping `routes/pages.py` for existing
   `TemplateResponse` calls. If it's `_templates` or
   `request.app.state.templates`, use that.

7. **CSS file location** (Task 23 Step 3). The plan asks you to add CSS
   for `.live-bar`, `.rec-pill`, `.live-strip`. Grep `backend/app/static/`
   for `.detail-hdr` to find the right `.css` file. Match that file's
   formatting style.

8. **`_anno_panels.html` tab strip pattern** (Task 24 Step 4). Read the
   file first. The existing pattern likely has a `<div class="anno-tabs">`
   or similar with `@click="tab='markers'"` buttons. Add the **History**
   button + content branch using the exact same idioms. Don't restructure
   the existing tabs.

9. **`PromptsRepo.create_with_initial_version` signature** (Task 7
   Step 3). Confirm by reading the repo source. If the `target_map` /
   `output_schema` params have different names or accept different
   types (e.g. expect `TargetMap` model instances rather than plain
   dicts), pass through whatever the existing code calling this method
   passes in (look at `backend/app/seed.py:seed_default_prompt`).

10. **Frontend element selectors** (Task 18 + Task 20). The plan
    grabs the video element via `document.querySelector("video.video")`
    — that selector matches `clip_detail.html:46`
    (`<video x-ref="video" class="video" …>`). If the operator's branch
    has since restyled the player and removed the `video` class, fall
    back to `document.querySelector("video")` and note it.

For any of the above, if the codebase shape is meaningfully different
from what the plan assumes, **fix the test/implementation to match
reality and document the deviation in your final report**. Do not
restructure the plan's overall architecture or invent new abstractions
to "tidy up" what you see.

## Things explicitly out of scope

These looked tempting but stay out of this PR:

- **No WebSocket route in FastAPI.** If you find yourself opening one,
  re-read the spec's non-goals.
- **No SDK-level wrappers around the Gemini Developer API.** Two HTTP
  calls (`authTokens.create`, `generateContent`) via raw `httpx.AsyncClient`
  is all we need; introducing the `google-genai` SDK would be wasted
  scope.
- **No persistence of audio bytes.** We store transcripts (text) and
  summaries (text). Inbound/outbound PCM is played and discarded.
- **No auto-push of summaries into draft annotations.** Spec §1
  non-goals. The History panel is read-only.
- **No keyboard shortcut for `🎤 Live`.** The existing player has
  shortcuts (Space, J/K/L, etc.); the Live button is mouse-only in v1
  to avoid accidental session starts.
- **No mobile / touch optimization.** Desktop-only operator workflow.
- **No multi-user concurrency.** Single-operator app.

## Definition of done

- **Phases 1–5 (backend):** Tasks 1–15 all checked off, including every
  TDD red→green→commit sub-step.
- **Phase 6 (browser plumbing):** Tasks 16–22 all checked off with the
  one-commit-per-task discipline; Task 17 Step 2 and Task 18 Step 2 are
  the only sub-steps marked `N/A — operator-only`.
- **Phase 7 (templates):** Tasks 23 and 24 all checked off. Visual
  verification deferred to Task 27.
- **Phase 8:**
  - Task 25 Steps 1, 2, 3 done (script written + chmodded + committed).
    The actual `./deploy/enable-gemini-live.sh` execution is `N/A —
    operator-only`.
  - Task 26 done (README updated + committed).
  - Task 27 all sub-steps `N/A — operator-only`.
- `git log --oneline` shows one commit per task, with the exact
  messages from the plan:
  - `feat(settings): add gemini live config fields`
  - `feat(db): add live_sessions table migration 0010`
  - `feat(models): add LiveSession pydantic model`
  - `feat(repo): LiveSessionsRepo CRUD + state transitions + cleanup`
  - `feat(services): czech context block builder for gemini live`
  - `feat(seeds): czech system instruction for gemini live assistant`
  - `feat(seed): seed live.system_instruction.cs prompt at startup`
  - `feat(services): assemble_setup_payload for gemini live wss setup`
  - `feat(services): mint_ephemeral_token via authTokens.create`
  - `feat(services): summarize() — czech post-session summary via generateContent`
  - `feat(routes): GET /api/live/session-config — mint token + assemble setup`
  - `feat(routes): POST /api/live/sessions/{id}/transcript`
  - `feat(routes): POST /api/live/sessions/{id}/summarize`
  - `feat(routes): GET /api/live/sessions list + detail`
  - `feat(startup): reap stale-pending live_sessions on lifespan start`
  - `feat(static): audio worklet — 16kHz int16 pcm capture`
  - `feat(static): liveSession alpine component skeleton`
  - `feat(static): mic capture wired through audio worklet to wss`
  - `feat(static): wss open + setup + transcript wiring + end_session tool`
  - `feat(static): frame capture + auto-send on pause + beforeunload persist`
  - `feat(static): pcm 24khz playback queue for gemini live audio`
  - `feat(static): rolling inactivity timer (default 60s)`
  - `feat(ui): live button + header overlay + transcript strip on clip detail`
  - `feat(ui): live-session history tab + per-session detail expansion`
  - `feat(deploy): gcloud script to enable gemini live + mint api key`
  - `docs(readme): document gemini live setup + env vars`
  - Plus the one-off `chore(deps): add respx for httpx mocking` from
    Spec Gap §1 above.
- `.venv/bin/pytest -q` is green. New test files exist:
  - `tests/integration/test_live_sessions_migration.py`
  - `tests/unit/test_live_session_model.py`
  - `tests/integration/test_live_sessions_repo.py`
  - `tests/unit/test_live_context.py`
  - `tests/integration/test_seed_live_prompt.py`
  - `tests/unit/test_live_sessions_service.py`
  - `tests/integration/test_routes_live.py`
  - `tests/integration/test_live_pending_cleanup_startup.py`
  - `tests/integration/test_routes_live_history_partial.py`
- New source files exist:
  - `backend/migrations/0010_live_sessions.sql`
  - `backend/app/models/live_session.py`
  - `backend/app/repositories/live_sessions.py`
  - `backend/app/services/live_context.py`
  - `backend/app/services/live_sessions.py`
  - `backend/app/routes/live.py`
  - `backend/app/static/audio-worklet-recorder.js`
  - `backend/app/static/liveSession.js`
  - `backend/app/templates/pages/_anno_live_history.html`
  - `backend/seeds/live_system_instruction_cs.json`
  - `deploy/enable-gemini-live.sh` (mode `0755`)
- Modified files include the changes the plan calls out:
  `backend/app/settings.py`, `backend/app/main.py`, `backend/app/seed.py`,
  `backend/app/startup.py`, `backend/app/templates/pages/clip_detail.html`,
  `backend/app/templates/pages/_anno_panels.html`,
  `backend/app/routes/pages.py`, `README.md`, and the CSS file from
  Spec Gap §7.
- `git status` is clean.

## Reporting back

When done, post a single message summarising:

1. The commit hashes you produced (one line each: `<hash>  <subject>`).
2. The output of the final `.venv/bin/pytest -q` run (last 10 lines).
3. The list of operator-only checkboxes left unchecked, all annotated
   `N/A — operator-only`. At minimum:
   - Task 17 Step 2
   - Task 18 Step 2
   - Task 25 (the actual gcloud execution; the script itself is committed)
   - Task 27 (the entire manual checklist)
4. Anything you encountered that the plan didn't anticipate, with the
   resolution you chose. Be specific — "had to adjust signature X
   because Y" is useful; "made some small tweaks" is not. In particular,
   flag if:
   - The `PromptsRepo` method names differed from `get_by_name` /
     `get_production_version` (Spec Gap §2). State the actual names and
     whether you added a wrapper.
   - `view_models._fix` is named or located differently (Spec Gap §3).
   - The clip view-model helper in `routes/pages.py` is named
     differently from `_build_clip_view_model` (Spec Gap §4).
   - You had to adjust a route/repo test fixture pattern (Spec Gap §5).
   - The CSS lives somewhere other than the obvious `static/*.css`
     (Spec Gap §7).
   - Existing tests broke and you had to update them (list each
     assertion you changed and why).
   - You couldn't make a test pass and chose to skip it with `xfail` —
     this should be very rare; explain.

Do not open a PR. Leave the work on the current branch (`main`); the
operator will review the commits, run the operator-only manual checklist
in Task 27, and decide how to integrate.
