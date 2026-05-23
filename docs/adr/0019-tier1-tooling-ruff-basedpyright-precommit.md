# 0019. Tier 1 tooling: ruff format, basedpyright with baseline, pre-commit

- **Date:** 2026-05-23
- **Status:** Accepted

## Context

A senior reviewer flagged the backend as feeling chaotic
despite real layering (`archive/` → `repositories/` → `services/` → `routes/`).
The signals were 89 outstanding ruff errors, no type checker, no
pre-commit hook, scattered `# noqa: E402` workarounds in `main.py`,
and no enforcement that the layer rules are honored. Goal: make the
codebase orientable for a new human developer and stop the bleeding
at the keyboard, not in review.

## Alternatives

1. *Type checker.* mypy (`--strict`) vs basedpyright vs pyright.
   mypy is the default but slow on this 11.6k-line tree, and slow
   pre-commit hooks get skipped. basedpyright is a maintained fork
   of pyright that's pip-installable (no node), runs in seconds,
   and supports a `--baseline` file out of the box.
2. *Type-checker rollout strategy.* Either drive the codebase to
   zero errors today, or capture a baseline and ratchet later. The
   first option requires touching ~50 files, much of it in tests
   that pre-date this initiative. The second option lets us turn
   the hook on now without blocking commits on legacy noise.
3. *ASYNC240 lint rule.* Either fix every blocking `Path.stat()`
   / `Path.exists()` call with `asyncio.to_thread`, or stop linting
   for them. We use neither anyio nor trio (the rule's premise),
   and the calls in question are sub-ms local-SSD stats — wrapping
   them in `to_thread` costs more than it saves and adds visual
   clutter to every cache/proxy resolver. The one genuinely
   blocking call (writing a multi-hundred-MB proxy stream in
   `catdv_client._stream_to_file`) is fixed properly with
   `to_thread` per chunk.

## Decision

- `ruff format` enabled (replaces black + isort; ruff was already in
  the dev deps with sensible lint config).
- `basedpyright` added with `typeCheckingMode = "basic"` and a
  baseline at `.basedpyright/baseline.json` (237 pre-existing errors
  snapshotted; `0 errors` now means "no new ones"). Baseline refresh
  is a one-line command documented inline in `.pre-commit-config.yaml`.
- `ASYNC240` disabled globally in `pyproject.toml` with an inline
  comment explaining why. The single legitimate blocking-I/O case
  in `_stream_to_file` is fixed with `asyncio.to_thread` per chunk
  and carries a `# noqa: ASYNC230` plus a code comment.
- `B017` (`pytest.raises(Exception)`) added to per-file ignores for
  `tests/` — that pattern is intentional when the contract under
  test only promises "raises *something*."
- `pre-commit` wires ruff check (with `--fix`), ruff format, and
  basedpyright. Hook versions pinned to match the project's local
  ruff (`v0.15.13`) — version drift between local and pre-commit
  ruff caused `Unknown rule selector` errors when the hook used an
  older release.
- `main.py` rewritten: all router imports moved to the top, router
  registration extracted into `register_routers(app)`. Removes the
  nine `# noqa: E402` workarounds the old shape required, and
  gives a new contributor one place to see the full route surface.

## Consequences

The architecture was fine; the chaos was the *absence of
enforcement*. Once pre-commit refuses to land unformatted code or
new type errors, the layers we already have stay legible. Picking
basedpyright + a baseline file specifically solves the "237 latent
errors block every commit" trap that kills mypy adoption on
existing codebases. Disabling `ASYNC240` is a deliberate scope cut:
the rule was selected aspirationally but the codebase never
followed it; making the rule's intent visible at the disable site
is better than littering the repo with `# noqa`. The remaining
Tier 2/3 items (import-linter for layer enforcement, `CONTEXT.md`
glossary, `ARCHITECTURE.md` map, splitting the 663-line
`routes/pages.py`) are intentionally out of scope here — those are
orientation work, this is hygiene work.
