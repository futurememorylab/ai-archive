from backend.app.routes.cache import _cache_row
from backend.app.services.cache_inspector import ClipCacheStatus, LayerStatus


def _layer(layer, *, present, size_bytes=None):
    return LayerStatus(
        layer=layer,
        present=present,
        size_bytes=size_bytes,
        location=None,
        fetched_at=None,
        last_used_at=None,
        pinned_by_workspaces=(),
        evictable=False,
    )


def _status(*, md_present):
    return ClipCacheStatus(
        clip_key=("catdv", "4242"),
        name="some-clip",
        layers=(
            _layer("metadata", present=md_present),
            _layer("media-local", present=True, size_bytes=1000),
            _layer("media-ai", present=False),
        ),
        total_local_bytes=1000,
        total_ai_bytes=0,
    )


def test_cache_row_links_to_clip_detail_when_metadata_present():
    row = _cache_row(_status(md_present=True))
    assert row["row_href"] == "/clips/4242"


def test_cache_row_orphan_is_not_clickable():
    row = _cache_row(_status(md_present=False))
    assert row["row_href"] is None
