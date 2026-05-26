# 0027. Prompt Studio implementation: deviations from the plan

- **Date:** 2026-05-26
- **Status:** Accepted

## Context

The Prompt Studio implementation (ADR 0026, spec
`docs/specs/2026-05-26-prompt-studio-design.md`, plan
`docs/plans/2026-05-26-prompt-studio.md`) was executed task-by-task by
subagents. Several small deviations from the plan's literal text were
necessary either to match existing codebase conventions or to fix
correctness issues in the plan's draft code. This ADR records them so
a future reader of the diff isn't left wondering why the code differs
from the plan.

## Alternatives

For each deviation, the alternative was "follow the plan literally,
diverge from the codebase, and either break existing patterns or leave
a latent bug." None of these were attractive, so the deviations were
taken.

## Decision

(1) **Test placement.** Plan specifies `tests/routes/`, `tests/services/`,
`tests/repositories/`. The codebase uses two layout buckets only:
`tests/unit/` for pure-function / model tests, `tests/integration/`
for everything else (repos, services, routes — all share the same
`db` fixture from `tests/integration/conftest.py`). Studio tests
follow the established convention.

(2) **`db` fixture, not new `conn` fixture.** Plan tests open their own
`aiosqlite.connect` per file. The repo already has a battle-tested
async `db` fixture at `tests/integration/conftest.py:11` that opens
the DB via `open_db()` and runs migrations. Studio tests reuse it;
the local `conn` fixture is dropped.

(3) **`AnnotationOutput.raw: dict[str, Any]` added.** The plan's draft
of `_process_item` writes `Annotation.raw_response={"text": out.raw_text}`,
which is a shape change from the prior `result.get("raw", {})`. Adding
a `raw` field to the shared output dataclass preserves byte-for-byte
behavior of the existing annotator path while keeping the Studio side
of the refactor untouched. The Phase 1 model test was updated to pass
the field; no production reader of `raw_response` knows or cares.

(4) **Resolver chain calls the real interfaces, not the plan's stubs.**
The plan named `ai_store.find_by_clip_key` and `clip_cache_repo.get`;
the real protocol methods are `AIInputStore.status(clip_key)` and
`ClipCacheRepo.get_by_key(conn, *, provider_id, provider_clip_id)`.
The resolver chain in `services/studio_runs.py::resolve_clip_input`
uses the real names. `LocalCacheOnlyResolver` raises `ProxyNotFound`,
which subclasses `FileNotFoundError` — so the plan's `except
FileNotFoundError` catch works as written.

(5) **`StudioRunsService` wired both eagerly (context) and lazily
(route).** The plan only describes eager wiring in
`AppContext.build`, which runs solely when `init_external=True`.
Test paths use `init_external=False` and would not get a service
populated, so the routes file carries a `_build_studio_service`
helper that constructs one on first request when
`ctx.studio_runs_service is None`. The eager path remains the
production hot path; the lazy path is the defensive fallback.

(6) **Early-return cancellation guard in `StudioRunsService.run`.**
The plan's draft of `run` unconditionally calls
`update_status("running")` before checking the per-item cancellation
flag. That overwrites a pre-cancelled status and processes all items
anyway. The implementation checks `run.status == "cancelled"` at the
top of the worker loop and returns immediately; the
per-item-boundary check inside the loop is the second line of
defense.

(7) **Test environment used `pytest_asyncio.fixture`, not `@pytest.fixture`,
for async fixtures.** Pytest-asyncio in this project's pinned version
requires the dedicated decorator for async-yield fixtures. The plan
used `@pytest.fixture` in places where the fixture body was async;
those were adapted to `@pytest_asyncio.fixture`.

(8) **Phase 8 templates use `{% block body %}`, not `{% block content %}`.**
The plan's stub templates extend `pages/layout.html` with a
`{% block content %}` body, but the layout file defines
`{% block body %}`. Stubs use the correct block name; rail-active
highlighting uses the existing `{% block rail_active %}` pattern.

(9) **Phase 10 (manual verification) deferred to the user.** The plan's
final phase exercises the full UI flow with a real Gemini key and
both online/offline CatDV states. The implementation environment
this session ran in is a remote sandbox without browser access or
operator-facing keys; the verification checklist is preserved in
the plan for the user to run locally. ADR 0026 §c–§h decisions all
hold; nothing in the implementation contradicts them.

## Consequences

None of these deviations change the eight ADR 0026 design decisions
(separate Studio tables, nested folders, JSON gold, FK-only prompt
versions, side-by-side compare, CatDV-optional, local uploads,
gold-optional). They are mechanical adaptations to existing codebase
conventions plus two small bug fixes in the plan's draft code. The
test suite is the ground truth: 717 passed, 3 skipped, zero new
failures in any pre-existing test. A future contributor reading the
diff vs. the plan will find each deviation in this ADR with a
one-line "why."
