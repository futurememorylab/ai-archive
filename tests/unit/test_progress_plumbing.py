"""Issue #78: ensure_cached -> resolver -> download forwards progress_cb."""

import pytest

from backend.app.services.media_cache import LocalProxyBackend


class _RecordingResolver:
    is_host_local = False

    def __init__(self):
        self.seen_cb = "unset"

    async def path_for_clip_id(self, clip_id, progress_cb=None):
        self.seen_cb = progress_cb
        return None  # path unused by this test

    def is_managed(self, path):
        return False


@pytest.mark.asyncio
async def test_local_backend_forwards_progress_cb_to_resolver():
    resolver = _RecordingResolver()
    backend = LocalProxyBackend(resolver=resolver, ai_store=None, gcs=None)

    async def cb(d, t):
        pass

    await backend.ensure_cached(7, progress_cb=cb)
    assert resolver.seen_cb is cb


@pytest.mark.asyncio
async def test_local_backend_default_cb_is_none():
    resolver = _RecordingResolver()
    backend = LocalProxyBackend(resolver=resolver, ai_store=None, gcs=None)
    await backend.ensure_cached(7)
    assert resolver.seen_cb is None
