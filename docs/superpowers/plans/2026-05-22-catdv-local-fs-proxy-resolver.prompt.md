# Remote subagent prompt — implement CatDV local-FS proxy resolver

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
docs/superpowers/plans/2026-05-22-catdv-local-fs-proxy-resolver.md
```

This adds a `MediaStoreMap` value object + rewrites `FilesystemProxyResolver`
so that when the app is deployed on the same host as the CatDV server, it
resolves each clip's web proxy as an on-disk path (`/Volumes/ARECA/CatDV_Proxy/...`)
instead of downloading the proxy over HTTP. No new env vars; reuses the
existing `PROXY_SOURCE=filesystem`.

Context for why this is being done lives in `docs/decisions.md` under the
`2026-05-22 — Local-filesystem proxy resolution` heading. Read it before
Task 1 — it explains the pairing rule, the `target: "web"` filter, and the
operationally-loud failure policy. Do **not** re-derive these decisions;
they're locked.

## Execution rules

- **One task at a time, in order, with checkpoints.** Use the
  `superpowers:executing-plans` skill to drive the loop. Treat each Task in
  the plan as one batch: write the failing test(s), confirm they fail, write
  the code, confirm they pass, commit.
- **TDD is required** — the plan is structured around it. If a step says
  "write the failing test", run the test and confirm the failure mode before
  writing implementation code. Don't skip ahead.
- **Frequent commits.** Each task ends with a commit step using the message
  shown in the plan. Don't squash tasks together.
- **Don't expand scope.** The plan is the spec. If a step looks redundant or
  over-specified, complete it as written. If something is genuinely
  unbuildable as specified, stop and surface the contradiction — don't
  improvise.
- **No new dependencies.** Everything builds on existing modules
  (`backend.app.archive`, `backend.app.services.catdv_client`,
  `backend.app.services.proxy_resolver`, pydantic-settings, pytest-asyncio).
  If you find yourself reaching for a new library, you're off-track.

## Verification

- Use the venv: `.venv/bin/pytest` and `.venv/bin/python` (never system
  `python3` — see `CLAUDE.md` global rules).
- After each task: run that task's specific tests with `-v` and confirm the
  expected pass/fail at each step.
- After Task 4: run the full unit + targeted integration test set:
  ```
  .venv/bin/pytest tests/unit -q
  .venv/bin/pytest tests/integration/test_proxy_resolver_fs.py tests/integration/test_proxy_resolver_rest.py -q
  ```
  Both must be green. If `tests/integration/test_proxy_resolver_rest.py`
  fails because of an unrelated VPN/CatDV dependency, note it but do not
  attempt to fix — that's out of scope.
- The Task 4 "manual smoke test" step requires the CatDV host's filesystem,
  which you don't have. **Skip it** and mark the checkbox as N/A in your
  final report — the test suite already exercises the resolver end-to-end
  with fakes. Do not start the dev server.
- **Do not start a uvicorn instance.** A dev server is already running on
  this developer's machine on port 8765 with an active CatDV session
  (1 of 2 license seats). Starting a second uvicorn instance would either
  collide on the port or burn the remaining seat. The plan's runtime
  verification is the operator's job, not yours.

## CatDV / session safety

You will not call the CatDV REST API directly. All of your work is in
Python source + tests. If you find yourself about to `curl` the CatDV
server or run code that opens a `CatdvClient`, stop — that's not in scope
and risks the 2-seat license limit.

## Definition of done

- Tasks 1–6 in the plan are all checked off (Task 6 covers the cache-state
  UI invariant — synthetic media-local layer, dead controls hidden — in
  `PROXY_SOURCE=filesystem` mode).
- `git log --oneline` shows one commit per task, with the exact messages from
  the plan (`feat(proxy): …`, `refactor(settings): …`, `feat(context): …`,
  `docs(deploy): …`, `feat(cache): host-local mode — …`).
- `grep -rn "proxy_fs_root\|proxy_path_template" backend/ tests/ .env.example`
  returns zero hits — Task 4 self-review item #4.
- `.venv/bin/pytest tests/unit -q` is green.
- `.venv/bin/pytest tests/integration/test_proxy_resolver_fs.py -q` is green.
- `git status` is clean.

## Reporting back

When done, post a single message summarising:

1. The commit hashes you produced (one line each: `<hash>  <subject>`).
2. The output of the final `pytest tests/unit -q` run (last 5 lines).
3. Anything you encountered that the plan didn't anticipate, with the
   resolution you chose. Be specific — "had to adjust signature X because Y"
   is useful; "made some small tweaks" is not.
4. A list of any checkboxes you left unchecked and why (e.g. "Task 4 Step 5:
   manual smoke skipped per prompt instructions — no CatDV host filesystem
   available in this environment").

Do not open a PR. Leave the work on the current branch; the operator will
review the commits and decide how to integrate.
