"""Graceful-shutdown trigger seam.

Isolated in its own module so the shutdown route can fire it and tests can
replace it without signalling the pytest process. The real implementation
sends SIGTERM to our own process — uvicorn's documented graceful-shutdown
trigger, the same path as `kill -TERM` / Ctrl-C. uvicorn then runs the
FastAPI lifespan shutdown, which releases the CatDV seat in
`AppContext.aclose()`.
"""

from __future__ import annotations

import asyncio
import os
import signal


def request_graceful_shutdown() -> None:
    """Send SIGTERM to our own process to start uvicorn's graceful shutdown."""
    os.kill(os.getpid(), signal.SIGTERM)


def schedule_graceful_shutdown(delay_s: float = 0.5) -> None:
    """Defer the SIGTERM by `delay_s` so the HTTP response flushes first.

    Tests generally swap the whole function at the route's import site; the
    unit test instead swaps `request_graceful_shutdown` *before* calling this,
    so the reference captured below is the test's stub.
    """
    loop = asyncio.get_running_loop()
    loop.call_later(delay_s, request_graceful_shutdown)
