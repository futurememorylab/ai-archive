"""FastAPI dependencies — typed accessors for shared app state."""

from __future__ import annotations

from fastapi import Request

from backend.app.context import AppContext


def get_ctx(request: Request) -> AppContext:
    """Return the live AppContext built by the lifespan.

    Type-only helper. Routes used to read `request.app.state.ctx`
    directly, which basedpyright sees as `Any` — making every
    `ctx.db`, `ctx.archive`, etc. an untyped attribute access.
    """
    return request.app.state.ctx  # type: ignore[no-any-return]
