# 0020. Typed `get_ctx` accessor (PR E of arch plan)

- **Date:** 2026-05-23
- **Status:** Accepted
- **Lifespan:** Invariant

## Context

Every route reached into `request.app.state.ctx` to grab
the live `AppContext`. Starlette types `app.state` as the catch-all
`State` class, so basedpyright saw every `ctx.archive`, `ctx.db`,
`ctx.workspace_manager`, etc. as an `Any` attribute access. The
plan's hypothesis was that the bulk of the 237 baseline errors were
this `Any`-poisoning and that one typed accessor would knock a chunk
of them out.

## Alternatives

- `Annotated[AppContext, Depends(get_ctx)]` parameter on every
  handler. Cleaner, idiomatic FastAPI, but a much larger diff and
  the per-handler signature noise didn't seem worth it for a pure
  type-ergonomics PR — leave for a follow-up if useful.
- Cast at every call site (`cast(AppContext, request.app.state.ctx)`).
  Same error suppression, ~14 type-ignores instead of 1, no central
  place to evolve the pattern.
- Leave it alone and target other categories first. Tempting after
  seeing the result below, but the chokepoint is still worth having
  — the rest of the codebase no longer needs a `# type: ignore` to
  reach the context.

## Decision

A single helper in `backend/app/deps.py`:

```python
def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx  # type: ignore[no-any-return]
```

Routes call `ctx = get_ctx(request)` inline. The lone
`# type: ignore` is contained to one file.

**Why / what we learned.** The baseline did *not* shrink — it grew
from 237 to 273 errors (+36). The reason: most `AppContext` fields
are typed `T | None` because they're only wired during a successful
online boot (`sync_engine`, `workspace_manager`, `connection_monitor`,
`media_prefetcher`, `cache_actions`, etc.). When ctx was `Any`,
`ctx.sync_engine.drain_once()` was silently `Any.Any()`; now it's a
real `reportOptionalMemberAccess`. Breakdown of the +36:
`reportOptionalMemberAccess` 9 → 40 (+31), `reportArgumentType`
74 → 78, plus a handful of misc. All real, all previously hidden.

This is still the right trade. The whole point of the type system is
to make these latent None-derefs visible — and route handlers *do*
need to handle the missing-service case (the `main.py` health endpoint
already uses `getattr(..., None)` exactly for this reason). The
follow-up is to either narrow with explicit `if ctx.sync_engine is
None: raise HTTPException(503, ...)` guards (some routes already do
this) or to split `AppContext` into "always-present core" and
"optional services" types. That's a separate PR; this one's job was
to expose the surface area.
