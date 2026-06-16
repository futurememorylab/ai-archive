"""A single un-hydratable clip in a filter's candidate set must not 502 the
whole filtered clips list. CatDV-offline (or synthetic/manual review_items
whose clip id has no cached metadata and no upstream record) used to make the
``none`` / ``for_review`` filters raise ProviderError, which the route mapped
to 502 — and HTMX silently keeps the previous filter's rows, so the filter
looked broken ("annotated and not-annotated return the same rows").

The clips list is a cache-first, offline-safe view: a clip that can't be
hydrated right now is skipped (recoverable on refresh) and logged, never an
abort. See ADR 0087.
"""

from datetime import UTC, datetime

import pytest

from backend.app.archive.errors import ProviderError
from backend.app.archive.model import CanonicalClip, MediaRef
from backend.app.routes.pages import clips as clips_mod


def _clip(cid: int) -> CanonicalClip:
    return CanonicalClip(
        key=("catdv", str(cid)),
        name=f"Clip {cid}",
        duration_secs=1.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle="x",
        ),
        provider_data={},
        fetched_at=datetime.now(UTC),
    )


class _FakeClipCacheRepo:
    async def get_by_key(self, db, *, provider_id, provider_clip_id):
        return None  # force the live-hydrate path for every candidate


class _FakeListCacheRepo:
    async def clips_for_catalog(self, db, *, provider_id, catalog_id):
        return {}  # nothing pre-hydrated; each candidate hits the archive


class _FakeArchive:
    """get_clip succeeds for the good id, raises an offline error for the bad
    one (the synthetic 1000000001-style review_items clip)."""

    def __init__(self, bad_id: int):
        self._bad = bad_id

    async def get_clip(self, clip_id: str):
        if int(clip_id) == self._bad:
            raise ProviderError(f"clip {clip_id} not available offline")
        return _clip(int(clip_id))


class _Ctx:
    def __init__(self, bad_id: int):
        self.db = object()
        self.clip_cache_repo = _FakeClipCacheRepo()
        self.clip_list_cache_repo = _FakeListCacheRepo()
        self.archive = _FakeArchive(bad_id)


@pytest.mark.asyncio
async def test_filtered_page_skips_unhydratable_clip_instead_of_raising(monkeypatch):
    good_id, bad_id = 200, 1000000001

    async def fake_resolve(*args, **kwargs):
        return {good_id, bad_id}

    monkeypatch.setattr(clips_mod, "resolve_filters", fake_resolve)

    clips, total = await clips_mod._filtered_page(
        _Ctx(bad_id),
        catalog_id="0",
        q=None,
        offset=0,
        limit=50,
        cache_filter="any",
        anno_filter="none",
    )

    # The offline clip is skipped, the good one survives — no 502.
    assert total == 1
    assert [c.key for c in clips] == [("catdv", str(good_id))]
