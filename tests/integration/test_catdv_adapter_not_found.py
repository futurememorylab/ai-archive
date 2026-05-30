"""CatDV adapter raises NotFoundError on documented 'not found' responses.

NotFoundError is the only exception type CacheInspector / WorkspaceManager
should treat as evidence a clip is absent. CatdvError generally means 'the
server said no' for many reasons; only the NOT_FOUND subset deserves the
narrower type."""

import pytest

from backend.app.archive.errors import NotFoundError
from backend.app.archive.providers.catdv.adapter import CatdvArchiveAdapter
from backend.app.services.catdv_client import CatdvClient
from tests.fakes.fake_catdv import running_fake_catdv


@pytest.mark.asyncio
async def test_get_clip_raises_not_found_for_missing_clip():
    with running_fake_catdv() as (base_url, fake):
        # Do NOT add clip 999 to fake.clips; fake_catdv returns "Not found" envelope.
        async with CatdvClient(base_url=base_url, username="klientAI", password="secret") as client:
            adapter = CatdvArchiveAdapter(client=client)
            with pytest.raises(NotFoundError):
                await adapter.get_clip("999")
