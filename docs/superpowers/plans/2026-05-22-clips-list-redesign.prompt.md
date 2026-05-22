# Remote subagent prompt — implement Clips List Redesign

> Paste the block below into a fresh Claude Code session running on a machine
> with this repo checked out. The agent has no memory of the conversation that
> wrote the plan — everything it needs is in the prompt.

---

You're picking up a written implementation plan. Everything you need to do
the work is in the repo at HEAD on `main` — read it, don't guess.

## Repo

`catdv-annotator` (Python 3.14, FastAPI). You are already in the repo root.
Confirm with `git rev-parse --show-toplevel` and `cat CLAUDE.md` to load
project conventions (CatDV session discipline, decisions.md format, venv
usage).

## Your task

Execute the plan at:

```
docs/superpowers/plans/2026-05-22-clips-list-redesign.md
```

This replaces the dense table-style clips list (`/`) with a media-row
layout that shows each clip's CatDV poster image and a 2-line notes excerpt
(expandable inline), defaults the page size to 20, and adds a disk-cached
`/api/poster/{clip_id}` route. The CatDV list endpoint already returns
`posterID`, `notes`, and `bigNotes`, so no new CatDV calls are added.

The spec lives at `docs/specs/2026-05-22-clips-list-redesign-design.md` —
read it before Task 1. It explains why a single poster per row (not a
thumbnail scrub strip), why `notes_excerpt` is sent un-truncated to the
client, and why the browser cache key uses `?v={poster_id}` rather than a
path segment. Do **not** re-derive those decisions; they're locked.

## Execution rules

- **One task at a time, in order, with checkpoints.** Use the
  `superpowers:executing-plans` skill to drive the loop. Treat each Task in
  the plan as one batch: write the failing test(s), confirm they fail, write
  the code, confirm they pass, commit.
- **TDD is required** — the plan is structured around it. If a step says
  "write the failing test", run the test and confirm the failure mode before
  writing implementation code. Don't skip ahead.
- **Frequent commits.** Each task ends with a commit step using the exact
  message shown in the plan. Don't squash tasks together.
- **Don't expand scope.** The plan is the spec. If a step looks redundant or
  over-specified, complete it as written. If something is genuinely
  unbuildable as specified, stop and surface the contradiction — don't
  improvise.
- **No new dependencies.** Everything builds on existing modules
  (FastAPI, Jinja2, Alpine.js already vendored, htmx already vendored, httpx,
  pytest, pytest-asyncio). If you find yourself reaching for a new library,
  you're off-track.

## Verification

- Use the venv: `.venv/bin/pytest` and `.venv/bin/python` (never system
  `python3` — see `CLAUDE.md` global rules).
- After each task: run that task's specific tests with `-v` and confirm the
  expected pass/fail at each step.
- After Task 7: run the full unit + targeted integration test set:
  ```
  .venv/bin/pytest tests/unit -q
  .venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_posters_route.py -q
  ```
  Both must be green. If unrelated VPN-dependent integration tests fail
  (e.g. ones that talk to the live CatDV), note them but do not attempt to
  fix — that's out of scope.

## Task 8 is operator-only — SKIP it

Task 8 in the plan is a manual end-to-end verification that requires:

- The Pragafilm WireGuard VPN being up on the operator's machine.
- The CatDV server at `192.168.1.41:8080` being reachable and powered on.
- A browser to inspect the actual rendered page.
- The CatDV license seat (which the operator may already be holding).

You have **none** of these. Do not attempt Task 8. Specifically:

- **Do not** `ping`, `curl`, or otherwise probe `192.168.1.41`.
- **Do not** start a `uvicorn` / `./run.sh` instance. Even locally without
  a VPN this would risk port conflicts or hit a different CatDV instance
  if the operator's environment is unusual.
- **Do not** write the `docs/decisions.md` entry described in Task 8 Step 8
  — leave that for the operator after their manual verification.

Mark every Task 8 checkbox as `N/A — operator-only` in your final report.
The unit + integration tests in Tasks 1–7 fully exercise the new code with
fakes; Task 8 is a runtime smoke that has to happen on the operator's
machine.

## CatDV / session safety

You will not call the CatDV REST API directly. All of your work is in
Python source + tests + Jinja templates + CSS. If you find yourself about
to `curl` the CatDV server, open a `CatdvClient` against a real base URL,
or import the `running_fake_catdv` integration harness, stop — none of that
is in scope and the live CatDV has a 2-seat license limit that's easy to
exhaust.

Your tests use `httpx.MockTransport` (Task 2) and Python-only fakes
(Tasks 3 + 4). The `tests/integration/test_routes_pages.py` and
`tests/integration/test_posters_route.py` tests use FastAPI's `TestClient`
with `FakeArchive` / `_FakeCatdvClient`, never the network.

## Definition of done

- Tasks 1–7 in the plan are all checked off. Task 8 left unchecked with
  `N/A — operator-only` notes on every step.
- `git log --oneline` shows one commit per task, with the exact messages
  from the plan:
  - `feat(view-models): clip_summary carries poster_id and notes excerpt`
  - `feat(catdv-client): download_poster with 401 re-auth`
  - `feat(poster-cache): disk-cached poster store with per-clip lock`
  - `feat(posters): /api/poster/{clip_id} with disk cache and immutable headers`
  - `feat(clips-list): default page size 20`
  - `style(clips): media-row layout CSS and film-strip fallback`
  - `feat(clips-ui): media-row layout with poster, notes excerpt, expand`
- `.venv/bin/pytest tests/unit -q` is green.
- `.venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_posters_route.py -q` is green.
- `git status` is clean.
- Files created exist: `backend/app/services/poster_cache.py`,
  `backend/app/routes/posters.py`, `backend/app/static/film-strip.svg`,
  `tests/unit/test_catdv_client_poster.py`, `tests/unit/test_poster_cache.py`,
  `tests/integration/test_posters_route.py`.

## Reporting back

When done, post a single message summarising:

1. The commit hashes you produced (one line each: `<hash>  <subject>`).
2. The output of the final `.venv/bin/pytest tests/unit -q` run (last 5 lines).
3. The output of the final `.venv/bin/pytest tests/integration/test_routes_pages.py tests/integration/test_posters_route.py -q` run (last 5 lines).
4. Anything you encountered that the plan didn't anticipate, with the
   resolution you chose. Be specific — "had to adjust signature X because
   Y" is useful; "made some small tweaks" is not. In particular, flag if:
   - The `CatdvClient.__init__` did not have a `transport=` kwarg and you
     added one (Task 2 Step 3).
   - The `AppContext` shape needed a different attribute name than
     `poster_cache` (Task 4 Step 1 + Step 5).
   - Any CSS variable referenced in the new rules (`--panel-2`, `--text-2`,
     `--accent`) did not exist and you fell back to a literal or to an
     existing token.
5. The list of Task 8 checkboxes left unchecked, all annotated
   `N/A — operator-only`.

Do not open a PR. Leave the work on the current branch (`main`); the
operator will review the commits and decide how to integrate.
