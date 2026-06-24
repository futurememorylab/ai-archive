"""M6: the kind-filter path fetches the whole catalog up to a fixed cap and
filters in Python. A catalog larger than the cap is silently truncated and the
reported `total` is under-counted. The fix doesn't redesign pagination — it just
makes the bound observable by logging a warning when the fetch hits the cap.

This pins that warning so the truncation can't silently come back.
"""

from datetime import UTC, datetime

import pytest

from backend.app.archive.model import CanonicalClip, ClipPage, MediaRef
from backend.app.routes.pages import clips as clips_mod


def _clip(cid: int, *, image: bool) -> CanonicalClip:
    path = f"clip_{cid}.{'jpg' if image else 'mov'}"
    return CanonicalClip(
        key=("catdv", str(cid)),
        name=path,
        duration_secs=1.0,
        fps=25.0,
        markers=(),
        fields={},
        notes={},
        media=MediaRef(
            mime_type="image/jpeg" if image else "video/quicktime",
            size_bytes=None,
            cached_path=None,
            upstream_handle=path,
        ),
        provider_data={"media": {"filePath": path}},
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


class _FakeArchive:
    def __init__(self, items):
        self._items = list(items)

    async def list_clips(self, catalog_id, query):
        # Mimic CatDV honouring the requested limit: hand back at most `limit`.
        sliced = self._items[query.offset : query.offset + query.limit]
        return ClipPage(
            items=sliced, total=len(self._items), offset=query.offset, limit=query.limit
        )


class _FakeInspector:
    async def status_for_clips(self, keys):
        return []


class _FakeCtx:
    def __init__(self, items):
        self.archive = _FakeArchive(items)
        self.cache_inspector = _FakeInspector()
        self.db = object()


@pytest.mark.asyncio
async def test_kind_filter_logs_warning_when_catalog_hits_cap(monkeypatch, caplog):
    # Shrink the cap so the test doesn't need 5000 clips.
    monkeypatch.setattr(clips_mod, "_KIND_FILTER_FETCH_LIMIT", 4)
    # Exactly cap-many clips come back → the fetch was capped → warn.
    items = [_clip(i, image=(i % 2 == 0)) for i in range(4)]
    ctx = _FakeCtx(items)

    with caplog.at_level("WARNING"):
        summaries, total, _ = await clips_mod.query_clip_page(
            ctx,
            catalog_id="cat",
            q=None,
            offset=0,
            limit=2,
            cache_f="any",
            anno_f="any",
            batch_ids=[],
            host_local_proxies=False,
            kind="image",
        )

    assert any(
        "hit the" in r.message and "cap" in r.message for r in caplog.records
    ), "expected a truncation warning when the kind-filter fetch hits the cap"
    # Sanity: the filter still works (only image clips, page-sliced).
    assert total == 2  # ids 0 and 2 are images among the 4 fetched
    assert len(summaries) <= 2


@pytest.mark.asyncio
async def test_kind_filter_no_warning_below_cap(monkeypatch, caplog):
    monkeypatch.setattr(clips_mod, "_KIND_FILTER_FETCH_LIMIT", 100)
    items = [_clip(i, image=(i % 2 == 0)) for i in range(4)]
    ctx = _FakeCtx(items)

    with caplog.at_level("WARNING"):
        await clips_mod.query_clip_page(
            ctx,
            catalog_id="cat",
            q=None,
            offset=0,
            limit=50,
            cache_f="any",
            anno_f="any",
            batch_ids=[],
            host_local_proxies=False,
            kind="image",
        )

    assert not any("cap" in r.message for r in caplog.records)
