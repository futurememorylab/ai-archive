# AGENTS.md — ai-archive

Shared entry point for coding agents (Claude Code reads it through
`CLAUDE.md`; opencode and others read it directly). Keep it short: rules a
guard test already enforces live in the test, and the architecture lives in
[`docs/architecture-invariants.md`](docs/architecture-invariants.md), which
is the canon. If this file and that page disagree, the invariants page wins.

Machine-specific notes (local paths, Python/node quirks, VPN) go in a
gitignored `CLAUDE.local.md`; see `docs/onboarding/CLAUDE.local.example.md`.

## What this is

Local-first FastAPI app for annotating archival footage with Gemini and
writing results back to the archive (CatDV REST or a filesystem sidecar).
Server-rendered: Jinja2 + HTMX + Alpine.js, SQLite, no JS build step.
Start with `docs/onboarding/README.md` and `docs/ARCHITECTURE.md`.

## Commands

```bash
./run.sh                                         # dev server on :8765 (creates .venv)
.venv/bin/python -m pytest tests/unit/test_x.py -q   # in-loop: one file or dir
.venv/bin/python -m pytest -n auto -q            # full suite, once before committing
ruff check . && ruff format --check .            # lint + format (CI-gated)
lint-imports                                     # layer contracts (CI-gated)
python tools/erosion_gate.py --path backend --baseline .erosion-baseline.json --max-cc 30 --base-ref origin/main
pre-commit install                               # run the same gates on every commit
```

CI (`.github/workflows/ci.yml`) runs lint, format, import contracts, the
erosion gate, docstring and duplicate-code checks and the full test suite
on every PR. Make them pass locally first. The baselines only go down: a
function you change may not get more complex, and when your PR lowers
erosion or max CC the gate asks you to `--write-baseline` and commit the
lower values. Raising a baseline is Peter's call (ADR 0129).

Claude Code sessions run two hooks from `.claude/hooks/`: edits to any
file in `.github/CODEOWNERS` are blocked (stop and ask Peter), and a
session can't finish while ruff, import contracts, the erosion gate or
the matching tests are red for the Python it changed.

## CatDV license seat (the one rule that bites)

CatDV allows 2 sessions and the human web client usually holds one, so
assume **one seat** for this app. A leaked `JSESSIONID` locks everyone out
until it times out server-side.

- Start and stop the dev server with the `server-start` / `server-stop`
  skills. They check for an existing instance and shut down with `SIGTERM`
  (never `kill -9`, which skips the logout).
- A script that `POST`s `/session` itself must `DELETE /catdv/api/9/session`
  when done.
- A 502 "session limit reached (Maximum:2)" means a seat is stuck. Don't
  retry; find and `kill -TERM` your own leftover process or wait it out.

Detail: `docs/onboarding/05-catdv-license-discipline.md`.

## Before you write code

- **Reuse before building.** UI primitives (buttons, fields, menus,
  modals, headers, pills, formatters) come from
  `templates/components/_ui.html`, `static/format.js` and the `:root`
  tokens in `static/app.css` — read `docs/design-language.md`. Scenes /
  markers / fields render through `_anno_panels.html`; video goes through
  `Alpine.data("player")` + `_player_overlay.html`; clip cards through
  `_video_list.html`. A genuinely new component needs a sentence in the
  spec saying why the existing one couldn't be extended.
- **Clip media goes through the cache layers**, never straight to CatDV or
  GCS: `ctx.proxy_resolver.path_for_clip_id`, `ctx.ai_store.status` then
  `ensure_uploaded`, `ctx.thumbnail_service.get_or_fetch`. Every miss must
  degrade gracefully and name the layer that missed. Canonical example:
  `services/clip_media.py::ai_input_for_clip`. No eager fetching on page render.
- **Absence needs evidence.** Decide "clip is gone" only via
  `archive/errors.py::is_provider_not_found(exc)`; anything else is
  transient. Catch `Exception`, not `BaseException`.
- **User-facing error text** goes through `services/errors.py::humanise`,
  and in the browser through `Alpine.store('toast').push(...)`. No
  `alert()`, no silent `.catch()`, no `location.reload()` after CRUD
  (return an HTMX partial instead).
- **Enumerations** live in `backend/app/enums/registry.py`; never hardcode
  options in a template, `<select>` or JS array.
- **List-of-keys repository reads** use `repositories/_batch.py::chunked_in_clause`
  and get a query-count test (`tests/_helpers/query_count.py`).
- **Gemini/Vertex model names**: confirm the model exists in the target
  region (`europe-west3`) before adding it.

The guard tests in `tests/unit/test_*guard*.py`, `test_no_*.py`,
`test_*single*.py`, `test_templates_shared.py` and
`test_context_delegation.py`, plus `.importlinter`, fail CI when one of the
removed patterns (god-context, `_x_dataStack`, a second Jinja env, sync fs
I/O in `async def`, hand-rolled UI primitives, routes importing `httpx`)
comes back. When one fails, read its docstring; it explains the rule.

## Testing

- Test first for bug fixes and features: a failing test, then the fix.
- Run the slice you're touching while iterating; run `-n auto` once
  before committing.
- UI changes (a page template, an Alpine component, a page route, a user
  flow) update the matching Playwright walkthrough in `tests/walkthrough/`
  in the same PR and pass `/e2e` in assert mode. Walkthroughs run offline
  on their own port and never touch the CatDV seat. Reference:
  `tests/walkthrough/README.md`.

## Docs and decisions

- Specs go in `docs/specs/` and end with a **Manual acceptance flows**
  section: numbered click-throughs a colleague can follow on a running
  app. Example: `docs/specs/2026-05-26-prompt-studio-pr2-design.md`.
- Write an ADR (`docs/adr/`, indexed in `docs/decisions.md`) only when a
  decision sets or changes an invariant, is expensive to reverse, or
  binds future PRs. Most PRs don't need one. An invariant change also
  updates `docs/architecture-invariants.md`.

## Git

- Work on a short-lived branch and open a PR; never push to `main`.
- Check the branch hasn't diverged from its remote before pushing.
- The one-time reformat commit is listed in `.git-blame-ignore-revs`; run
  `git config blame.ignoreRevsFile .git-blame-ignore-revs` once.
